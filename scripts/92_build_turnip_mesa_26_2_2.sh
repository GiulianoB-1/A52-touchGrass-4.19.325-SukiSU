#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${1:-$PWD}"
NDK="${2:-${ANDROID_NDK_ROOT:-}}"
OUT="${3:-$ROOT/artifacts/turnip-mesa-26.2.2}"

MESA_VERSION="26.2.2"
MESA_SHA256="eeb29ca7e56cfaa8e8a79538dcf834e3b18e501c31bef5145e959ea437cc4216"
MESA_URL="https://archive.mesa3d.org/mesa-${MESA_VERSION}.tar.xz"
ANDROID_API="36"

if [ -z "$NDK" ] || [ ! -d "$NDK/toolchains/llvm/prebuilt/linux-x86_64" ]; then
  echo "invalid Android NDK path: $NDK" >&2
  exit 2
fi

mkdir -p "$OUT"
WORK="$OUT/work"
SRC="$WORK/mesa-${MESA_VERSION}"
BUILD="$WORK/build-aarch64"
DIST="$OUT/dist"
rm -rf "$WORK" "$DIST"
mkdir -p "$WORK" "$DIST"

echo "==> Fetch Mesa ${MESA_VERSION}"
curl -fL --retry 5 --retry-delay 3 "$MESA_URL" -o "$WORK/mesa.tar.xz"
echo "$MESA_SHA256  $WORK/mesa.tar.xz" | sha256sum -c -
tar -C "$WORK" -xf "$WORK/mesa.tar.xz"

echo "==> Verify A619 + KGSL source support"
grep -Fq 'GPUId(619)' "$SRC/src/freedreno/common/freedreno_devices.py"
grep -Fq "freedreno_kmds.contains('kgsl')" "$SRC/src/freedreno/vulkan/meson.build"
grep -Fq "libtu_files += files('tu_knl_kgsl.cc')" "$SRC/src/freedreno/vulkan/meson.build"
grep -Fq 'PUBLIC struct hwvulkan_module_t HAL_MODULE_INFO_SYM' "$SRC/src/vulkan/runtime/vk_android.c"

echo "==> Backport Android YV12 explicit-layout support for Mesa 26.2.2"
python3 - "$SRC" <<'PY'
from pathlib import Path
import sys

src = Path(sys.argv[1])

def replace_once(path, old, new, label):
    p = src / path
    text = p.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 anchor, found {count}")
    p.write_text(text.replace(old, new, 1))

# Backport the FDL part of Turnip-Enhanced commit
# aeaf924c56adf7eddb0a9033b33474b48367e33d onto Mesa 26.2.2.
# The source commit is based on a newer Mesa tree, so applying the full patch
# directly is intentionally avoided.  These changes are limited to exact
# imported level-0 pitch preservation and the narrowly validated Android YV12
# AHB shape that our SurfaceFlinger probe reproduces.

replace_once(
    "src/freedreno/fdl/freedreno_layout.h",
    """struct fdl_explicit_layout {
   uint32_t offset;
   uint32_t pitch;
};""",
    """struct fdl_explicit_layout {
   uint32_t offset;
   uint32_t pitch;

   /* Optional validation alignment for an imported single-level linear
    * image. Zero keeps the ordinary hardware-layout requirement.
    */
   uint32_t pitch_alignment;

   /* Imported sampled-only Android images do not need Turnip's private
    * four-row tail padding. This is only accepted for a single-level,
    * single-layer, linear, non-UBWC image.
    */
   bool skip_last_level_padding;
};""",
    "fdl_explicit_layout fields",
)

replace_once(
    "src/freedreno/fdl/freedreno_layout.h",
    """   bool ubwc : 1;
   bool layer_first : 1; /* see above description */
   bool tile_all : 1;
   bool is_mutable : 1;
""",
    """   bool ubwc : 1;
   bool layer_first : 1; /* see above description */
   bool tile_all : 1;
   bool is_mutable : 1;
   bool has_explicit_pitch : 1;
""",
    "fdl_layout explicit pitch flag",
)

replace_once(
    "src/freedreno/fdl/freedreno_layout.h",
    """static inline uint32_t
fdl_pitch(const struct fdl_layout *layout, unsigned level)
{
   return align(u_minify(layout->pitch0, level), 1 << layout->pitchalign);
}""",
    """static inline uint32_t
fdl_pitch(const struct fdl_layout *layout, unsigned level)
{
   /* An imported level-0 row pitch is authoritative. pitchalign remains the
    * minimum derived-mip pitch encoded in the descriptor.
    */
   if (level == 0 && layout->has_explicit_pitch)
      return layout->pitch0;

   return align(u_minify(layout->pitch0, level), 1 << layout->pitchalign);
}""",
    "fdl_pitch explicit level0",
)

replace_once(
    "src/freedreno/fdl/fd6_layout.c",
    """   if (explicit_layout) {
      offset = explicit_layout->offset;
      layout->pitch0 = explicit_layout->pitch;
      if (align(layout->pitch0, 1 << layout->pitchalign) != layout->pitch0)
         return false;
   }""",
    """   if (explicit_layout) {
      offset = explicit_layout->offset;
      layout->pitch0 = explicit_layout->pitch;
      layout->has_explicit_pitch = true;

      if (explicit_layout->skip_last_level_padding &&
          (layout->tile_mode != TILE6_LINEAR || layout->ubwc ||
           params->mip_levels != 1 || params->array_size != 1 ||
           params->depth0 != 1 || params->is_3d))
         return false;

      uint32_t pitch_alignment = 1u << layout->pitchalign;
      if (explicit_layout->pitch_alignment) {
         if (layout->tile_mode != TILE6_LINEAR || params->mip_levels != 1 ||
             !util_is_power_of_two_nonzero(explicit_layout->pitch_alignment) ||
             explicit_layout->pitch_alignment < layout->cpp)
            return false;

         pitch_alignment = explicit_layout->pitch_alignment;
      }

      if (align(layout->pitch0, pitch_alignment) != layout->pitch0)
         return false;
   }""",
    "fdl explicit pitch validation",
)

replace_once(
    "src/freedreno/fdl/fd6_layout.c",
    """      if (level == params->mip_levels - 1)
         nblocksy = align(nblocksy, 4);""",
    """      if (level == params->mip_levels - 1 &&
          !(explicit_layout && explicit_layout->skip_last_level_padding))
         nblocksy = align(nblocksy, 4);""",
    "fdl imported tail padding",
)

# Samsung/QCOM's legacy GRALLOC_MODULE_PERFORM_GET_YUV_PLANE_INFO can
# return android_ycbcr pointers as offsets when the buffer is unmapped, but
# as absolute mapped virtual addresses after a CPU lock/unlock. Mesa 26.2.2
# assumes the former unconditionally and truncates those addresses into the
# int offset fields, which makes a previously CPU-touched YV12 import fail.
# Normalize only the exact planar YV12 case, using the Y pointer as the base.
replace_once(
    "src/util/u_gralloc/u_gralloc_internal.c",
    """#include <hardware/gralloc.h>
#include <errno.h>
""",
    """#include <hardware/gralloc.h>
#include <errno.h>
#include <limits.h>
#include <stdint.h>
""",
    "u_gralloc mapped YV12 includes",
)

