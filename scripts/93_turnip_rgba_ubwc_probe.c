#include <android/hardware_buffer.h>
#include <dlfcn.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vulkan/vulkan.h>
#include <vulkan/vulkan_android.h>

#ifndef AHARDWAREBUFFER_USAGE_GPU_FRAMEBUFFER
#define AHARDWAREBUFFER_USAGE_GPU_FRAMEBUFFER (1ULL << 9)
#endif

#define TG_GRALLOC_MODULE_PERFORM_GET_UBWC_FLAG 9
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
#define TG_QTI_FLAG_UBWC_ALIGNED 0x08000000u
#define TG_QTI_FLAG_UBWC_ALIGNED_PI 0x40000000u

struct tg_native_handle {
    int version;
    int numFds;
    int numInts;
    int data[0];
};

struct tg_hw_module_methods;
struct tg_hw_module {
    uint32_t tag;
    uint16_t module_api_version;
    uint16_t hal_api_version;
    const char *id;
    const char *name;
    const char *author;
    struct tg_hw_module_methods *methods;
    void *dso;
#if UINTPTR_MAX == UINT64_MAX
    uint64_t reserved[32 - 7];
#else
    uint32_t reserved[32 - 7];
#endif
};

struct tg_gralloc_module;
typedef const struct tg_native_handle *tg_buffer_handle_t;
struct tg_gralloc_module {
    struct tg_hw_module common;
    int (*registerBuffer)(const struct tg_gralloc_module *, tg_buffer_handle_t);
    int (*unregisterBuffer)(const struct tg_gralloc_module *, tg_buffer_handle_t);
    int (*lock)(const struct tg_gralloc_module *, tg_buffer_handle_t,
                int, int, int, int, int, void **);
    int (*unlock)(const struct tg_gralloc_module *, tg_buffer_handle_t);
    int (*perform)(const struct tg_gralloc_module *, int, ...);
};

typedef int (*tg_hw_get_module_t)(const char *, const struct tg_hw_module **);
typedef const struct tg_native_handle *(*tg_get_native_handle_t)(
    const AHardwareBuffer *buffer);

struct tg_case {
    const char *name;
    uint32_t width;
    uint32_t height;
};

static uint64_t tg_read_u64_words(const int *data, int index)
{
    uint64_t value = 0;
    memcpy(&value, &data[index], sizeof(value));
    return value;
}

static const char *tg_vk_result(VkResult r)
{
    switch (r) {
    case VK_SUCCESS: return "VK_SUCCESS";
    case VK_NOT_READY: return "VK_NOT_READY";
    case VK_TIMEOUT: return "VK_TIMEOUT";
    case VK_ERROR_OUT_OF_HOST_MEMORY: return "VK_ERROR_OUT_OF_HOST_MEMORY";
    case VK_ERROR_OUT_OF_DEVICE_MEMORY: return "VK_ERROR_OUT_OF_DEVICE_MEMORY";
    case VK_ERROR_INITIALIZATION_FAILED: return "VK_ERROR_INITIALIZATION_FAILED";
    case VK_ERROR_DEVICE_LOST: return "VK_ERROR_DEVICE_LOST";
    case VK_ERROR_MEMORY_MAP_FAILED: return "VK_ERROR_MEMORY_MAP_FAILED";
    case VK_ERROR_EXTENSION_NOT_PRESENT: return "VK_ERROR_EXTENSION_NOT_PRESENT";
    case VK_ERROR_FEATURE_NOT_PRESENT: return "VK_ERROR_FEATURE_NOT_PRESENT";
    case VK_ERROR_FORMAT_NOT_SUPPORTED: return "VK_ERROR_FORMAT_NOT_SUPPORTED";
    default: return "VK_RESULT_OTHER";
    }
}

