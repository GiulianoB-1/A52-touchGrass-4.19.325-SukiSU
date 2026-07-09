#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.153
SUKISU_DIR="$KERNEL_DIR/KernelSU"
RULES_C="$SUKISU_DIR/kernel/selinux/rules.c"
SEPOLICY_C="$SUKISU_DIR/kernel/selinux/sepolicy.c"
SELINUX_HIDE_C="$SUKISU_DIR/kernel/feature/selinux_hide.c"
SULOG_EVENT_C="$SUKISU_DIR/kernel/sulog/event.c"
PATCH_OUT="$ARTIFACTS_DIR/sukisu-linux-4.19-legacy-sepolicy.patch"
REPORT="$ARTIFACTS_DIR/sukisu-linux-4.19-legacy-sepolicy.txt"

test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before legacy SELinux compatibility patch"
for file in "$RULES_C" "$SEPOLICY_C" "$SELINUX_HIDE_C" "$SULOG_EVENT_C"; do
  test -f "$file" || fail "Required SukiSU source is missing: $file"
done
test "$(git -C "$SUKISU_DIR" rev-parse HEAD)" = "$SUKISU_COMMIT" || fail "SukiSU source is not at the pinned commit"

# Fail closed against the exact Android/Linux 4.19 SELinux representation.
grep -Fq 'struct selinux_ss *ss;' "$KERNEL_DIR/security/selinux/include/security.h" || fail "selinux_state.ss is missing"
grep -Fq 'struct policydb policydb;' "$KERNEL_DIR/security/selinux/ss/services.h" || fail "Legacy selinux_ss policydb is missing"
grep -Fq 'rwlock_t policy_rwlock;' "$KERNEL_DIR/security/selinux/ss/services.h" || fail "Legacy SELinux policy rwlock is missing"
grep -Fq 'struct flex_array *htable;' "$KERNEL_DIR/security/selinux/ss/avtab.h" || fail "Legacy flex-array avtab is missing"
grep -Fq 'struct flex_array *type_val_to_struct_array;' "$KERNEL_DIR/security/selinux/ss/policydb.h" || fail "Legacy type-value flex array is missing"
grep -Fq 'struct flex_array *type_attr_map_array;' "$KERNEL_DIR/security/selinux/ss/policydb.h" || fail "Legacy type-attribute flex array is missing"
grep -Fq 'struct filename_trans {' "$KERNEL_DIR/security/selinux/ss/policydb.h" || fail "Legacy filename transition key is missing"
grep -Fq 'struct hashtab *filename_trans;' "$KERNEL_DIR/security/selinux/ss/policydb.h" || fail "Legacy filename transition table is missing"
test ! -e "$KERNEL_DIR/include/linux/minmax.h" || fail "Unexpected linux/minmax.h exists; review patch"
grep -Fq '#define min_t' "$KERNEL_DIR/include/linux/kernel.h" || fail "Legacy min_t definition is missing"

info "Adapting SukiSU SELinux policy mutation to the Android/Linux 4.19 in-memory policydb"
python3 - "$RULES_C" "$SEPOLICY_C" "$SELINUX_HIDE_C" "$SULOG_EVENT_C" <<'PY'
from pathlib import Path
import sys

rules_path, sepolicy_path, hide_path, sulog_path = map(Path, sys.argv[1:])


def replace_between(text: str, start: str, end: str, replacement: str, label: str) -> str:
    start_pos = text.find(start)
    if start_pos < 0:
        raise SystemExit(f"{label}: start marker not found")
    end_pos = text.find(end, start_pos)
    if end_pos < 0:
        raise SystemExit(f"{label}: end marker not found")
    return text[:start_pos] + replacement + text[end_pos:]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# rules.c: Linux 4.19 stores the active policydb inside selinux_state.ss.
# Mutate that established policydb in place, matching the legacy KernelSU path.
# Parse the full manager batch before applying anything so malformed batches
# cannot partially modify the live policy.
# ---------------------------------------------------------------------------
rules = rules_path.read_text()