replace_once(
    "src/util/u_gralloc/u_gralloc_internal.c",
    """   enum chroma_order chroma_order =
      ((size_t)ycbcr->cr < (size_t)ycbcr->cb) ? YCrCb : YCbCr;

   /* .chroma_step is the byte distance between the same chroma channel
""",
    """   uintptr_t y_ptr = (uintptr_t)ycbcr->y;
   uintptr_t cb_ptr = (uintptr_t)ycbcr->cb;
   uintptr_t cr_ptr = (uintptr_t)ycbcr->cr;

   enum chroma_order chroma_order =
      (cr_ptr < cb_ptr) ? YCrCb : YCbCr;

   /* The legacy QCOM perform API may return offsets from a null base while
    * the allocation is unmapped, then return real process virtual addresses
    * after AHardwareBuffer_lockPlanes()/unlock().  This is reproducible on
    * the A52 YV12 path.  Preserve the ordinary offset mode, but for the exact
    * 3-plane YV12 shape normalize a mapped address triplet back to offsets.
    */
   if (hnd->hal_format == HAL_PIXEL_FORMAT_YV12 &&
       ycbcr->chroma_step == 1 &&
       y_ptr > INT_MAX && cb_ptr >= y_ptr && cr_ptr >= y_ptr) {
      cb_ptr -= y_ptr;
      cr_ptr -= y_ptr;
      y_ptr = 0;

      if (cb_ptr > INT_MAX || cr_ptr > INT_MAX) {
         mesa_logw("YV12 mapped plane offsets exceed Mesa import range");
         return -EINVAL;
      }

      mesa_logi("touchGrass: normalized mapped QCOM YV12 android_ycbcr pointers");
   }

   /* .chroma_step is the byte distance between the same chroma channel
""",
    "u_gralloc mapped YV12 normalize",
)

replace_once(
    "src/util/u_gralloc/u_gralloc_internal.c",
    """   out->offsets[0] = (size_t)ycbcr->y;
   /* We assume here that all the planes are located in one DMA-buf. */
   if (chroma_order == YCrCb) {
      out->offsets[1] = (size_t)ycbcr->cr;
      out->offsets[2] = (size_t)ycbcr->cb;
   } else {
      out->offsets[1] = (size_t)ycbcr->cb;
      out->offsets[2] = (size_t)ycbcr->cr;
   }
""",
    """   out->offsets[0] = (int)y_ptr;
   /* We assume here that all the planes are located in one DMA-buf. */
   if (chroma_order == YCrCb) {
      out->offsets[1] = (int)cr_ptr;
      out->offsets[2] = (int)cb_ptr;
   } else {
      out->offsets[1] = (int)cb_ptr;
      out->offsets[2] = (int)cr_ptr;
   }
""",
    "u_gralloc normalized YV12 offsets",
)


# v0.17: import QTI's private NV12 Venus UBWC Android buffers
# (HAL format 0x7fa30c06) through the legacy Qualcomm PlaneLayoutInfo ABI.
#
# The v0.16 SurfaceFlinger tombstone proved the remaining failure is no longer
# the exact-size RGB/CCU path.  SurfaceFlinger aborts while importing a
# 720x1280 AHardwareBuffer whose vendor format is 0x7fa30c06.  Mesa 26.2.2's
# old qcom gralloc backend does not classify that private format as YUV and
# consequently returns VK_ERROR_INVALID_EXTERNAL_HANDLE.
#
# Keep this deliberately narrow.  We runtime-load the exact public Qualcomm
# GetYUVPlaneInfo(BufferInfo, ...) symbol, validate the native-handle ABI and
# all four physical NV12-UBWC ranges (Y data, UV data, Y metadata, UV
# metadata), cross-check the handle-aware android_ycbcr result returned by the
# already-active gralloc module, then normalize it to the two logical Vulkan
# planes expected by DRM_FORMAT_NV12 + DRM_FORMAT_MOD_QCOM_COMPRESSED.
replace_once(
    "src/util/u_gralloc/u_gralloc_qcom.c",
    """#include <assert.h>
#include <dlfcn.h>
#include <errno.h>
#include <string.h>
""",
    """#include <assert.h>
#include <dlfcn.h>
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
""",
    "qcom NV12 UBWC includes",
)

replace_once(
    "src/util/u_gralloc/u_gralloc_qcom.c",
    """/* Using this gralloc is not recommended for new distributions. */

struct qcom_gralloc {
""",
    r"""/* Using this gralloc is not recommended for new distributions. */

#define TG_QTI_NV12_UBWC_FORMAT 0x7fa30c06
#define TG_QTI_TP10_UBWC_FORMAT 0x7fa30c09
#define TG_QTI_HANDLE_NUM_FDS 2
#define TG_QTI_HANDLE_MIN_INTS 22
#define TG_QTI_HANDLE_MAX_INTS 26
#define TG_QTI_HANDLE_MAGIC \
   (('g' << 24) | ('m' << 16) | ('s' << 8) | 'm')

#define TG_QTI_HANDLE_FLAGS_INDEX 3
#define TG_QTI_HANDLE_WIDTH_INDEX 4
#define TG_QTI_HANDLE_HEIGHT_INDEX 5
#define TG_QTI_HANDLE_UNALIGNED_WIDTH_INDEX 6
#define TG_QTI_HANDLE_UNALIGNED_HEIGHT_INDEX 7
#define TG_QTI_HANDLE_FORMAT_INDEX 8
#define TG_QTI_HANDLE_LAYER_COUNT_INDEX 10
#define TG_QTI_HANDLE_USAGE_INDEX 13
#define TG_QTI_HANDLE_SIZE_INDEX 15
#define TG_QTI_HANDLE_OFFSET_INDEX 16
#define TG_QTI_HANDLE_BASE_INDEX 18

#define TG_QTI_FLAG_SECURE_BUFFER 0x00000400u
#define TG_QTI_FLAG_UBWC_ALIGNED 0x08000000u
#define TG_QTI_FLAG_UBWC_ALIGNED_PI 0x40000000u

#define TG_QTI_PLANE_Y (1u << 0)
#define TG_QTI_PLANE_CB (1u << 1)
#define TG_QTI_PLANE_CR (1u << 2)
#define TG_QTI_PLANE_META (1u << 31)

#define TG_QTI_GET_YUV_PLANE_LAYOUTS_SYMBOL \
   "_ZN7gralloc15GetYUVPlaneInfoERKNS_10BufferInfoEiiiiPiPNS_15PlaneLayoutInfoE"

struct tg_qti_buffer_info {
   int32_t width;
   int32_t height;
   int32_t format;
   int32_t layer_count;
   uint64_t usage;
};

struct tg_qti_plane_layout_info {
   uint32_t component;
   uint32_t horizontal_subsampling;
   uint32_t vertical_subsampling;
   uint32_t offset;
   int32_t step;
   int32_t stride;
   int32_t stride_bytes;
   int32_t scanlines;
   uint32_t size;
};

typedef int (*tg_qti_get_yuv_plane_layouts_t)(
   const struct tg_qti_buffer_info *info, int32_t format, int32_t width,
   int32_t height, int32_t flags, int *plane_count,
   struct tg_qti_plane_layout_info *plane_info);

struct qcom_gralloc {
""",
    "qcom NV12 UBWC ABI definitions",
)

replace_once(
    "src/util/u_gralloc/u_gralloc_qcom.c",
    """   void *perform_handle;
   int (* perform)(void *dev, int op, ...);
   struct u_gralloc *fallback_gralloc;
};
""",
    """   void *perform_handle;
   int (* perform)(void *dev, int op, ...);
   struct u_gralloc *fallback_gralloc;
   void *grallocutils;
   tg_qti_get_yuv_plane_layouts_t get_yuv_plane_layouts;
};
""",
    "qcom NV12 UBWC runtime helper fields",
)

qcom = src / "src/util/u_gralloc/u_gralloc_qcom.c"
qcom_text = qcom.read_text()
qcom_helper_anchor = """static int
fallback_gralloc_get_yuv_info(struct u_gralloc *gralloc,
"""
if qcom_text.count(qcom_helper_anchor) != 1:
    raise SystemExit(
        f"qcom NV12 UBWC helper anchor count: {qcom_text.count(qcom_helper_anchor)}"
    )