static int tg_has_device_extension(VkPhysicalDevice physical, const char *name)
{
    uint32_t count = 0;
    if (vkEnumerateDeviceExtensionProperties(physical, NULL, &count, NULL) != VK_SUCCESS)
        return 0;

    VkExtensionProperties *exts = calloc(count, sizeof(*exts));
    if (!exts)
        return 0;

    VkResult r = vkEnumerateDeviceExtensionProperties(physical, NULL, &count, exts);
    if (r != VK_SUCCESS) {
        free(exts);
        return 0;
    }

    int found = 0;
    for (uint32_t i = 0; i < count; ++i) {
        if (strcmp(exts[i].extensionName, name) == 0) {
            found = 1;
            break;
        }
    }
    free(exts);
    return found;
}

static int tg_choose_memory_type(uint32_t bits, uint32_t *index)
{
    for (uint32_t i = 0; i < 32; ++i) {
        if (bits & (1u << i)) {
            *index = i;
            return 0;
        }
    }
    return -1;
}

static const struct tg_gralloc_module *tg_load_gralloc(void)
{
    void *libhardware = dlopen("libhardware.so", RTLD_NOW | RTLD_LOCAL);
    if (!libhardware) {
        printf("gralloc.libhardware=DL_OPEN_FAIL:%s\n", dlerror());
        return NULL;
    }

    tg_hw_get_module_t hw_get_module = NULL;
    void *sym = dlsym(libhardware, "hw_get_module");
    memcpy(&hw_get_module, &sym, sizeof(hw_get_module));
    if (!hw_get_module) {
        printf("gralloc.hw_get_module=NULL\n");
        return NULL;
    }

    const struct tg_hw_module *module = NULL;
    int ret = hw_get_module("gralloc", &module);
    printf("gralloc.hw_get_module.ret=%d\n", ret);
    if (ret != 0 || !module)
        return NULL;

    printf("gralloc.module.name=%s\n", module->name ? module->name : "NULL");
    printf("gralloc.module.author=%s\n", module->author ? module->author : "NULL");
    return (const struct tg_gralloc_module *)module;
}

static tg_get_native_handle_t tg_load_native_handle_getter(void)
{
    void *nw = dlopen("libnativewindow.so", RTLD_NOW | RTLD_LOCAL);
    if (!nw) {
        printf("nativewindow.dlopen=FAIL:%s\n", dlerror());
        return NULL;
    }

    tg_get_native_handle_t fn = NULL;
    void *sym = dlsym(nw, "AHardwareBuffer_getNativeHandle");
    memcpy(&fn, &sym, sizeof(fn));
    printf("nativewindow.AHardwareBuffer_getNativeHandle=%s\n",
           fn ? "AVAILABLE" : "MISSING");
    return fn;
}