legacy_apply = r'''void apply_kernelsu_rules()
{
    struct selinux_ss *ss;
    struct policydb *db;

    if (!getenforce()) {
        pr_info("SELinux permissive or disabled, apply rules!\n");
    }

    rcu_read_lock();
    ss = rcu_dereference(selinux_state.ss);
    if (!ss) {
        pr_err("SELinux security server is unavailable\n");
        rcu_read_unlock();
        return;
    }
    db = &ss->policydb;
    backup_sepolicy = NULL;

    ksu_type(db, KERNEL_SU_DOMAIN, "domain");
    ksu_permissive(db, KERNEL_SU_DOMAIN);
    ksu_typeattribute(db, KERNEL_SU_DOMAIN, "mlstrustedsubject");
    ksu_typeattribute(db, KERNEL_SU_DOMAIN, "netdomain");
    ksu_typeattribute(db, KERNEL_SU_DOMAIN, "bluetoothdomain");

    ksu_type(db, KERNEL_SU_FILE, "file_type");
    ksu_typeattribute(db, KERNEL_SU_FILE, "mlstrustedobject");
    ksu_allow(db, "domain", KERNEL_SU_FILE, ALL, ALL);

    ksu_allow(db, KERNEL_SU_DOMAIN, ALL, ALL, ALL);

    if (db->policyvers >= POLICYDB_VERSION_XPERMS_IOCTL) {
        ksu_allowxperm(db, KERNEL_SU_DOMAIN, ALL, "blk_file", ALL);
        ksu_allowxperm(db, KERNEL_SU_DOMAIN, ALL, "fifo_file", ALL);
        ksu_allowxperm(db, KERNEL_SU_DOMAIN, ALL, "chr_file", ALL);
        ksu_allowxperm(db, KERNEL_SU_DOMAIN, ALL, "file", ALL);
    }

    ksu_allow(db, "init", KERNEL_SU_DOMAIN, ALL, ALL);

    ksu_allow(db, "servicemanager", KERNEL_SU_DOMAIN, "dir", "search");
    ksu_allow(db, "servicemanager", KERNEL_SU_DOMAIN, "dir", "read");
    ksu_allow(db, "servicemanager", KERNEL_SU_DOMAIN, "file", "open");
    ksu_allow(db, "servicemanager", KERNEL_SU_DOMAIN, "file", "read");
    ksu_allow(db, "servicemanager", KERNEL_SU_DOMAIN, "process", "getattr");
    ksu_allow(db, "domain", KERNEL_SU_DOMAIN, "process", "sigchld");

    ksu_allow(db, "logd", KERNEL_SU_DOMAIN, "dir", "search");
    ksu_allow(db, "logd", KERNEL_SU_DOMAIN, "file", "read");
    ksu_allow(db, "logd", KERNEL_SU_DOMAIN, "file", "open");
    ksu_allow(db, "logd", KERNEL_SU_DOMAIN, "file", "getattr");

    ksu_allow(db, "domain", KERNEL_SU_DOMAIN, "fd", "use");
    ksu_allow(db, "domain", KERNEL_SU_DOMAIN, "fifo_file", "write");
    ksu_allow(db, "domain", KERNEL_SU_DOMAIN, "fifo_file", "read");
    ksu_allow(db, "domain", KERNEL_SU_DOMAIN, "fifo_file", "open");
    ksu_allow(db, "domain", KERNEL_SU_DOMAIN, "fifo_file", "getattr");
    ksu_allow(db, "domain", KERNEL_SU_DOMAIN, "unix_stream_socket", "read");
    ksu_allow(db, "domain", KERNEL_SU_DOMAIN, "unix_stream_socket", "write");
    ksu_allow(db, "domain", KERNEL_SU_DOMAIN, "unix_stream_socket", "connectto");
    ksu_allow(db, "domain", KERNEL_SU_DOMAIN, "unix_stream_socket", "getopt");
    ksu_allow(db, "domain", KERNEL_SU_DOMAIN, "unix_stream_socket", "getattr");

    ksu_allow(db, "domain", KERNEL_SU_DOMAIN, "memfd_file", "execute");
    ksu_allow(db, "domain", KERNEL_SU_DOMAIN, "memfd_file", "getattr");
    ksu_allow(db, "domain", KERNEL_SU_DOMAIN, "memfd_file", "map");
    ksu_allow(db, "domain", KERNEL_SU_DOMAIN, "memfd_file", "read");
    ksu_allow(db, "domain", KERNEL_SU_DOMAIN, "memfd_file", "write");

    ksu_allow(db, "hwservicemanager", KERNEL_SU_DOMAIN, "dir", "search");
    ksu_allow(db, "hwservicemanager", KERNEL_SU_DOMAIN, "file", "read");
    ksu_allow(db, "hwservicemanager", KERNEL_SU_DOMAIN, "file", "open");
    ksu_allow(db, "hwservicemanager", KERNEL_SU_DOMAIN, "process", "getattr");

    ksu_allow(db, "domain", KERNEL_SU_DOMAIN, "binder", ALL);
    ksu_allow(db, "system_server", KERNEL_SU_DOMAIN, "process", "getpgid");
    ksu_allow(db, "system_server", KERNEL_SU_DOMAIN, "process", "sigkill");

    reset_avc_cache();
    rcu_read_unlock();
}

'''
rules = replace_between(
    rules,
    'void apply_kernelsu_rules()\n',
    '#define KSU_SEPOLICY_MAX_BATCH_SIZE',
    legacy_apply,
    'rules.c apply_kernelsu_rules',
)