qcom_helpers = r"""static uint64_t
tg_qti_read_u64(const native_handle_t *handle, int index)
{
   uint64_t value = 0;
   memcpy(&value, &handle->data[index], sizeof(value));
   return value;
}

static bool
tg_qti_pointer_matches(uintptr_t base, uint32_t offset, const void *pointer)
{
   const uintptr_t value = (uintptr_t) pointer;

   /* Legacy Samsung/QCOM gralloc can return either null-based offsets or
    * base-relative process virtual addresses depending on mapping state.
    */
   if (value == (uintptr_t) offset)
      return true;

   return offset <= UINTPTR_MAX - base && value == base + offset;
}

static bool
tg_qti_plane_range_valid(const struct tg_qti_plane_layout_info *plane,
                         uint32_t component, uint32_t hsub, uint32_t vsub,
                         int32_t step, int32_t aligned_width,
                         uint64_t declared_size, uint64_t dma_size)
{
   if (plane->component != component ||
       plane->horizontal_subsampling != hsub ||
       plane->vertical_subsampling != vsub ||
       (step >= 0 && plane->step != step) ||
       plane->stride != aligned_width ||
       plane->stride_bytes <= 0 || plane->scanlines <= 0 ||
       plane->size == 0 || plane->offset > INT_MAX)
      return false;

   const uint64_t offset = plane->offset;
   const uint64_t size = plane->size;
   const uint64_t stride = (uint64_t) plane->stride_bytes;
   const uint64_t rows = (uint64_t) plane->scanlines;

   if (offset > declared_size || size > declared_size - offset ||
       offset > dma_size || size > dma_size - offset ||
       rows > UINT64_MAX / stride || stride * rows > size)
      return false;

   return true;
}

/* Return -EAGAIN when the handle is not one of the private QTI UBWC
 * allocations we own. Once a supported private format is positively
 * identified, every inconsistency is a hard failure so it can never fall
 * through and be guessed as linear.
 */
static int
tg_qcom_get_qti_ubwc_info(struct qcom_gralloc *gr,
                           struct u_gralloc_buffer_handle *hnd,
                           struct u_gralloc_buffer_basic_info *out)
{
   if (!hnd || !hnd->handle || !gr->get_yuv_plane_layouts)
      return -EAGAIN;

   const native_handle_t *handle = hnd->handle;
   if (sizeof(void *) != 8 ||
       handle->version != sizeof(native_handle_t) ||
       handle->numFds != TG_QTI_HANDLE_NUM_FDS ||
       handle->numInts < TG_QTI_HANDLE_MIN_INTS ||
       handle->numInts > TG_QTI_HANDLE_MAX_INTS ||
       handle->data[handle->numFds] != TG_QTI_HANDLE_MAGIC)
      return -EAGAIN;

   const int32_t private_format = handle->data[TG_QTI_HANDLE_FORMAT_INDEX];
   const bool is_nv12_ubwc = private_format == TG_QTI_NV12_UBWC_FORMAT;
   const bool is_tp10_ubwc = private_format == TG_QTI_TP10_UBWC_FORMAT;
   if (!is_nv12_ubwc && !is_tp10_ubwc)
      return -EAGAIN;

   if (hnd->hal_format != TG_QTI_NV12_UBWC_FORMAT &&
       hnd->hal_format != TG_QTI_TP10_UBWC_FORMAT &&
       hnd->hal_format != HAL_PIXEL_FORMAT_IMPLEMENTATION_DEFINED &&
       hnd->hal_format != HAL_PIXEL_FORMAT_YCbCr_420_888) {
      if (is_tp10_ubwc)
         mesa_loge("touchGrass TP10 reject=hal_format hal=0x%x", hnd->hal_format);
      return -EINVAL;
   }

   /* Qualcomm TP10 UBWC is packed 10-bit 4:2:0. Its UV plane advances
    * three bytes per chroma pair. Some vendor PlaneLayoutInfo revisions do
    * not initialize the Y-plane step for TP10, so do not use that field as
    * a validity gate for TP10. All byte strides, offsets, sizes and the
    * handle-aware android_ycbcr view are still independently validated.
    */
   const int expected_y_step = is_tp10_ubwc ? -1 : 1;
   const int expected_uv_step = is_tp10_ubwc ? 3 : 2;

   const uint32_t flags =
      (uint32_t) handle->data[TG_QTI_HANDLE_FLAGS_INDEX];
   const int32_t width = handle->data[TG_QTI_HANDLE_WIDTH_INDEX];
   const int32_t height = handle->data[TG_QTI_HANDLE_HEIGHT_INDEX];
   const int32_t unaligned_width =
      handle->data[TG_QTI_HANDLE_UNALIGNED_WIDTH_INDEX];
   const int32_t unaligned_height =
      handle->data[TG_QTI_HANDLE_UNALIGNED_HEIGHT_INDEX];
   const int32_t layer_count =
      handle->data[TG_QTI_HANDLE_LAYER_COUNT_INDEX];
   const uint64_t usage =
      tg_qti_read_u64(handle, TG_QTI_HANDLE_USAGE_INDEX);
   const int32_t declared_size_i =
      handle->data[TG_QTI_HANDLE_SIZE_INDEX];
   const uintptr_t base =
      (uintptr_t) tg_qti_read_u64(handle, TG_QTI_HANDLE_BASE_INDEX);

   if (is_tp10_ubwc)
      mesa_loge("touchGrass TP10 handle flags=0x%x aligned=%dx%d unaligned=%dx%d layers=%d size=%d usage=0x%llx",
                flags, width, height, unaligned_width, unaligned_height,
                layer_count, declared_size_i, (unsigned long long) usage);

   if (width <= 0 || height <= 0 ||
       unaligned_width <= 0 || unaligned_height <= 0 ||
       width < unaligned_width || height < unaligned_height ||
       layer_count != 1 || declared_size_i <= 0 ||
       handle->data[TG_QTI_HANDLE_OFFSET_INDEX] != 0 ||
       !(flags & TG_QTI_FLAG_UBWC_ALIGNED) ||
       (flags & (TG_QTI_FLAG_UBWC_ALIGNED_PI |
                 TG_QTI_FLAG_SECURE_BUFFER))) {
      if (is_tp10_ubwc)
         mesa_loge("touchGrass TP10 reject=handle_validation offset=%d",
                   handle->data[TG_QTI_HANDLE_OFFSET_INDEX]);
      return -EINVAL;
   }

   const uint64_t declared_size = (uint32_t) declared_size_i;
   off_t dma_end = lseek(handle->data[0], 0, SEEK_END);
   if (is_tp10_ubwc)
      mesa_loge("touchGrass TP10 dma declared=%llu actual=%lld",
                (unsigned long long) declared_size, (long long) dma_end);
   if (dma_end <= 0 || declared_size > (uint64_t) dma_end) {
      if (is_tp10_ubwc)
         mesa_loge("touchGrass TP10 reject=dma_size");
      return -EINVAL;
   }
   const uint64_t dma_size = (uint64_t) dma_end;

   struct tg_qti_buffer_info info = {
      .width = unaligned_width,
      .height = unaligned_height,
      .format = private_format,
      .layer_count = 1,
      .usage = usage,
   };
   struct tg_qti_plane_layout_info planes[8];
   memset(planes, 0, sizeof(planes));
   int plane_count = 0;

   int ret = gr->get_yuv_plane_layouts(
      &info, private_format, width, height, 0, &plane_count, planes);
   if (is_tp10_ubwc) {
      mesa_loge("touchGrass TP10 GetYUVPlaneInfo ret=%d count=%d", ret, plane_count);
      if (ret == 0 && plane_count >= 0 && plane_count <= 8) {
         for (int i = 0; i < plane_count; ++i) {
            mesa_loge("touchGrass TP10 plane[%d] comp=0x%x sub=%u,%u off=%u step=%d stride=%d bytes=%d scan=%d size=%u",
                      i, planes[i].component,
                      planes[i].horizontal_subsampling,
                      planes[i].vertical_subsampling,
                      planes[i].offset, planes[i].step,
                      planes[i].stride, planes[i].stride_bytes,
                      planes[i].scanlines, planes[i].size);
         }
      }
   }
   if (ret != 0 || plane_count != 4) {
      if (is_tp10_ubwc)
         mesa_loge("touchGrass TP10 reject=plane_query");
      return -EINVAL;
   }

   const struct tg_qti_plane_layout_info *y = &planes[0];
   const struct tg_qti_plane_layout_info *uv = &planes[1];
   const struct tg_qti_plane_layout_info *y_meta = &planes[2];
   const struct tg_qti_plane_layout_info *uv_meta = &planes[3];

   const bool y_valid = tg_qti_plane_range_valid(
      y, TG_QTI_PLANE_Y, 0, 0, expected_y_step, width,
      declared_size, dma_size);
   const bool uv_valid = tg_qti_plane_range_valid(
      uv, TG_QTI_PLANE_CB | TG_QTI_PLANE_CR, 1, 1,
      expected_uv_step, width, declared_size, dma_size);
   const bool y_meta_valid = tg_qti_plane_range_valid(
      y_meta, TG_QTI_PLANE_META | TG_QTI_PLANE_Y, 0, 0, 0, width,
      declared_size, dma_size);
   const bool uv_meta_valid = tg_qti_plane_range_valid(
      uv_meta, TG_QTI_PLANE_META | TG_QTI_PLANE_CB | TG_QTI_PLANE_CR,
      0, 0, 0, width, declared_size, dma_size);
   if (is_tp10_ubwc)
      mesa_loge("touchGrass TP10 plane_valid y=%d uv=%d ym=%d uvm=%d",
                y_valid, uv_valid, y_meta_valid, uv_meta_valid);
   if (!y_valid || !uv_valid || !y_meta_valid || !uv_meta_valid) {
      if (is_tp10_ubwc)
         mesa_loge("touchGrass TP10 reject=plane_validation");
      return -EINVAL;
   }

   const uint64_t y_meta_end =
      (uint64_t) y_meta->offset + y_meta->size;
   const uint64_t y_end = (uint64_t) y->offset + y->size;
   const uint64_t uv_meta_end =
      (uint64_t) uv_meta->offset + uv_meta->size;
   const uint64_t uv_end = (uint64_t) uv->offset + uv->size;

   /* Progressive QTI NV12 UBWC is physically:
    * Y metadata -> Y data -> UV metadata -> UV data.
    */
   if (is_tp10_ubwc)
      mesa_loge("touchGrass TP10 ordering ym=%u y=%u uvm=%u uv=%u ends=%llu,%llu,%llu,%llu",
                y_meta->offset, y->offset, uv_meta->offset, uv->offset,
                (unsigned long long) y_meta_end,
                (unsigned long long) y_end,
                (unsigned long long) uv_meta_end,
                (unsigned long long) uv_end);
   if (y_meta->offset != 0 ||
       (uint64_t) y->offset != y_meta_end ||
       (uint64_t) uv_meta->offset != y_end ||
       (uint64_t) uv->offset != uv_meta_end ||
       uv_end > declared_size || uv_end > dma_size) {
      if (is_tp10_ubwc)
         mesa_loge("touchGrass TP10 reject=plane_order");
      return -EINVAL;
   }

   /* Independently cross-check the layout against the handle-aware vendor
    * query used by the stock gralloc module.
    */
   struct android_ycbcr ycbcr[2];
   memset(ycbcr, 0, sizeof(ycbcr));
   ret = gr->perform(gr->perform_handle,
                     GRALLOC_MODULE_PERFORM_GET_YUV_PLANE_INFO,
                     handle, ycbcr);
   if (is_tp10_ubwc)
      mesa_loge("touchGrass TP10 ycbcr ret=%d y=%p cb=%p cr=%p ys=%zu cs=%zu step=%zu base=0x%llx",
                ret, ycbcr[0].y, ycbcr[0].cb, ycbcr[0].cr,
                ycbcr[0].ystride, ycbcr[0].cstride,
                ycbcr[0].chroma_step, (unsigned long long) base);
   if (ret != 0 ||
       ycbcr[1].y || ycbcr[1].cb || ycbcr[1].cr ||
       ycbcr[1].ystride || ycbcr[1].cstride ||
       ycbcr[1].chroma_step ||
       ycbcr[0].ystride != (size_t) y->stride_bytes ||
       ycbcr[0].cstride != (size_t) uv->stride_bytes ||
       ycbcr[0].chroma_step != (size_t) expected_uv_step ||
       !tg_qti_pointer_matches(base, y->offset, ycbcr[0].y) ||
       !tg_qti_pointer_matches(base, uv->offset, ycbcr[0].cb) ||
       uv->offset == UINT32_MAX ||
       !tg_qti_pointer_matches(base, uv->offset + 1, ycbcr[0].cr)) {
      if (is_tp10_ubwc)
         mesa_loge("touchGrass TP10 reject=ycbcr_crosscheck");
      return -EINVAL;
   }

   out->drm_fourcc = is_tp10_ubwc ? DRM_FORMAT_NV15 : DRM_FORMAT_NV12;
   out->modifier = DRM_FORMAT_MOD_QCOM_COMPRESSED;
   out->num_planes = 2;
   out->fds[0] = out->fds[1] = handle->data[0];

   /* For QCOM_COMPRESSED, Turnip's logical plane starts at the metadata
    * range. FDL computes the primary-data offset from the modifier geometry.
    */
   out->offsets[0] = (int) y_meta->offset;
   out->offsets[1] = (int) uv_meta->offset;
   out->strides[0] = y->stride_bytes;
   out->strides[1] = uv->stride_bytes;

   if (is_tp10_ubwc)
      mesa_logi("touchGrass: imported QTI TP10 UBWC 0x7fa30c09 as NV15 via legacy PlaneLayoutInfo");
   else
      mesa_logi("touchGrass: imported QTI NV12 UBWC 0x7fa30c06 via legacy PlaneLayoutInfo");
   return 0;
}

"""
qcom_text = qcom_text.replace(
    qcom_helper_anchor, qcom_helpers + qcom_helper_anchor, 1
)