static void tg_dump_handle(const struct tg_case *tc,
                           const AHardwareBuffer_Desc *desc,
                           const struct tg_native_handle *h,
                           const struct tg_gralloc_module *gralloc)
{
    printf("%s.desc.width=%u\n", tc->name, desc->width);
    printf("%s.desc.height=%u\n", tc->name, desc->height);
    printf("%s.desc.layers=%u\n", tc->name, desc->layers);
    printf("%s.desc.format=0x%08x\n", tc->name, desc->format);
    printf("%s.desc.usage=0x%016" PRIx64 "\n", tc->name, desc->usage);
    printf("%s.desc.stride=%u\n", tc->name, desc->stride);

    if (!h) {
        printf("%s.handle=NULL\n", tc->name);
        return;
    }

    printf("%s.handle.version=%d\n", tc->name, h->version);
    printf("%s.handle.numFds=%d\n", tc->name, h->numFds);
    printf("%s.handle.numInts=%d\n", tc->name, h->numInts);

    for (int i = 0; i < h->numFds; ++i) {
        struct stat st;
        memset(&st, 0, sizeof(st));
        int rc = fstat(h->data[i], &st);
        printf("%s.fd[%d]=%d\n", tc->name, i, h->data[i]);
        if (rc == 0)
            printf("%s.fd[%d].size=%" PRIu64 "\n",
                   tc->name, i, (uint64_t)st.st_size);
    }

    if (h->numFds == 2 && h->numInts >= 22) {
        uint32_t flags = (uint32_t)h->data[TG_QTI_HANDLE_FLAGS_INDEX];
        uint32_t ubwc_bits = flags &
            (TG_QTI_FLAG_UBWC_ALIGNED | TG_QTI_FLAG_UBWC_ALIGNED_PI);
        printf("%s.handle.flags=0x%08x\n", tc->name, flags);
        printf("%s.handle.ubwc_flag_bits=0x%08x\n", tc->name, ubwc_bits);
        printf("%s.handle.ubwc_flag_bits_present=%s\n",
               tc->name, ubwc_bits ? "YES" : "NO");
        printf("%s.handle.aligned=%dx%d\n", tc->name,
               h->data[TG_QTI_HANDLE_WIDTH_INDEX],
               h->data[TG_QTI_HANDLE_HEIGHT_INDEX]);
        printf("%s.handle.unaligned=%dx%d\n", tc->name,
               h->data[TG_QTI_HANDLE_UNALIGNED_WIDTH_INDEX],
               h->data[TG_QTI_HANDLE_UNALIGNED_HEIGHT_INDEX]);
        printf("%s.handle.private_format=0x%08x\n", tc->name,
               (uint32_t)h->data[TG_QTI_HANDLE_FORMAT_INDEX]);
        printf("%s.handle.layer_count=%d\n", tc->name,
               h->data[TG_QTI_HANDLE_LAYER_COUNT_INDEX]);
        printf("%s.handle.usage64=0x%016" PRIx64 "\n", tc->name,
               tg_read_u64_words(h->data, TG_QTI_HANDLE_USAGE_INDEX));
        printf("%s.handle.declared_size=%u\n", tc->name,
               (uint32_t)h->data[TG_QTI_HANDLE_SIZE_INDEX]);
        printf("%s.handle.offset=%d\n", tc->name,
               h->data[TG_QTI_HANDLE_OFFSET_INDEX]);
        printf("%s.handle.base=0x%016" PRIx64 "\n", tc->name,
               tg_read_u64_words(h->data, TG_QTI_HANDLE_BASE_INDEX));
    }

    if (gralloc && gralloc->perform) {
        int ubwc = -1;
        int rc = gralloc->perform(gralloc,
                                  TG_GRALLOC_MODULE_PERFORM_GET_UBWC_FLAG,
                                  h, &ubwc);
        printf("%s.gralloc.GET_UBWC_FLAG.ret=%d\n", tc->name, rc);
        printf("%s.gralloc.GET_UBWC_FLAG.value=0x%08x\n",
               tc->name, (uint32_t)ubwc);
        printf("%s.gralloc.authoritative_ubwc=%s\n",
               tc->name, rc == 0 && ubwc != 0 ? "YES" : "NO");
        printf("%s.mesa26_qcom_expected_modifier=%s\n", tc->name,
               rc == 0 && ubwc != 0 ? "DRM_FORMAT_MOD_QCOM_COMPRESSED"
                                    : "DRM_FORMAT_MOD_LINEAR");
    } else {
        printf("%s.gralloc.GET_UBWC_FLAG.ret=UNAVAILABLE\n", tc->name);
    }
}

