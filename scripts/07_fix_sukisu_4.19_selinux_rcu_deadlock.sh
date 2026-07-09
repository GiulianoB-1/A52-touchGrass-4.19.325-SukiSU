#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.153
SUKISU_DIR="$KERNEL_DIR/KernelSU"
RULES_C="$SUKISU_DIR/kernel/selinux/rules.c"
PATCH_OUT="$ARTIFACTS_DIR/sukisu-linux-4.19-selinux-rcu-deadlock.patch"
REPORT="$ARTIFACTS_DIR/sukisu-linux-4.19-selinux-rcu-deadlock.txt"

test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before SELinux RCU deadlock fix"
test -f "$RULES_C" || fail "SukiSU rules.c is missing"
test "$(git -C "$SUKISU_DIR" rev-parse HEAD)" = "$SUKISU_COMMIT" || fail "SukiSU source is not at the pinned commit"
grep -Fq 'rwlock_t policy_rwlock;' "$KERNEL_DIR/security/selinux/ss/services.h" || fail "Legacy SELinux policy rwlock is missing"

before=$(mktemp)
trap 'rm -f "$before"' EXIT
cp "$RULES_C" "$before"

info "Replacing unsafe sleep-under-RCU SELinux policy mutation with policy_rwlock"
python3 - "$RULES_C" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one exact match, found {count}")
    return source.replace(old, new, 1)


apply_lookup_old = '''    rcu_read_lock();
    ss = rcu_dereference(selinux_state.ss);
    if (!ss) {
        pr_err("SELinux security server is unavailable\\n");
        rcu_read_unlock();
        return;
    }
    db = &ss->policydb;
    backup_sepolicy = NULL;
'''
apply_lookup_new = '''    /*
     * Android/Linux 4.19 keeps the live policydb in selinux_state.ss.
     * The security-server object is stable after SELinux initialization;
     * serialize in-place policy mutation with its legacy policy_rwlock.
     */
    ss = rcu_dereference_protected(selinux_state.ss, 1);
    if (!ss) {
        pr_err("SELinux security server is unavailable\\n");
        return;
    }
    db = &ss->policydb;
    backup_sepolicy = NULL;

    write_lock(&ss->policy_rwlock);
'''
text = replace_once(text, apply_lookup_old, apply_lookup_new,
                    "apply_kernelsu_rules lookup")

apply_flush_old = '''    reset_avc_cache();
    rcu_read_unlock();
}
'''
apply_flush_new = '''    write_unlock(&ss->policy_rwlock);

    /* AVC reset may call synchronize_net()/synchronize_rcu() and sleep. */
    smp_mb();
    reset_avc_cache();
}
'''
text = replace_once(text, apply_flush_old, apply_flush_new,
                    "apply_kernelsu_rules flush")

handle_lookup_old = '''    rcu_read_lock();
    ss = rcu_dereference(selinux_state.ss);
    if (!ss) {
        ret = -ENODEV;
        goto out_rcu;
    }
    db = &ss->policydb;

    cursor.cur = payload;
'''
handle_lookup_new = '''    ss = rcu_dereference_protected(selinux_state.ss, 1);
    if (!ss) {
        ret = -ENODEV;
        goto out_free;
    }
    db = &ss->policydb;

    write_lock(&ss->policy_rwlock);

    cursor.cur = payload;
'''
text = replace_once(text, handle_lookup_old, handle_lookup_new,
                    "handle_sepolicy lookup")

handle_flush_old = '''    reset_avc_cache();
    ret = success_cmd_count;

out_rcu:
    rcu_read_unlock();
out_free:
'''
handle_flush_new = '''    write_unlock(&ss->policy_rwlock);

    /* Flush only after leaving the non-sleepable policy write section. */
    smp_mb();
    reset_avc_cache();
    ret = success_cmd_count;

out_free:
'''
text = replace_once(text, handle_flush_old, handle_flush_new,
                    "handle_sepolicy flush")

# Function-local fail-closed validation.
apply_start = text.index('void apply_kernelsu_rules()')
apply_end = text.index('#define KSU_SEPOLICY_MAX_BATCH_SIZE', apply_start)
apply_body = text[apply_start:apply_end]
handle_start = text.index('int handle_sepolicy(void __user *user_data, u64 data_len)')
handle_body = text[handle_start:]

for name, body in (("apply_kernelsu_rules", apply_body),
                   ("handle_sepolicy", handle_body)):
    if 'rcu_read_lock();' in body or 'rcu_read_unlock();' in body:
        raise SystemExit(f"{name}: unsafe RCU read-side section remains")
    if body.count('write_lock(&ss->policy_rwlock);') != 1:
        raise SystemExit(f"{name}: expected exactly one policy write lock")
    if body.count('write_unlock(&ss->policy_rwlock);') != 1:
        raise SystemExit(f"{name}: expected exactly one policy write unlock")
    unlock = body.index('write_unlock(&ss->policy_rwlock);')
    reset = body.index('reset_avc_cache();')
    if reset < unlock:
        raise SystemExit(f"{name}: AVC reset still occurs while policy lock is held")
    if 'smp_mb();' not in body[unlock:reset]:
        raise SystemExit(f"{name}: memory barrier missing before AVC reset")

path.write_text(text)
PY

# Static guards against the exact boot-deadlock signature seen in pstore.
! grep -Fq 'out_rcu:' "$RULES_C" || fail "Legacy RCU cleanup label remains in rules.c"
! grep -Fq 'rcu_read_lock();' "$RULES_C" || fail "Unsafe RCU read lock remains in rules.c"
! grep -Fq 'rcu_read_unlock();' "$RULES_C" || fail "Unsafe RCU read unlock remains in rules.c"
test "$(grep -Fc 'write_lock(&ss->policy_rwlock);' "$RULES_C")" -eq 2 || fail "Expected two SELinux policy write locks"
test "$(grep -Fc 'write_unlock(&ss->policy_rwlock);' "$RULES_C")" -eq 2 || fail "Expected two SELinux policy write unlocks"
test "$(grep -Fc 'smp_mb();' "$RULES_C")" -ge 2 || fail "Expected memory barriers before AVC cache resets"

git -C "$SUKISU_DIR" diff --check
set +e
diff -u --label a/kernel/selinux/rules.c --label b/kernel/selinux/rules.c \
  "$before" "$RULES_C" > "$PATCH_OUT"
diff_rc=$?
set -e
test "$diff_rc" -eq 1 || fail "Could not produce SELinux RCU deadlock patch"
test -s "$PATCH_OUT" || fail "SELinux RCU deadlock patch is empty"
sha256sum "$PATCH_OUT" > "$PATCH_OUT.sha256"

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'sukisu_commit=%s\n' "$(git -C "$SUKISU_DIR" rev-parse HEAD)"
  printf 'boot_failure_signature=init-apply_kernelsu_rules-avc_ss_reset-synchronize_rcu\n'
  printf 'policy_mutation_lock=selinux_state.ss-policy_rwlock\n'
  printf 'rcu_read_side_policy_mutation=no\n'
  printf 'avc_reset_after_write_unlock=yes\n'
  printf 'fixed_functions=apply_kernelsu_rules,handle_sepolicy\n'
  printf 'patch_sha256=%s\n' "$(cut -d' ' -f1 "$PATCH_OUT.sha256")"
} | tee "$REPORT"

info "SukiSU Linux 4.19 SELinux RCU deadlock fix applied"