get_info_anchor = """   int out_flag = 0;
   int err;

   err = gr->perform(gr->perform_handle, GRALLOC_MODULE_PERFORM_GET_UBWC_FLAG,
"""
get_info_new = """   int out_flag = 0;
   int err;

   /* Intercept only positively identified private QTI NV12/TP10 UBWC
    * handles. -EAGAIN means this is an ordinary allocation and the Mesa
    * 26.2.2 path below remains untouched.
    */
   int qti_ret = tg_qcom_get_qti_ubwc_info(gr, hnd, out);
   if (qti_ret != -EAGAIN)
      return qti_ret;

   err = gr->perform(gr->perform_handle, GRALLOC_MODULE_PERFORM_GET_UBWC_FLAG,
"""
if qcom_text.count(get_info_anchor) != 1:
    raise SystemExit(
        f"qcom NV12 UBWC get_buffer_info anchor count: {qcom_text.count(get_info_anchor)}"
    )
qcom_text = qcom_text.replace(get_info_anchor, get_info_new, 1)

destroy_anchor = """   if (gr->fallback_gralloc)
      gr->fallback_gralloc->ops.destroy(gr->fallback_gralloc);

   FREE(gr);
"""
destroy_new = """   if (gr->fallback_gralloc)
      gr->fallback_gralloc->ops.destroy(gr->fallback_gralloc);

   if (gr->grallocutils)
      dlclose(gr->grallocutils);

   FREE(gr);
"""
if qcom_text.count(destroy_anchor) != 1:
    raise SystemExit(
        f"qcom NV12 UBWC destroy anchor count: {qcom_text.count(destroy_anchor)}"
    )
qcom_text = qcom_text.replace(destroy_anchor, destroy_new, 1)