static int tg_run_case(VkPhysicalDevice physical,
                       VkDevice device,
                       PFN_vkGetAndroidHardwareBufferPropertiesANDROID get_ahb_props,
                       PFN_vkGetImageDrmFormatModifierPropertiesEXT get_modifier,
                       const struct tg_gralloc_module *gralloc,
                       tg_get_native_handle_t get_native,
                       const struct tg_case *tc)
{
    const uint64_t usage = AHARDWAREBUFFER_USAGE_GPU_FRAMEBUFFER |
                           AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE;
    AHardwareBuffer_Desc desc = {
        .width = tc->width,
        .height = tc->height,
        .layers = 1,
        .format = AHARDWAREBUFFER_FORMAT_R8G8B8A8_UNORM,
        .usage = usage,
    };

    AHardwareBuffer *ahb = NULL;
    int arc = AHardwareBuffer_allocate(&desc, &ahb);
    printf("\n========== %s ==========" "\n", tc->name);
    printf("%s.allocate.ret=%d\n", tc->name, arc);
    if (arc != 0 || !ahb)
        return 1;

    AHardwareBuffer_Desc actual = {0};
    AHardwareBuffer_describe(ahb, &actual);
    const struct tg_native_handle *h = get_native ? get_native(ahb) : NULL;
    tg_dump_handle(tc, &actual, h, gralloc);

    VkAndroidHardwareBufferFormatPropertiesANDROID format_props = {
        .sType = VK_STRUCTURE_TYPE_ANDROID_HARDWARE_BUFFER_FORMAT_PROPERTIES_ANDROID,
    };
    VkAndroidHardwareBufferPropertiesANDROID props = {
        .sType = VK_STRUCTURE_TYPE_ANDROID_HARDWARE_BUFFER_PROPERTIES_ANDROID,
        .pNext = &format_props,
    };
    VkResult vr = get_ahb_props(device, ahb, &props);
    printf("%s.vkGetAndroidHardwareBufferProperties=%d:%s\n",
           tc->name, vr, tg_vk_result(vr));
    if (vr != VK_SUCCESS) {
        AHardwareBuffer_release(ahb);
        return 1;
    }

    printf("%s.vk.allocationSize=%" PRIu64 "\n",
           tc->name, (uint64_t)props.allocationSize);
    printf("%s.vk.memoryTypeBits=0x%08x\n",
           tc->name, props.memoryTypeBits);
    printf("%s.vk.format=%d\n", tc->name, format_props.format);
    printf("%s.vk.externalFormat=0x%016" PRIx64 "\n",
           tc->name, format_props.externalFormat);
    printf("%s.vk.formatFeatures=0x%016" PRIx64 "\n",
           tc->name, (uint64_t)format_props.formatFeatures);

    VkExternalFormatANDROID external_format = {
        .sType = VK_STRUCTURE_TYPE_EXTERNAL_FORMAT_ANDROID,
        .externalFormat = format_props.externalFormat,
    };
    VkExternalMemoryImageCreateInfo external_mem = {
        .sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO,
        .pNext = format_props.format == VK_FORMAT_UNDEFINED
                     ? (const void *)&external_format : NULL,
        .handleTypes =
            VK_EXTERNAL_MEMORY_HANDLE_TYPE_ANDROID_HARDWARE_BUFFER_BIT_ANDROID,
    };
    VkImageCreateInfo image_info = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
        .pNext = &external_mem,
        .imageType = VK_IMAGE_TYPE_2D,
        .format = format_props.format,
        .extent = { actual.width, actual.height, 1 },
        .mipLevels = 1,
        .arrayLayers = 1,
        .samples = VK_SAMPLE_COUNT_1_BIT,
        .tiling = VK_IMAGE_TILING_OPTIMAL,
        .usage = VK_IMAGE_USAGE_SAMPLED_BIT |
                 VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
        .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED,
    };

    VkImage image = VK_NULL_HANDLE;
    vr = vkCreateImage(device, &image_info, NULL, &image);
    printf("%s.vkCreateImage=%d:%s\n", tc->name, vr, tg_vk_result(vr));
    if (vr != VK_SUCCESS) {
        AHardwareBuffer_release(ahb);
        return 1;
    }

    uint32_t memory_type = 0;
    if (tg_choose_memory_type(props.memoryTypeBits, &memory_type) != 0) {
        printf("%s.memory_type=NONE\n", tc->name);
        vkDestroyImage(device, image, NULL);
        AHardwareBuffer_release(ahb);
        return 1;
    }

    VkMemoryDedicatedAllocateInfo dedicated = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO,
        .image = image,
    };
    VkImportAndroidHardwareBufferInfoANDROID import = {
        .sType = VK_STRUCTURE_TYPE_IMPORT_ANDROID_HARDWARE_BUFFER_INFO_ANDROID,
        .pNext = &dedicated,
        .buffer = ahb,
    };
    VkMemoryAllocateInfo alloc = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .pNext = &import,
        .allocationSize = props.allocationSize,
        .memoryTypeIndex = memory_type,
    };

    VkDeviceMemory memory = VK_NULL_HANDLE;
    vr = vkAllocateMemory(device, &alloc, NULL, &memory);
    printf("%s.vkAllocateMemory=%d:%s\n", tc->name, vr, tg_vk_result(vr));
    if (vr != VK_SUCCESS) {
        vkDestroyImage(device, image, NULL);
        AHardwareBuffer_release(ahb);
        return 1;
    }

    vr = vkBindImageMemory(device, image, memory, 0);
    printf("%s.vkBindImageMemory=%d:%s\n", tc->name, vr, tg_vk_result(vr));

    if (get_modifier) {
        VkImageDrmFormatModifierPropertiesEXT modifier_props = {
            .sType = VK_STRUCTURE_TYPE_IMAGE_DRM_FORMAT_MODIFIER_PROPERTIES_EXT,
        };
        VkResult mr = get_modifier(device, image, &modifier_props);
        printf("%s.vkGetImageDrmFormatModifierPropertiesEXT=%d:%s\n",
               tc->name, mr, tg_vk_result(mr));
        if (mr == VK_SUCCESS) {
            printf("%s.vk.actual_drm_modifier=0x%016" PRIx64 "\n",
                   tc->name, modifier_props.drmFormatModifier);
            printf("%s.vk.actual_modifier_is_linear=%s\n",
                   tc->name,
                   modifier_props.drmFormatModifier == 0 ? "YES" : "NO");
        }
    } else {
        printf("%s.vkGetImageDrmFormatModifierPropertiesEXT=UNAVAILABLE\n",
               tc->name);
    }

    if (memory)
        vkFreeMemory(device, memory, NULL);
    if (image)
        vkDestroyImage(device, image, NULL);
    AHardwareBuffer_release(ahb);
    return vr == VK_SUCCESS ? 0 : 1;
}