legacy_handle = r'''static int validate_sepolicy_batch(const u8 *payload, size_t payload_len)
{
    struct sepol_batch_cursor cursor;
    u32 cmd_index = 0;

    cursor.cur = payload;
    cursor.end = payload + payload_len;

    while (cursor.cur < cursor.end) {
        struct sepol_data header;
        const char *arg;
        int expected_argc;
        int ret;
        int i;

        ret = sepol_read_cmd_header(&cursor, &header);
        if (ret < 0)
            return ret;

        expected_argc = sepol_expected_argc(header.cmd);
        if (expected_argc < 0 || expected_argc > KSU_SEPOLICY_MAX_ARGS)
            return -EINVAL;

        for (i = 0; i < expected_argc; i++) {
            ret = sepol_read_string(&cursor, &arg);
            if (ret < 0)
                return ret;
        }
        cmd_index++;
    }

    return cursor.cur == cursor.end ? 0 : -EINVAL;
}

int handle_sepolicy(void __user *user_data, u64 data_len)
{
    struct selinux_ss *ss;
    struct policydb *db;
    struct sepol_batch_cursor cursor;
    u8 *payload;
    int ret;
    int success_cmd_count = 0;
    u32 cmd_index = 0;

    if (!user_data || !data_len)
        return -EINVAL;
    if (data_len > KSU_SEPOLICY_MAX_BATCH_SIZE)
        return -E2BIG;

    payload = kvmalloc((size_t)data_len, GFP_KERNEL);
    if (!payload)
        return -ENOMEM;

    if (copy_from_user(payload, user_data, (size_t)data_len)) {
        ret = -EFAULT;
        goto out_free;
    }

    ret = validate_sepolicy_batch(payload, (size_t)data_len);
    if (ret < 0) {
        pr_err("sepol: malformed batch rejected before policy mutation: %d\n", ret);
        goto out_free;
    }

    if (!getenforce())
        pr_info("SELinux permissive or disabled when handling policy!\n");

    rcu_read_lock();
    ss = rcu_dereference(selinux_state.ss);
    if (!ss) {
        ret = -ENODEV;
        goto out_rcu;
    }
    db = &ss->policydb;

    cursor.cur = payload;
    cursor.end = payload + (size_t)data_len;

    while (cursor.cur < cursor.end) {
        struct sepol_data header;
        const char *args[KSU_SEPOLICY_MAX_ARGS] = { 0 };
        int expected_argc;
        u32 arg_index;

        ret = sepol_read_cmd_header(&cursor, &header);
        if (ret < 0)
            break;

        expected_argc = sepol_expected_argc(header.cmd);
        if (expected_argc < 0 || expected_argc > KSU_SEPOLICY_MAX_ARGS) {
            ret = -EINVAL;
            break;
        }

        for (arg_index = 0; arg_index < (u32)expected_argc; arg_index++) {
            ret = sepol_read_string(&cursor, &args[arg_index]);
            if (ret < 0)
                break;
        }
        if (ret < 0)
            break;

        ret = apply_one_sepolicy_cmd(db, &header, args);
        if (ret < 0) {
            pr_err("sepol: cmd #%u failed, cmd=%u subcmd=%u.\n",
                   cmd_index, header.cmd, header.subcmd);
        } else {
            success_cmd_count++;
        }
        cmd_index++;
    }

    reset_avc_cache();
    ret = success_cmd_count;

out_rcu:
    rcu_read_unlock();
out_free:
    kvfree(payload);
    return ret;
}
'''
handle_start = rules.find('int handle_sepolicy(void __user *user_data, u64 data_len)')
if handle_start < 0:
    raise SystemExit('rules.c handle_sepolicy marker not found')