create_anchor = """   if (out_stride == 0)
      goto fail;

   gr->base.ops.get_buffer_basic_info = get_buffer_info;
"""
create_new = r"""   if (out_stride == 0)
      goto fail;

   /* Prefer the helper already loaded with the active gralloc module.  If the
    * dependency is not in that lookup scope, try the process-wide scope and
    * finally take an explicit reference.  Failure is non-fatal for ordinary
    * buffers; only the supported private QTI UBWC imports require this
    * helper.
    */
   void *plane_symbol =
      dlsym(gr->gralloc_module->dso, TG_QTI_GET_YUV_PLANE_LAYOUTS_SYMBOL);
   if (!plane_symbol)
      plane_symbol = dlsym(RTLD_DEFAULT, TG_QTI_GET_YUV_PLANE_LAYOUTS_SYMBOL);
   if (!plane_symbol) {
      gr->grallocutils = dlopen("libgrallocutils.so", RTLD_NOW | RTLD_LOCAL);
      if (gr->grallocutils)
         plane_symbol =
            dlsym(gr->grallocutils, TG_QTI_GET_YUV_PLANE_LAYOUTS_SYMBOL);
   }

   if (plane_symbol) {
      memcpy(&gr->get_yuv_plane_layouts, &plane_symbol,
             sizeof(gr->get_yuv_plane_layouts));
      mesa_logi("touchGrass: QTI legacy PlaneLayoutInfo helper available");
   }

   gr->base.ops.get_buffer_basic_info = get_buffer_info;
"""
if qcom_text.count(create_anchor) != 1:
    raise SystemExit(
        f"qcom NV12 UBWC create anchor count: {qcom_text.count(create_anchor)}"
    )
qcom_text = qcom_text.replace(create_anchor, create_new, 1)

qcom.write_text(qcom_text)

tu = src / "src/freedreno/vulkan/tu_image.cc"
text = tu.read_text()
anchor = """template <chip CHIP>
VkResult
tu_image_init(struct tu_device *device, struct tu_image *image,
              const VkImageCreateInfo *pCreateInfo, uint64_t modifier,
              const VkSubresourceLayout *plane_layouts)
{"""
if text.count(anchor) != 1:
    raise SystemExit(f"tu_image_init anchor count: {text.count(anchor)}")

helper = r"""static bool
tu_is_android_yv12_import(struct tu_image *image, uint64_t modifier,
                          const VkSubresourceLayout *plane_layouts)
{
   const bool is_android_buffer =
      vk_image_is_android_hardware_buffer(&image->vk) ||
      vk_image_is_android_native_buffer(&image->vk) ||
      vk_image_is_android_native_buffer_alias(&image->vk);

   if (!plane_layouts || modifier != DRM_FORMAT_MOD_LINEAR ||
       !is_android_buffer ||
       image->vk.format != VK_FORMAT_G8_B8_R8_3PLANE_420_UNORM ||
       image->vk.image_type != VK_IMAGE_TYPE_2D ||
       image->vk.samples != VK_SAMPLE_COUNT_1_BIT ||
       image->vk.mip_levels != 1 || image->vk.array_layers != 1 ||
       image->vk.extent.depth != 1 ||
       image->vk.usage != VK_IMAGE_USAGE_SAMPLED_BIT ||
       (image->vk.extent.width & 1) || (image->vk.extent.height & 1))
      return false;

   const uint64_t y_pitch = plane_layouts[0].rowPitch;
   const uint64_t cb_pitch = plane_layouts[1].rowPitch;
   const uint64_t cr_pitch = plane_layouts[2].rowPitch;

   if (!y_pitch || !cb_pitch || y_pitch > UINT32_MAX ||
       cb_pitch > UINT32_MAX || cr_pitch != cb_pitch ||
       y_pitch < image->vk.extent.width ||
       cb_pitch < image->vk.extent.width / 2 ||
       (y_pitch & 15) ||
       cb_pitch != ((y_pitch / 2 + 15) & ~UINT64_C(15)))
      return false;

   const uint64_t height = image->vk.extent.height;
   if (height > UINT64_MAX / y_pitch ||
       height / 2 > UINT64_MAX / cb_pitch)
      return false;

   const uint64_t y_size = y_pitch * height;
   const uint64_t chroma_size = cb_pitch * (height / 2);
   if (y_size > UINT32_MAX ||
       chroma_size > (UINT32_MAX - y_size) / 2)
      return false;

   /* vk_android already converts DRM Y-V-U order into Vulkan Y-U-V order. */
   return plane_layouts[0].offset == 0 &&
          plane_layouts[2].offset == y_size &&
          plane_layouts[1].offset == y_size + chroma_size;
}

"""
text = text.replace(anchor, helper + anchor, 1)

anchor2 = """   assert(!(image->vk.create_flags & VK_IMAGE_CREATE_SPARSE_RESIDENCY_BIT) ||
          tile_mode == TILE6_3);

   for (uint32_t i = 0; i < tu6_plane_count(image->vk.format); i++) {"""
new2 = """   assert(!(image->vk.create_flags & VK_IMAGE_CREATE_SPARSE_RESIDENCY_BIT) ||
          tile_mode == TILE6_3);

   /* Android YV12 guarantees 16-byte row-pitch alignment. Only relax the
    * imported-layout validation for the exact sampled-only linear AHB shape.
    */
   const bool android_yv12_import =
      tu_is_android_yv12_import(image, modifier, plane_layouts);

   for (uint32_t i = 0; i < tu6_plane_count(image->vk.format); i++) {"""
if text.count(anchor2) != 1:
    raise SystemExit(f"YV12 insertion anchor count: {text.count(anchor2)}")
text = text.replace(anchor2, new2, 1)

anchor3 = """      struct fdl_explicit_layout plane_layout;

      if (plane_layouts) {"""
new3 = """      struct fdl_explicit_layout plane_layout = {
         .pitch_alignment = android_yv12_import ? 16u : 0u,
         .skip_last_level_padding = android_yv12_import,
      };

      if (plane_layouts) {"""
if text.count(anchor3) != 1:
    raise SystemExit(f"plane layout init anchor count: {text.count(anchor3)}")
text = text.replace(anchor3, new3, 1)

# Backport Turnip-Enhanced 76a4087d9e26fd2470936fae698827b6a2872528.
# Android gralloc linear color allocations can end exactly at the final
# logical row. Turnip's ordinary last-level tail padding plus event/CCU GMEM
# fast paths may then access beyond the dma-buf and produce CCU write
# translation faults. Preserve the exact imported footprint and route edge
# loads/stores through bounded paths.
replace_once(
    "src/freedreno/vulkan/tu_image.h",
    """   struct fdl_layout layout[3];
   uint64_t subsampled_metadata_offset;
   uint64_t total_size;

   /* Set when bound */
""",
    """   struct fdl_layout layout[3];
   uint64_t subsampled_metadata_offset;
   uint64_t total_size;

   /* Exact-size linear Android imports have no private FDL tail rows, so
    * mem<->GMEM operations must use bounded edge paths when necessary.
    */
   bool android_external_no_gmem_padding;

   /* Set when bound */
""",
    "tu_image exact linear Android flag",
)

helper_anchor = """template <chip CHIP>
VkResult
tu_image_init(struct tu_device *device, struct tu_image *image,
              const VkImageCreateInfo *pCreateInfo, uint64_t modifier,
              const VkSubresourceLayout *plane_layouts)
{"""
if text.count(helper_anchor) != 1:
    raise SystemExit(f"exact-linear helper anchor count: {text.count(helper_anchor)}")

