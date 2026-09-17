#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${1:-$PWD}"
NDK="${2:-${ANDROID_NDK_ROOT:-}}"
OUT="${3:-$ROOT/artifacts/turnip-mesa-26.2.2-v035}"
BASE="$ROOT/scripts/92_build_turnip_mesa_26_2_2.sh"
TMP="$ROOT/.tmp-95-build-turnip-autotune-directlog.sh"

[ -f "$BASE" ] || { echo "missing base build script: $BASE" >&2; exit 2; }
cp "$BASE" "$TMP"
trap 'rm -f "$TMP"' EXIT

python3 - "$TMP" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
text = p.read_text()

anchor = 'TOOLCHAIN="$NDK/toolchains/llvm/prebuilt/linux-x86_64"\n'
if text.count(anchor) != 1:
    raise SystemExit(f"v0.35 injection anchor count: {text.count(anchor)}")

inject = r'''echo "==> Enable v0.35 direct Android autotune diagnostics"
python3 - "$SRC" <<'PYDIAG'
from pathlib import Path
import sys

src = Path(sys.argv[1])
p = src / "src/freedreno/vulkan/tu_autotune.cc"
text = p.read_text()


def one(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"v0.35 {label}: expected 1 anchor, found {count}")
    text = text.replace(old, new, 1)

# Bypass Mesa's logger completely.  The Turnip HAL already links liblog.so,
# so direct __android_log_print() calls provide an independent Android logcat
# path for this diagnostic build.
one(
    '#include "tu_autotune.h"\n',
    '#include "tu_autotune.h"\n\n#include <android/log.h>\n',
    'android log include',
)

# Keep the diagnostic bounded: first 256 render-mode calls and first 256
# bandwidth decisions per process are enough to characterize Settings UI.
marker = '#define TU_AUTOTUNE_FLUSH_AT_FINISH 0\n'
one(
    marker,
    marker + '''\nstatic std::atomic<uint32_t> tg_diag_call_count { 0 };\nstatic std::atomic<uint32_t> tg_diag_bw_count { 0 };\n''',
    'diagnostic counters',
)

# Heartbeat: proves the autotuner constructor itself is reached, and prints the
# actual parsed algorithm/flags without relying on Mesa's logger.
ctor = '''tu_autotune::tu_autotune(struct tu_device *device, VkResult &result)\n   : device(device), supported_mod_flags(get_supported_mod_flags(device)), active_config(get_env_config())\n{\n'''
one(
    ctor,
    ctor + '''   std::string tg_cfg = active_config.load().to_string();\n   __android_log_print(ANDROID_LOG_WARN, "TGAT", "TGAT_INIT %s", tg_cfg.c_str());\n''',
    'constructor heartbeat',
)

# Log every entry (bounded) before any early return.  This is the key signal
# missing from v0.34a: it tells us whether Android UI renderpasses reach the
# selector at all, how many draws they contain, and whether tune_small is set.
entry = '''   cmd_buf_ctx &cb_ctx = cmd_buffer->autotune_ctx;\n   config_t config = active_config.load();\n\n'''
one(
    entry,
    entry + '''   const uint32_t tg_seq = tg_diag_call_count.fetch_add(1, std::memory_order_relaxed);\n   const bool tg_emit = tg_seq < 256;\n   if (tg_emit) {\n      __android_log_print(ANDROID_LOG_WARN, "TGAT",\n                          "TGAT_CALL seq=%" PRIu32 " draws=%" PRIu32 " enabled=%u simultaneous=%u tune_small=%u",\n                          tg_seq, rp_state->drawcall_count, enabled ? 1U : 0U,\n                          (cmd_buffer->usage_flags & VK_COMMAND_BUFFER_USAGE_SIMULTANEOUS_USE_BIT) ? 1U : 0U,\n                          config.test(mod_flag::TUNE_SMALL) ? 1U : 0U);\n   }\n\n''',
    'selector entry',
)

# Preserve behavior exactly while making every major early-return reason
# visible.  These are diagnostics only; returned modes are unchanged.
one(
    '''   if (rp_state->sysmem_single_prim_mode)\n      return render_mode::GMEM;\n''',
    '''   if (rp_state->sysmem_single_prim_mode) {\n      if (tg_emit)\n         __android_log_print(ANDROID_LOG_WARN, "TGAT", "TGAT_DECISION seq=%" PRIu32 " reason=sysmem_single_prim mode=GMEM", tg_seq);\n      return render_mode::GMEM;\n   }\n''',
    'single prim decision',
)
one(
    '''   if (pass->has_fdm)\n      return render_mode::GMEM;\n''',
    '''   if (pass->has_fdm) {\n      if (tg_emit)\n         __android_log_print(ANDROID_LOG_WARN, "TGAT", "TGAT_DECISION seq=%" PRIu32 " reason=fdm mode=GMEM", tg_seq);\n      return render_mode::GMEM;\n   }\n''',
    'fdm decision',
)
one(
    '''   if (pass->has_msrtss)\n      return render_mode::GMEM;\n''',
    '''   if (pass->has_msrtss) {\n      if (tg_emit)\n         __android_log_print(ANDROID_LOG_WARN, "TGAT", "TGAT_DECISION seq=%" PRIu32 " reason=msrtss mode=GMEM", tg_seq);\n      return render_mode::GMEM;\n   }\n''',
    'msrtss decision',
)
one(
    '''   if (!enabled || simultaneous_use || ignore_small_rp)\n      return default_mode;\n''',
    '''   if (!enabled || simultaneous_use || ignore_small_rp) {\n      if (tg_emit) {\n         const char *tg_reason = !enabled ? "disabled" : (simultaneous_use ? "simultaneous" : "small_rp");\n         __android_log_print(ANDROID_LOG_WARN, "TGAT",\n                             "TGAT_DECISION seq=%" PRIu32 " reason=%s draws=%" PRIu32 " mode=SYSMEM",\n                             tg_seq, tg_reason, rp_state->drawcall_count);\n      }\n      return default_mode;\n   }\n''',
    'default early decision',
)

# Early/late forced-mode decisions (prefer_gmem/prefer_sysmem/big_gmem).
one(
    '''   if (can_early_return && early_return_mode) {\n      at_log_base_h("%" PRIu32 " draw calls, using %s (early)",\n                    key_opt ? key_opt->hash : rp_key(pass, framebuffer, cmd_buffer).hash, rp_state->drawcall_count,\n                    render_mode_str(*early_return_mode));\n      return *early_return_mode;\n   }\n''',
    '''   if (can_early_return && early_return_mode) {\n      at_log_base_h("%" PRIu32 " draw calls, using %s (early)",\n                    key_opt ? key_opt->hash : rp_key(pass, framebuffer, cmd_buffer).hash, rp_state->drawcall_count,\n                    render_mode_str(*early_return_mode));\n      if (tg_emit)\n         __android_log_print(ANDROID_LOG_WARN, "TGAT",\n                             "TGAT_DECISION seq=%" PRIu32 " reason=forced_early draws=%" PRIu32 " mode=%s",\n                             tg_seq, rp_state->drawcall_count, render_mode_str(*early_return_mode));\n      return *early_return_mode;\n   }\n''',
    'forced early decision',
)
one(
    '''   if (config.test(mod_flag::PREEMPT_OPTIMIZE) && history.preempt_optimize.is_latency_sensitive()) {\n      /* Try to mitigate the risk of high preemption latency by always using GMEM, which should break up any larger\n       * draws into smaller ones with tiling.\n       */\n      at_log_base_h("high preemption latency risk, using GMEM", key.hash);\n      return render_mode::GMEM;\n   }\n''',
    '''   if (config.test(mod_flag::PREEMPT_OPTIMIZE) && history.preempt_optimize.is_latency_sensitive()) {\n      /* Try to mitigate the risk of high preemption latency by always using GMEM, which should break up any larger\n       * draws into smaller ones with tiling.\n       */\n      at_log_base_h("high preemption latency risk, using GMEM", key.hash);\n      if (tg_emit)\n         __android_log_print(ANDROID_LOG_WARN, "TGAT", "TGAT_DECISION seq=%" PRIu32 " reason=preempt mode=GMEM", tg_seq);\n      return render_mode::GMEM;\n   }\n''',
    'preempt decision',
)
one(
    '''   if (early_return_mode) {\n      at_log_base_h("%" PRIu32 " draw calls, using %s (late)", key.hash, rp_state->drawcall_count,\n                    render_mode_str(*early_return_mode));\n      return *early_return_mode;\n   }\n''',
    '''   if (early_return_mode) {\n      at_log_base_h("%" PRIu32 " draw calls, using %s (late)", key.hash, rp_state->drawcall_count,\n                    render_mode_str(*early_return_mode));\n      if (tg_emit)\n         __android_log_print(ANDROID_LOG_WARN, "TGAT",\n                             "TGAT_DECISION seq=%" PRIu32 " reason=forced_late draws=%" PRIu32 " mode=%s",\n                             tg_seq, rp_state->drawcall_count, render_mode_str(*early_return_mode));\n      return *early_return_mode;\n   }\n''',
    'forced late decision',
)

# Log the actual BANDWIDTH decision independently of the caller's CALL cap.
bw = '''      bool select_sysmem = sysmem_bandwidth <= gmem_bandwidth;\n      render_mode mode = select_sysmem ? render_mode::SYSMEM : render_mode::GMEM;\n\n'''
one(
    bw,
    bw + '''      const uint32_t tg_bw_seq = tg_diag_bw_count.fetch_add(1, std::memory_order_relaxed);\n      if (tg_bw_seq < 256) {\n         __android_log_print(ANDROID_LOG_WARN, "TGAT",\n                             "TGAT_BW seq=%" PRIu32 " hash=%016" PRIx64 " draws=%" PRIu32 " mode=%s mean_samples=%" PRIu64 " sys_bw=%" PRIu64 " gmem_bw=%" PRIu64,\n                             tg_bw_seq, history.hash, rp_state->drawcall_count, render_mode_str(mode), mean_samples,\n                             sysmem_bandwidth, gmem_bandwidth);\n      }\n\n''',
    'bandwidth decision',
)

p.write_text(text)

patched = p.read_text()
for needle in (
    '#include <android/log.h>',
    'TGAT_INIT %s',
    'TGAT_CALL seq=',
    'TGAT_DECISION seq=',
    'TGAT_BW seq=',
    '__android_log_print(ANDROID_LOG_WARN, "TGAT"',
):
    if needle not in patched:
        raise SystemExit(f"v0.35 direct-log audit missing: {needle}")

print("source_audit=Turnip direct Android autotune heartbeat:PASS")
print("source_audit=Turnip direct Android renderpass decisions:PASS")
print("source_audit=Turnip direct Android bandwidth decisions:PASS")
PYDIAG

'''
text = text.replace(anchor, inject + anchor, 1)

old_meta = 'runtime_logging=errors-warnings-only-no-bringup-success-traces\n'
new_meta = 'runtime_logging=autotune-direct-android-liblog-diagnostic\n'
if text.count(old_meta) != 1:
    raise SystemExit(f"runtime logging metadata anchor count: {text.count(old_meta)}")
text = text.replace(old_meta, new_meta, 1)

mode_anchor = 'kgsl_sync_merge=fixed-mixed-ts-syncfd-and-cross-queue-ts\n'
if text.count(mode_anchor) != 1:
    raise SystemExit(f"BUILD-INFO diagnostic anchor count: {text.count(mode_anchor)}")
text = text.replace(
    mode_anchor,
    mode_anchor + 'autotune_diagnostics=direct-liblog-init-call-decision-bandwidth\n',
    1,
)

p.write_text(text)
PY

chmod +x "$TMP"
exec "$TMP" "$ROOT" "$NDK" "$OUT"