rules = rules[:handle_start] + legacy_handle
rules_path.write_text(rules)

# ---------------------------------------------------------------------------
# sepolicy.c: use the legacy Android SELinux structures that are present in
# this tree. These branches are based on the established pre-5.1 KernelSU
# compatibility design, adapted to the pinned SukiSU public function surface.
# ---------------------------------------------------------------------------
sepolicy = sepolicy_path.read_text()
sepolicy = replace_once(
    sepolicy,
    '#include <linux/gfp.h>\n',
    '#include <linux/gfp.h>\n#include <linux/flex_array.h>\n',
    'sepolicy.c flex_array include',
)

legacy_remove = r'''static bool remove_avtab_node(struct policydb *db, struct avtab_node *node)
{
    /*
     * Android's legacy flex-array avtab has no safe public unlink primitive.
     * The permission bits were already cleared by the caller; retaining the
     * now-empty node preserves policy semantics without corrupting the table.
     */
    (void)db;
    (void)node;
    return true;
}

'''
sepolicy = replace_between(
    sepolicy,
    'static bool remove_avtab_node(struct policydb *db, struct avtab_node *node)\n',
    'static bool add_rule(struct policydb *db,',
    legacy_remove,
    'sepolicy.c remove_avtab_node',
)

legacy_filename = r'''static bool add_filename_trans(struct policydb *db, const char *s, const char *t, const char *c, const char *d,
                               const char *o)
{
    struct type_datum *src, *tgt, *def;
    struct class_datum *cls;
    struct filename_trans key;
    struct filename_trans_datum *trans;
    struct filename_trans *new_key;

    src = symtab_search(&db->p_types, s);
    tgt = symtab_search(&db->p_types, t);
    cls = symtab_search(&db->p_classes, c);
    def = symtab_search(&db->p_types, d);
    if (!src || !tgt || !cls || !def)
        return false;

    key.stype = src->value;
    key.ttype = tgt->value;
    key.tclass = cls->value;
    key.name = o;

    trans = hashtab_search(db->filename_trans, &key);
    if (!trans) {
        trans = kzalloc(sizeof(*trans), GFP_ATOMIC);
        new_key = kzalloc(sizeof(*new_key), GFP_ATOMIC);
        if (!trans || !new_key) {
            kfree(trans);
            kfree(new_key);
            return false;
        }
        *new_key = key;
        new_key->name = kstrdup(key.name, GFP_ATOMIC);
        if (!new_key->name) {
            kfree(new_key);
            kfree(trans);
            return false;
        }
        trans->otype = def->value;
        if (hashtab_insert(db->filename_trans, new_key, trans)) {
            kfree((char *)new_key->name);
            kfree(new_key);
            kfree(trans);
            return false;
        }
    } else {
        trans->otype = def->value;
    }

    return ebitmap_set_bit(&db->filename_trans_ttypes, tgt->value - 1, 1) == 0;
}

'''
sepolicy = replace_between(
    sepolicy,
    'static bool add_filename_trans(struct policydb *db,',
    'static bool add_genfscon(struct policydb *db,',
    legacy_filename,
    'sepolicy.c add_filename_trans',
)