exact_linear_helper = r"""static bool
tu_is_android_exact_linear_color_import(struct tu_image *image,
                                        uint64_t modifier,
                                        const VkSubresourceLayout *plane_layouts)
{
   const VkImageUsageFlags supported_usage =
      VK_IMAGE_USAGE_TRANSFER_SRC_BIT |
      VK_IMAGE_USAGE_TRANSFER_DST_BIT |
      VK_IMAGE_USAGE_SAMPLED_BIT |
      VK_IMAGE_USAGE_STORAGE_BIT |
      VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT |
      VK_IMAGE_USAGE_INPUT_ATTACHMENT_BIT |
      VK_IMAGE_USAGE_ATTACHMENT_FEEDBACK_LOOP_BIT_EXT;

   const bool is_android_buffer =
      vk_image_is_android_hardware_buffer(&image->vk) ||
      vk_image_is_android_native_buffer(&image->vk) ||
      vk_image_is_android_native_buffer_alias(&image->vk);

   if (!is_android_buffer || !plane_layouts ||
       modifier != DRM_FORMAT_MOD_LINEAR ||
       tu6_plane_count(image->vk.format) != 1 ||
       !vk_format_is_color(image->vk.format) ||
       vk_format_is_depth_or_stencil(image->vk.format) ||
       vk_format_is_compressed(image->vk.format) ||
       image->vk.image_type != VK_IMAGE_TYPE_2D ||
       image->vk.samples != VK_SAMPLE_COUNT_1_BIT ||
       image->vk.mip_levels != 1 || image->vk.array_layers != 1 ||
       image->vk.extent.depth != 1 ||
       (image->vk.usage & ~supported_usage))
      return false;

   const enum pipe_format format = tu6_plane_format(image->vk.format, 0);
   if (format == PIPE_FORMAT_NONE)
      return false;

   const uint64_t pitch = plane_layouts[0].rowPitch;
   const uint64_t offset = plane_layouts[0].offset;
   const uint64_t min_pitch =
      util_format_get_stride(format, image->vk.extent.width);
   const uint64_t size = pitch * image->vk.extent.height;

   return pitch >= min_pitch && pitch <= UINT32_MAX &&
          offset <= UINT32_MAX && size <= UINT32_MAX &&
          offset + size <= UINT32_MAX;
}

"""
text = text.replace(helper_anchor, exact_linear_helper + helper_anchor, 1)

layout_anchor = """   /* Layout computation begins here */
   enum a6xx_tile_mode tile_mode = TILE6_3;
#if DETECT_OS_LINUX || DETECT_OS_BSD
"""
layout_new = """   /* Layout computation begins here */
   enum a6xx_tile_mode tile_mode = TILE6_3;
   image->android_external_no_gmem_padding = false;
#if DETECT_OS_LINUX || DETECT_OS_BSD
"""
if text.count(layout_anchor) != 1:
    raise SystemExit(f"exact-linear layout anchor count: {text.count(layout_anchor)}")
text = text.replace(layout_anchor, layout_new, 1)

yv12_bool_anchor = """   const bool android_yv12_import =
      tu_is_android_yv12_import(image, modifier, plane_layouts);

   for (uint32_t i = 0; i < tu6_plane_count(image->vk.format); i++) {"""
yv12_bool_new = """   const bool android_yv12_import =
      tu_is_android_yv12_import(image, modifier, plane_layouts);
   const bool android_exact_linear_color_import =
      tu_is_android_exact_linear_color_import(image, modifier, plane_layouts);
   image->android_external_no_gmem_padding =
      android_exact_linear_color_import;

   for (uint32_t i = 0; i < tu6_plane_count(image->vk.format); i++) {"""
if text.count(yv12_bool_anchor) != 1:
    raise SystemExit(f"exact-linear boolean anchor count: {text.count(yv12_bool_anchor)}")
text = text.replace(yv12_bool_anchor, yv12_bool_new, 1)

plane_padding_anchor = """.skip_last_level_padding = android_yv12_import,"""
plane_padding_new = """.skip_last_level_padding =
            android_yv12_import || android_exact_linear_color_import,"""
if text.count(plane_padding_anchor) != 1:
    raise SystemExit(f"exact-linear padding anchor count: {text.count(plane_padding_anchor)}")
text = text.replace(plane_padding_anchor, plane_padding_new, 1)

bind_anchor = """   assert(mem);
   image->mem = mem;
"""
bind_new = """   assert(mem);

   const bool is_android_buffer =
      vk_image_is_android_hardware_buffer(&image->vk) ||
      vk_image_is_android_native_buffer(&image->vk) ||
      vk_image_is_android_native_buffer_alias(&image->vk);
   if (is_android_buffer && mem->bo &&
       (offset > mem->bo->size ||
        image->total_size > mem->bo->size - offset)) {
      return vk_errorf(device, VK_ERROR_INVALID_EXTERNAL_HANDLE,
                       "Android image binding exceeds dma-buf size (%" PRIu64
                       " + %" PRIu64 " > %" PRIu64 ")",
                       offset, image->total_size, mem->bo->size);
   }

   image->mem = mem;
"""
if text.count(bind_anchor) != 1:
    raise SystemExit(f"exact-linear bind anchor count: {text.count(bind_anchor)}")
text = text.replace(bind_anchor, bind_new, 1)

tu.write_text(text)

clear = src / "src/freedreno/vulkan/tu_clear_blit.cc"
clear_text = clear.read_text()

clear_helper_anchor = """template <chip CHIP>
void
tu_load_gmem_attachment(struct tu_cmd_buffer *cmd,
"""
if clear_text.count(clear_helper_anchor) != 1:
    raise SystemExit(f"bounded GMEM helper anchor count: {clear_text.count(clear_helper_anchor)}")

clear_helper = r"""static bool
tu_attachment_gmem_edge_unaligned(struct tu_cmd_buffer *cmd, uint32_t a,
                                  bool require_image_edge_y_alignment)
{
   struct tu_physical_device *phys_dev = cmd->device->physical_device;
   const struct tu_image_view *iview = cmd->state.attachments[a];

   unsigned render_area_count =
      cmd->state.per_layer_render_area ? cmd->state.pass->num_views : 1;

   /* Existing FDM paths already use bounded coordinates. */
   if (cmd->state.fdm_subsampled)
      return false;

   for (unsigned i = 0; i < render_area_count; i++) {
      const VkRect2D *render_area = &cmd->state.render_areas[i];
      uint32_t x1 = render_area->offset.x;
      uint32_t y1 = render_area->offset.y;
      uint32_t x2 = x1 + render_area->extent.width;
      uint32_t y2 = y1 + render_area->extent.height;

      bool need_x2_align = x2 != iview->view.width;
      if (!need_x2_align &&
          iview->image->android_external_no_gmem_padding) {
         const struct fdl_layout *layout = &iview->image->layout[0];
         const uint64_t aligned_width =
            DIV_ROUND_UP((uint64_t)x2, phys_dev->info->gmem_align_w) *
            phys_dev->info->gmem_align_w;
         const uint64_t required_pitch = aligned_width * layout->cpp;
         need_x2_align = required_pitch > layout->pitch0;
      }

      const bool need_y2_align =
         y2 != iview->view.height || iview->view.need_y2_align ||
         require_image_edge_y_alignment;

      if (x1 % phys_dev->info->gmem_align_w ||
          (x2 % phys_dev->info->gmem_align_w && need_x2_align) ||
          y1 % phys_dev->info->gmem_align_h ||
          (y2 % phys_dev->info->gmem_align_h && need_y2_align))
         return true;
   }

   return false;
}

"""
clear_text = clear_text.replace(clear_helper_anchor,
                                clear_helper + clear_helper_anchor, 1)

load_anchor = """   if (!load_common && !load_stencil)
      return;

   trace_start_gmem_load(&cmd->rp_trace, cs, cmd, attachment->format, force_load);
"""
load_new = """   if (!load_common && !load_stencil)
      return;

   const bool bounded_external_load =
      iview->image->android_external_no_gmem_padding &&
      tu_attachment_gmem_edge_unaligned(cmd, a, true);

   trace_start_gmem_load(&cmd->rp_trace, cs, cmd, attachment->format, force_load);
"""
if clear_text.count(load_anchor) != 1:
    raise SystemExit(f"bounded load anchor count: {clear_text.count(load_anchor)}")
clear_text = clear_text.replace(load_anchor, load_new, 1)

fast_load_anchor = """   if (TU_DEBUG(3D_LOAD) ||
       cmd->state.pass->has_fdm ||
"""
fast_load_new = """   if (TU_DEBUG(3D_LOAD) ||
       bounded_external_load ||
       cmd->state.pass->has_fdm ||
"""
if clear_text.count(fast_load_anchor) != 1:
    raise SystemExit(f"bounded load-path anchor count: {clear_text.count(fast_load_anchor)}")