int main(void)
{
    printf("========== touchGrass RGBA UBWC DIAGNOSTIC v0.33 ==========\n");
    printf("purpose=GPU-only RGBA framebuffer+sampled AHB compression/import audit\n");

    tg_get_native_handle_t get_native = tg_load_native_handle_getter();
    const struct tg_gralloc_module *gralloc = tg_load_gralloc();

    VkApplicationInfo app = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "touchGrass-rgba-ubwc-diag",
        .applicationVersion = 33,
        .pEngineName = "touchGrass",
        .engineVersion = 33,
        .apiVersion = VK_API_VERSION_1_1,
    };
    VkInstanceCreateInfo ici = {
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &app,
    };

    VkInstance instance = VK_NULL_HANDLE;
    VkResult vr = vkCreateInstance(&ici, NULL, &instance);
    printf("vkCreateInstance=%d:%s\n", vr, tg_vk_result(vr));
    if (vr != VK_SUCCESS)
        return 10;

    uint32_t phys_count = 0;
    vr = vkEnumeratePhysicalDevices(instance, &phys_count, NULL);
    if (vr != VK_SUCCESS || phys_count == 0) {
        printf("physical_devices=%u ret=%d\n", phys_count, vr);
        vkDestroyInstance(instance, NULL);
        return 11;
    }

    VkPhysicalDevice *phys = calloc(phys_count, sizeof(*phys));
    if (!phys) {
        vkDestroyInstance(instance, NULL);
        return 12;
    }
    vr = vkEnumeratePhysicalDevices(instance, &phys_count, phys);
    if (vr != VK_SUCCESS) {
        free(phys);
        vkDestroyInstance(instance, NULL);
        return 13;
    }
    VkPhysicalDevice physical = phys[0];
    free(phys);

    VkPhysicalDeviceProperties pdp;
    vkGetPhysicalDeviceProperties(physical, &pdp);
    printf("device_name=%s\n", pdp.deviceName);
    printf("device_api=%u.%u.%u\n",
           VK_VERSION_MAJOR(pdp.apiVersion),
           VK_VERSION_MINOR(pdp.apiVersion),
           VK_VERSION_PATCH(pdp.apiVersion));

    if (!tg_has_device_extension(
            physical,
            VK_ANDROID_EXTERNAL_MEMORY_ANDROID_HARDWARE_BUFFER_EXTENSION_NAME)) {
        printf("AHB_EXTENSION=MISSING\n");
        vkDestroyInstance(instance, NULL);
        return 14;
    }

    const int have_drm_modifier = tg_has_device_extension(
        physical, VK_EXT_IMAGE_DRM_FORMAT_MODIFIER_EXTENSION_NAME);
    printf("VK_EXT_image_drm_format_modifier=%s\n",
           have_drm_modifier ? "YES" : "NO");

    uint32_t qcount = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &qcount, NULL);
    VkQueueFamilyProperties *qprops = calloc(qcount, sizeof(*qprops));
    if (!qprops) {
        vkDestroyInstance(instance, NULL);
        return 15;
    }
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &qcount, qprops);
    uint32_t qfam = UINT32_MAX;
    for (uint32_t i = 0; i < qcount; ++i) {
        if (qprops[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) {
            qfam = i;
            break;
        }
    }
    free(qprops);
    if (qfam == UINT32_MAX) {
        printf("graphics_queue=NONE\n");
        vkDestroyInstance(instance, NULL);
        return 16;
    }

    const char *exts[2];
    uint32_t ext_count = 0;
    exts[ext_count++] =
        VK_ANDROID_EXTERNAL_MEMORY_ANDROID_HARDWARE_BUFFER_EXTENSION_NAME;
    if (have_drm_modifier)
        exts[ext_count++] = VK_EXT_IMAGE_DRM_FORMAT_MODIFIER_EXTENSION_NAME;

    float priority = 1.0f;
    VkDeviceQueueCreateInfo qci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = qfam,
        .queueCount = 1,
        .pQueuePriorities = &priority,
    };
    VkDeviceCreateInfo dci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .queueCreateInfoCount = 1,
        .pQueueCreateInfos = &qci,
        .enabledExtensionCount = ext_count,
        .ppEnabledExtensionNames = exts,
    };

    VkDevice device = VK_NULL_HANDLE;
    vr = vkCreateDevice(physical, &dci, NULL, &device);
    printf("vkCreateDevice=%d:%s\n", vr, tg_vk_result(vr));
    if (vr != VK_SUCCESS) {
        vkDestroyInstance(instance, NULL);
        return 17;
    }

    PFN_vkGetAndroidHardwareBufferPropertiesANDROID get_ahb_props =
        (PFN_vkGetAndroidHardwareBufferPropertiesANDROID)
        vkGetDeviceProcAddr(device, "vkGetAndroidHardwareBufferPropertiesANDROID");
    PFN_vkGetImageDrmFormatModifierPropertiesEXT get_modifier = NULL;
    if (have_drm_modifier) {
        get_modifier = (PFN_vkGetImageDrmFormatModifierPropertiesEXT)
            vkGetDeviceProcAddr(device,
                                "vkGetImageDrmFormatModifierPropertiesEXT");
    }

    if (!get_ahb_props) {
        printf("vkGetAndroidHardwareBufferPropertiesANDROID=NULL\n");
        vkDestroyDevice(device, NULL);
        vkDestroyInstance(instance, NULL);
        return 18;
    }

    const struct tg_case cases[] = {
        { "rgba_gpu_256x256", 256, 256 },
        { "rgba_gpu_940x1670", 940, 1670 },
        { "rgba_gpu_1080x2400", 1080, 2400 },
    };

    unsigned pass = 0;
    unsigned fail = 0;
    for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
        int rc = tg_run_case(physical, device, get_ahb_props, get_modifier,
                             gralloc, get_native, &cases[i]);
        if (rc == 0)
            ++pass;
        else
            ++fail;
    }

    printf("\nrgba_ubwc_cases_pass=%u\n", pass);
    printf("rgba_ubwc_cases_fail=%u\n", fail);
    printf("RGBA_UBWC_DIAG_STATUS=%s\n", fail == 0 ? "COMPLETE" : "FAIL");

    vkDeviceWaitIdle(device);
    vkDestroyDevice(device, NULL);
    vkDestroyInstance(instance, NULL);
    return fail == 0 ? 0 : 20;
}