legacy_add_type = r'''static bool add_type(struct policydb *db, const char *type_name, bool attr)
{
    struct type_datum *type;
    struct flex_array *new_type_attr_map_array;
    struct flex_array *new_type_val_to_struct;
    struct flex_array *new_val_to_name_types;
    struct flex_array *old_fa;
    char *key;
    void *old_elem;
    u32 value;
    int i;

    type = symtab_search(&db->p_types, type_name);
    if (type)
        return true;

    value = ++db->p_types.nprim;
    type = kzalloc(sizeof(*type), GFP_ATOMIC);
    key = kstrdup(type_name, GFP_ATOMIC);
    if (!type || !key)
        return false;

    type->primary = 1;
    type->value = value;
    type->attribute = attr;
    if (symtab_insert(&db->p_types, key, type))
        return false;

    new_type_attr_map_array = flex_array_alloc(sizeof(struct ebitmap), value,
                                                GFP_ATOMIC | __GFP_ZERO);
    new_type_val_to_struct = flex_array_alloc(sizeof(struct type_datum *), value,
                                               GFP_ATOMIC | __GFP_ZERO);
    new_val_to_name_types = flex_array_alloc(sizeof(char *), value,
                                              GFP_ATOMIC | __GFP_ZERO);
    if (!new_type_attr_map_array || !new_type_val_to_struct || !new_val_to_name_types)
        return false;

    if (flex_array_prealloc(new_type_attr_map_array, 0, value,
                            GFP_ATOMIC | __GFP_ZERO) ||
        flex_array_prealloc(new_type_val_to_struct, 0, value,
                            GFP_ATOMIC | __GFP_ZERO) ||
        flex_array_prealloc(new_val_to_name_types, 0, value,
                            GFP_ATOMIC | __GFP_ZERO))
        return false;

    if (db->type_attr_map_array) {
        for (i = 0; i < db->type_attr_map_array->total_nr_elements; i++) {
            old_elem = flex_array_get(db->type_attr_map_array, i);
            if (old_elem)
                flex_array_put(new_type_attr_map_array, i, old_elem,
                               GFP_ATOMIC | __GFP_ZERO);
        }
    }
    if (db->type_val_to_struct_array) {
        for (i = 0; i < db->type_val_to_struct_array->total_nr_elements; i++) {
            old_elem = flex_array_get_ptr(db->type_val_to_struct_array, i);
            if (old_elem)
                flex_array_put_ptr(new_type_val_to_struct, i, old_elem,
                                   GFP_ATOMIC | __GFP_ZERO);
        }
    }
    if (db->sym_val_to_name[SYM_TYPES]) {
        for (i = 0; i < db->sym_val_to_name[SYM_TYPES]->total_nr_elements; i++) {
            old_elem = flex_array_get_ptr(db->sym_val_to_name[SYM_TYPES], i);
            if (old_elem)
                flex_array_put_ptr(new_val_to_name_types, i, old_elem,
                                   GFP_ATOMIC | __GFP_ZERO);
        }
    }

    old_fa = db->type_attr_map_array;
    db->type_attr_map_array = new_type_attr_map_array;
    if (old_fa)
        flex_array_free(old_fa);
    ebitmap_init(flex_array_get(db->type_attr_map_array, value - 1));
    ebitmap_set_bit(flex_array_get(db->type_attr_map_array, value - 1),
                    value - 1, 1);

    old_fa = db->type_val_to_struct_array;
    db->type_val_to_struct_array = new_type_val_to_struct;
    if (old_fa)
        flex_array_free(old_fa);
    flex_array_put_ptr(db->type_val_to_struct_array, value - 1, type,
                       GFP_ATOMIC | __GFP_ZERO);

    old_fa = db->sym_val_to_name[SYM_TYPES];
    db->sym_val_to_name[SYM_TYPES] = new_val_to_name_types;
    if (old_fa)
        flex_array_free(old_fa);
    flex_array_put_ptr(db->sym_val_to_name[SYM_TYPES], value - 1, key,
                       GFP_ATOMIC | __GFP_ZERO);

    for (i = 0; i < db->p_roles.nprim; i++)
        ebitmap_set_bit(&db->role_val_to_struct[i]->types, value - 1, 1);

    return true;
}

'''
sepolicy = replace_between(
    sepolicy,
    'static bool add_type(struct policydb *db,',
    'static bool set_type_state(struct policydb *db,',
    legacy_add_type,
    'sepolicy.c add_type',
)