clear_text = clear_text.replace(fast_load_anchor, fast_load_new, 1)

store_start = clear_text.index("""static bool
tu_attachment_store_unaligned(struct tu_cmd_buffer *cmd, uint32_t a)
{""")
store_end = clear_text.index("""
}

/* The fast path cannot handle mismatched mutability. */""", store_start) + 2
old_store = clear_text[store_start:store_end]
new_store = r"""static bool
tu_attachment_store_unaligned(struct tu_cmd_buffer *cmd, uint32_t a)
{
   const struct tu_image_view *iview = cmd->state.attachments[a];

   /* Unaligned store is incredibly rare in CTS, we have to force it to test. */
   if (TU_DEBUG(UNALIGNED_STORE))
      return true;

   return tu_attachment_gmem_edge_unaligned(
      cmd, a, iview->image->android_external_no_gmem_padding);
}"""
clear_text = clear_text[:store_start] + new_store + clear_text[store_end:]

clear.write_text(clear_text)

# Keep patch verification inside Python so an audit failure always names the
# exact missing source marker instead of exiting silently under set -e.
source_checks = [
    ("src/freedreno/fdl/freedreno_layout.h", "bool has_explicit_pitch : 1;", "FDL explicit-pitch flag"),
    ("src/freedreno/fdl/freedreno_layout.h", "if (level == 0 && layout->has_explicit_pitch)", "FDL explicit level-0 pitch"),
    ("src/freedreno/vulkan/tu_image.cc", "pitch_alignment = android_yv12_import ? 16u : 0u", "YV12 16-byte pitch"),
    ("src/freedreno/vulkan/tu_image.cc", "android_yv12_import || android_exact_linear_color_import", "exact linear tail-padding policy"),
    ("src/freedreno/vulkan/tu_image.cc", "tu_is_android_yv12_import", "YV12 import recognizer"),
    ("src/freedreno/vulkan/tu_image.h", "android_external_no_gmem_padding", "exact linear AHB flag"),
    ("src/freedreno/vulkan/tu_image.cc", "tu_is_android_exact_linear_color_import", "exact linear AHB recognizer"),
    ("src/freedreno/vulkan/tu_image.cc", "Android image binding exceeds dma-buf size", "Android dma-buf bounds check"),
    ("src/freedreno/vulkan/tu_clear_blit.cc", "tu_attachment_gmem_edge_unaligned", "bounded GMEM edge helper"),
    ("src/freedreno/vulkan/tu_clear_blit.cc", "bounded_external_load", "bounded GMEM load path"),
    ("src/util/u_gralloc/u_gralloc_qcom.c", "TG_QTI_NV12_UBWC_FORMAT 0x7fa30c06", "QTI private NV12 UBWC format"),
    ("src/util/u_gralloc/u_gralloc_qcom.c", "TG_QTI_TP10_UBWC_FORMAT 0x7fa30c09", "QTI private TP10 UBWC format"),
    ("src/util/u_gralloc/u_gralloc_qcom.c", "TG_QTI_GET_YUV_PLANE_LAYOUTS_SYMBOL", "QTI PlaneLayoutInfo runtime ABI"),
    ("src/util/u_gralloc/u_gralloc_qcom.c", "DRM_FORMAT_NV15", "QTI TP10 NV15 mapping"),
    ("src/util/u_gralloc/u_gralloc_qcom.c", "touchGrass: imported QTI NV12 UBWC 0x7fa30c06 via legacy PlaneLayoutInfo", "QTI NV12 UBWC import path"),
    ("src/util/u_gralloc/u_gralloc_qcom.c", "touchGrass: imported QTI TP10 UBWC 0x7fa30c09 as NV15 via legacy PlaneLayoutInfo", "QTI TP10 UBWC import path"),
]
for rel, needle, label in source_checks:
    if needle not in (src / rel).read_text():
        raise SystemExit(f"source audit failed: {label}: {needle}")
    print(f"source_audit={label}:PASS")
PY

echo "==> Cap Turnip bring-up to Vulkan 1.3"
python3 - "$SRC" <<'PY'
from pathlib import Path
import sys

src = Path(sys.argv[1])
dev = src / "src/freedreno/vulkan/tu_device.cc"
meson = src / "src/freedreno/vulkan/meson.build"

s = dev.read_text()
old = "#define TU_API_VERSION VK_MAKE_VERSION(1, 4, VK_HEADER_VERSION)"
new = "#define TU_API_VERSION VK_MAKE_VERSION(1, 3, VK_HEADER_VERSION)"
if s.count(old) != 1:
    raise SystemExit(f"unexpected TU_API_VERSION anchor count: {s.count(old)}")
dev.write_text(s.replace(old, new, 1))

s = meson.read_text()
old = "'--api-version', '1.4'"
if s.count(old) != 2:
    raise SystemExit(f"unexpected ICD api-version anchor count: {s.count(old)}")
meson.write_text(s.replace(old, "'--api-version', '1.3'"))
PY

grep -Fq '#define TU_API_VERSION VK_MAKE_VERSION(1, 3, VK_HEADER_VERSION)'   "$SRC/src/freedreno/vulkan/tu_device.cc"
test "$(grep -F "'--api-version', '1.3'" "$SRC/src/freedreno/vulkan/meson.build" | wc -l)" -eq 2

TOOLCHAIN="$NDK/toolchains/llvm/prebuilt/linux-x86_64"
CROSS="$WORK/android-aarch64.ini"
cat > "$CROSS" <<EOF
[constants]
ndk_path = '$NDK'
toolchain = ndk_path / 'toolchains/llvm/prebuilt/linux-x86_64'

[binaries]
ar = toolchain / 'bin/llvm-ar'
c = ['ccache', toolchain / 'bin/aarch64-linux-android${ANDROID_API}-clang']
cpp = ['ccache', toolchain / 'bin/aarch64-linux-android${ANDROID_API}-clang++', '-fno-exceptions', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '--start-no-unused-arguments', '-static-libstdc++', '--end-no-unused-arguments']
c_ld = 'lld'
cpp_ld = 'lld'
strip = toolchain / 'bin/llvm-strip'

[host_machine]
system = 'android'
cpu_family = 'aarch64'
cpu = 'armv8'
endian = 'little'
EOF

echo "==> Configure Mesa Turnip A619/KGSL"
meson setup "$BUILD" "$SRC"   --cross-file "$CROSS"   --buildtype release   -Db_ndebug=true   -Dplatforms=android   -Dplatform-sdk-version="$ANDROID_API"   -Dandroid-stub=true   -Dandroid-libbacktrace=disabled   -Dandroid-libperfetto=disabled   -Dexpat=disabled   -Dxmlconfig=disabled   -Degl=disabled   -Dgles1=disabled   -Dgles2=disabled   -Dopengl=false   -Dgbm=disabled   -Dglx=disabled   -Dgallium-drivers=   -Dvulkan-drivers=freedreno   -Dvulkan-layers=   -Dfreedreno-kmds=kgsl   -Dllvm=disabled   -Dvalgrind=disabled   -Dlibunwind=disabled   -Dlmsensors=disabled   -Dperfetto=false   -Dzstd=disabled   -Dzlib=enabled   -Dbuild-tests=false   -Dtools=   -Dvideo-codecs=   -Dtu-build-id=15799e6d32f2965a70353013be22dc22a9d57c012b9085f860e94bd349821eac

echo "==> Compile Turnip"
meson compile -C "$BUILD"

DRIVER="$BUILD/src/freedreno/vulkan/libvulkan_freedreno.so"
test -s "$DRIVER"

echo "==> Stage Android Vulkan HAL"
cp "$DRIVER" "$DIST/vulkan.adreno.so"
patchelf --set-soname vulkan.adreno.so "$DIST/vulkan.adreno.so"
patchelf --remove-rpath "$DIST/vulkan.adreno.so"
"$TOOLCHAIN/bin/llvm-strip" --strip-unneeded "$DIST/vulkan.adreno.so"

echo "==> Audit HAL"
readelf -d "$DIST/vulkan.adreno.so" | tee "$OUT/readelf-dynamic.txt"
readelf -Ws "$DIST/vulkan.adreno.so" | tee "$OUT/readelf-symbols.txt" >/dev/null
readelf -d "$DIST/vulkan.adreno.so" | grep -Fq '(SONAME)'
readelf -d "$DIST/vulkan.adreno.so" | grep -Fq 'vulkan.adreno.so'
! readelf -d "$DIST/vulkan.adreno.so" | grep -Fq '(RUNPATH)'
readelf -Ws "$DIST/vulkan.adreno.so" | grep -Eq '[[:space:]]HMI$'
for lib in libhardware.so liblog.so libnativewindow.so libsync.so libc.so; do
  readelf -d "$DIST/vulkan.adreno.so" | grep -Fq "Shared library: [$lib]"
done
file "$DIST/vulkan.adreno.so" | tee "$OUT/file.txt"
file "$DIST/vulkan.adreno.so" | grep -Fq 'ARM aarch64'
sha256sum "$DIST/vulkan.adreno.so" | tee "$OUT/vulkan.adreno.so.sha256"

echo "==> Build on-device Vulkan probe"
PROBE_SRC="$ROOT/scripts/92_turnip_vk_probe.c"
PROBE="$DIST/turnip-vk-probe"
test -s "$PROBE_SRC"
"$TOOLCHAIN/bin/aarch64-linux-android${ANDROID_API}-clang" \
  -O2 -Wall -Wextra -Werror \
  "$PROBE_SRC" -o "$PROBE" -lvulkan
"$TOOLCHAIN/bin/llvm-strip" --strip-unneeded "$PROBE"
chmod 0755 "$PROBE"
file "$PROBE" | tee "$OUT/probe-file.txt"
file "$PROBE" | grep -Fq 'ARM aarch64'
readelf -d "$PROBE" | tee "$OUT/probe-readelf-dynamic.txt"
readelf -d "$PROBE" | grep -Fq 'Shared library: [libvulkan.so]'
sha256sum "$PROBE" | tee "$OUT/turnip-vk-probe.sha256"

echo "==> Build on-device Android hardware-buffer import probe"
AHB_PROBE_SRC="$ROOT/scripts/92_turnip_ahb_probe.c"
AHB_PROBE="$DIST/turnip-ahb-probe"
test -s "$AHB_PROBE_SRC"
"$TOOLCHAIN/bin/aarch64-linux-android${ANDROID_API}-clang" \
  -O2 -Wall -Wextra -Werror \
  "$AHB_PROBE_SRC" -o "$AHB_PROBE" -lvulkan -landroid
"$TOOLCHAIN/bin/llvm-strip" --strip-unneeded "$AHB_PROBE"
chmod 0755 "$AHB_PROBE"
file "$AHB_PROBE" | tee "$OUT/ahb-probe-file.txt"
file "$AHB_PROBE" | grep -Fq 'ARM aarch64'
readelf -d "$AHB_PROBE" | tee "$OUT/ahb-probe-readelf-dynamic.txt"
readelf -d "$AHB_PROBE" | grep -Fq 'Shared library: [libvulkan.so]'
readelf -d "$AHB_PROBE" | grep -Fq 'Shared library: [libandroid.so]'
sha256sum "$AHB_PROBE" | tee "$OUT/turnip-ahb-probe.sha256"

echo "==> Build on-device YV12 GPU sampling probe"
YV12_SHADER_SRC="$ROOT/scripts/92_turnip_yv12_sample.comp"
YV12_PROBE_SRC="$ROOT/scripts/92_turnip_yv12_sample_probe.c"
YV12_SPV="$OUT/yv12_sample.spv"
YV12_HDR="$OUT/yv12_sample_spv.h"
YV12_PROBE="$DIST/turnip-yv12-sample-probe"
test -s "$YV12_SHADER_SRC"
test -s "$YV12_PROBE_SRC"
glslangValidator -V --target-env vulkan1.1 -S comp \
  "$YV12_SHADER_SRC" -o "$YV12_SPV"
python3 - "$YV12_SPV" "$YV12_HDR" <<'PY'
from pathlib import Path
import struct
import sys

spv = Path(sys.argv[1]).read_bytes()
if len(spv) % 4:
    raise SystemExit("SPIR-V size is not uint32 aligned")
words = struct.unpack("<%dI" % (len(spv) // 4), spv)
with open(sys.argv[2], "w") as out:
    out.write("#pragma once\n#include <stddef.h>\n#include <stdint.h>\n")
    out.write("static const uint32_t yv12_sample_spv[] = {\n")
    for i in range(0, len(words), 8):
        chunk = words[i:i+8]
        out.write("    " + ", ".join(f"0x{w:08x}u" for w in chunk) + ",\n")
    out.write("};\n")
    out.write("static const size_t yv12_sample_spv_size = sizeof(yv12_sample_spv);\n")
PY
"$TOOLCHAIN/bin/aarch64-linux-android${ANDROID_API}-clang" \
  -O2 -Wall -Wextra -Werror -I"$OUT" \
  "$YV12_PROBE_SRC" -o "$YV12_PROBE" -lvulkan -landroid
"$TOOLCHAIN/bin/llvm-strip" --strip-unneeded "$YV12_PROBE"
chmod 0755 "$YV12_PROBE"
file "$YV12_PROBE" | tee "$OUT/yv12-sample-probe-file.txt"
file "$YV12_PROBE" | grep -Fq 'ARM aarch64'
readelf -d "$YV12_PROBE" | tee "$OUT/yv12-sample-probe-readelf-dynamic.txt"
readelf -d "$YV12_PROBE" | grep -Fq 'Shared library: [libvulkan.so]'
readelf -d "$YV12_PROBE" | grep -Fq 'Shared library: [libandroid.so]'
sha256sum "$YV12_PROBE" "$YV12_SPV" | tee "$OUT/turnip-yv12-sample-probe.sha256"

cat > "$OUT/BUILD-INFO.txt" <<EOF
project=touchGrass Turnip A619 KGSL bring-up
mesa_version=26.2.2
mesa_archive_sha256=$MESA_SHA256
mesa_release_commit=3281a69a8bfd9f997e91c15ed0e6290cae12dd32
gpu=Adreno 619
kmd=KGSL
android_api=36
ndk=$(basename "$NDK")
turnip_api_cap=Vulkan-1.3
turnip_upstream_api=Vulkan-1.4
driver_filename=vulkan.adreno.so
soname=vulkan.adreno.so
architecture=aarch64
probe=turnip-vk-probe
probe_api_request=Vulkan-1.3
probe_mode=device-submit-memory-verify-offscreen-dynamic-render-readback
ahb_probe=turnip-ahb-probe
ahb_probe_mode=rgba-yuv420-yv12-qti-nv12-tp10-ubwc-import-bind-lifetime-forensics
yv12_sample_probe=turnip-yv12-sample-probe
yv12_sample_mode=940x1670-postfill-importfirst-stock-reference-qcom-mapped-fix
android_yv12_fix=mesa-26.2.2-explicit-layout-plus-qcom-mapped-pointer-normalization
android_linear_ahb_ccu_fix=76a4087d9e26fd2470936fae698827b6a2872528
qti_nv12_ubwc_fix=legacy-PlaneLayoutInfo-0x7fa30c06
qti_tp10_ubwc_fix=legacy-PlaneLayoutInfo-0x7fa30c09-drm-nv15
qti_nv12_ubwc_reference_commit=6255156b8e6b868992ad085e3c4ddedcfb3b65f9
android_yv12_reference_commit=aeaf924c56adf7eddb0a9033b33474b48367e33d
build_id=15799e6d32f2965a70353013be22dc22a9d57c012b9085f860e94bd349821eac
EOF

echo "==> Turnip build complete"