legacy_typeattr = r'''static void add_typeattribute_raw(struct policydb *db, struct type_datum *type, struct type_datum *attr)
{
    struct ebitmap *sattr;
    struct hashtab_node *node;
    struct constraint_node *n;
    struct constraint_expr *e;

    sattr = flex_array_get(db->type_attr_map_array, type->value - 1);
    if (!sattr)
        return;
    ebitmap_set_bit(sattr, attr->value - 1, 1);

    ksu_hashtab_for_each(db->p_classes.table, node)
    {
        struct class_datum *cls = (struct class_datum *)node->datum;
        for (n = cls->constraints; n; n = n->next) {
            for (e = n->expr; e; e = e->next) {
                if (e->expr_type == CEXPR_NAMES &&
                    ebitmap_get_bit(&e->type_names->types, attr->value - 1))
                    ebitmap_set_bit(&e->names, type->value - 1, 1);
            }
        }
    };
}

'''
sepolicy = replace_between(
    sepolicy,
    'static void add_typeattribute_raw(struct policydb *db,',
    'static bool add_typeattribute(struct policydb *db,',
    legacy_typeattr,
    'sepolicy.c add_typeattribute_raw',
)

# The old kernel mutates its existing policydb and has no struct selinux_policy.
dup_marker = '// ======== sepolicy ========\n'
dup_pos = sepolicy.find(dup_marker)
if dup_pos < 0:
    raise SystemExit('sepolicy.c duplication marker not found')
sepolicy = sepolicy[:dup_pos] + '// Linux 4.19 uses the live selinux_state.ss policydb; no policy object duplication.\n'
sepolicy_path.write_text(sepolicy)

# ---------------------------------------------------------------------------
# The SELinux-hide feature depends on replaceable struct selinux_policy objects,
# which do not exist in this Android 4.19 implementation. Keep the public hooks
# as explicit no-ops instead of fabricating an incompatible backup policy.
# ---------------------------------------------------------------------------
hide_path.write_text(r'''#include "feature/selinux_hide.h"

void ksu_selinux_hide_init(void) {}
void ksu_selinux_hide_exit(void) {}
void ksu_selinux_hide_drop_backup_if_unused(void) {}
void ksu_selinux_hide_handle_second_stage(void) {}
void ksu_selinux_hide_handle_post_fs_data(void) {}
''')

# ---------------------------------------------------------------------------
# SULOG uses the newer names for helpers already available under their legacy
# names in Linux 4.19.
# ---------------------------------------------------------------------------
sulog = sulog_path.read_text()
sulog = replace_once(
    sulog,
    '#include <linux/minmax.h>\n',
    '#include <linux/kernel.h> /* Linux 4.19 provides min_t here. */\n',
    'sulog/event.c minmax include',
)
count = sulog.count('strncpy_from_user_nofault(')
if count != 2:
    raise SystemExit(f'sulog/event.c expected two nofault string calls, found {count}')
sulog = sulog.replace('strncpy_from_user_nofault(', 'strncpy_from_unsafe_user(')
sulog_path.write_text(sulog)
PY

# Verify the old-kernel path rather than merely checking that text changed.
! grep -Fq 'selinux_state.policy' "$RULES_C" || fail "New SELinux policy pointer remains in rules.c"
! grep -Fq 'selinux_state.policy_mutex' "$RULES_C" || fail "New SELinux policy mutex remains in rules.c"
grep -Fq 'rcu_dereference(selinux_state.ss)' "$RULES_C" || fail "Legacy SELinux security-server lookup is missing"
grep -Fq 'validate_sepolicy_batch' "$RULES_C" || fail "Pre-mutation batch validation is missing"
grep -Fq 'db = &ss->policydb;' "$RULES_C" || fail "Legacy live policydb selection is missing"

! grep -Fq 'struct filename_trans_key' "$SEPOLICY_C" || fail "New filename transition layout remains"
grep -Fq 'struct filename_trans key;' "$SEPOLICY_C" || fail "Legacy filename transition layout is missing"
grep -Fq 'type_val_to_struct_array' "$SEPOLICY_C" || fail "Legacy type-value flex array path is missing"
! grep -Fq 'db->type_val_to_struct,' "$SEPOLICY_C" || fail "New type-value pointer-array path remains"
grep -Fq 'flex_array_get(db->type_attr_map_array' "$SEPOLICY_C" || fail "Legacy type-attribute flex-array access is missing"
! grep -Fq 'ksu_dup_sepolicy' "$SEPOLICY_C" || fail "Unsupported policy duplication implementation remains"
! grep -Fq 'ksu_destroy_sepolicy' "$SEPOLICY_C" || fail "Unsupported policy destruction implementation remains"

grep -Fq 'void ksu_selinux_hide_init(void) {}' "$SELINUX_HIDE_C" || fail "SELinux-hide no-op compatibility hook is missing"
! grep -Fq 'backup_sepolicy' "$SELINUX_HIDE_C" || fail "Unsupported SELinux-hide backup access remains"
! grep -Fq '#include <linux/minmax.h>' "$SULOG_EVENT_C" || fail "Unsupported minmax header remains"
grep -Fq '#include <linux/kernel.h>' "$SULOG_EVENT_C" || fail "Legacy min_t header is missing"
! grep -Fq 'strncpy_from_user_nofault' "$SULOG_EVENT_C" || fail "Unsupported SULOG nofault helper remains"
test "$(grep -Fc 'strncpy_from_unsafe_user(' "$SULOG_EVENT_C")" -eq 2 || fail "Expected two legacy SULOG nofault string calls"

git -C "$SUKISU_DIR" diff --check
git -C "$SUKISU_DIR" diff --binary -- \
  kernel/selinux/rules.c \
  kernel/selinux/sepolicy.c \
  kernel/feature/selinux_hide.c \
  kernel/sulog/event.c > "$PATCH_OUT"
test -s "$PATCH_OUT" || fail "SukiSU Linux 4.19 legacy SELinux patch is empty"
sha256sum "$PATCH_OUT" > "$PATCH_OUT.sha256"

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'sukisu_commit=%s\n' "$(git -C "$SUKISU_DIR" rev-parse HEAD)"
  printf 'selinux_policy_storage=selinux_state.ss-policydb\n'
  printf 'policy_mutation=legacy-in-place-after-full-batch-validation\n'
  printf 'avtab_storage=flex-array-empty-nodes-retained\n'
  printf 'filename_transition=legacy-struct-filename_trans\n'
  printf 'type_maps=legacy-flex-arrays\n'
  printf 'selinux_hide=disabled-noop-on-linux-4.19\n'
  printf 'sulog_minmax=linux-kernel-h\n'
  printf 'sulog_user_string_nofault=strncpy_from_unsafe_user\n'
  printf 'historical_compatibility_reference=KernelSU-pre-5.1-design\n'
  printf 'patch_sha256=%s\n' "$(cut -d' ' -f1 "$PATCH_OUT.sha256")"
} | tee "$REPORT"

info "SukiSU Linux 4.19 legacy SELinux policy compatibility patch applied"
