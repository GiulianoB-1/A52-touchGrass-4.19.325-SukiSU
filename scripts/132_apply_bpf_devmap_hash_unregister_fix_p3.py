#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 3:
    raise SystemExit("usage: 132_apply_bpf_devmap_hash_unregister_fix_p3.py <kernel_tree> <report_dir>")

kernel = Path(sys.argv[1]).resolve()
report_dir = Path(sys.argv[2]).resolve()
report_dir.mkdir(parents=True, exist_ok=True)

devmap = kernel / "kernel/bpf/devmap.c"
if not devmap.is_file():
    raise SystemExit(f"missing expected kernel file: {devmap}")

s = devmap.read_text()

anchor = """const struct bpf_map_ops dev_map_hash_ops = {
	.map_alloc = dev_map_alloc,
	.map_free = dev_map_free,
	.map_get_next_key = dev_map_hash_get_next_key,
	.map_lookup_elem = dev_map_hash_lookup_elem,
	.map_update_elem = dev_map_hash_update_elem,
	.map_delete_elem = dev_map_hash_delete_elem,
};

static int dev_map_notification(struct notifier_block *notifier,
"""

replacement = """const struct bpf_map_ops dev_map_hash_ops = {
	.map_alloc = dev_map_alloc,
	.map_free = dev_map_free,
	.map_get_next_key = dev_map_hash_get_next_key,
	.map_lookup_elem = dev_map_hash_lookup_elem,
	.map_update_elem = dev_map_hash_update_elem,
	.map_delete_elem = dev_map_hash_delete_elem,
};

/*
 * A52 USB RNDIS fix P3
 *
 * Backport of upstream Linux commit ce197d83a9fc42795c248c90983bf05faf0f013b
 * ("xdp: Handle device unregister for devmap_hash map type").
 *
 * DEVMAP_HASH entries live in dev_index_head, not netdev_map[].  The old
 * unregister notifier only scanned netdev_map[] and therefore never dropped
 * the dev_get_by_index() reference held by a hash entry.  Android tethering
 * inserts rndis0 into a DEVMAP_HASH, leaving unregister_netdev() stuck with
 * Usage count = 1.  Walk the hash buckets on NETDEV_UNREGISTER and schedule
 * normal RCU destruction of every entry referring to the removed netdev.
 */
static void dev_map_hash_remove_netdev(struct bpf_dtab *dtab,
				       struct net_device *netdev)
{
	unsigned long flags;
	u32 i;

	spin_lock_irqsave(&dtab->index_lock, flags);
	for (i = 0; i < dtab->n_buckets; i++) {
		struct bpf_dtab_netdev *dev;
		struct hlist_head *head;
		struct hlist_node *next;

		head = dev_map_index_hash(dtab, i);

		hlist_for_each_entry_safe(dev, next, head, index_hlist) {
			if (netdev != dev->dev)
				continue;

			dtab->items--;
			hlist_del_rcu(&dev->index_hlist);
			call_rcu(&dev->rcu, __dev_map_entry_free);
		}
	}
	spin_unlock_irqrestore(&dtab->index_lock, flags);
}

static int dev_map_notification(struct notifier_block *notifier,
"""

if s.count(anchor) != 1:
    raise SystemExit(f"devmap helper anchor count={s.count(anchor)}")
s = s.replace(anchor, replacement, 1)

loop_anchor = """		rcu_read_lock();
		list_for_each_entry_rcu(dtab, &dev_map_list, list) {
			for (i = 0; i < dtab->map.max_entries; i++) {
"""
loop_replacement = """		rcu_read_lock();
		list_for_each_entry_rcu(dtab, &dev_map_list, list) {
			if (dtab->map.map_type == BPF_MAP_TYPE_DEVMAP_HASH) {
				dev_map_hash_remove_netdev(dtab, netdev);
				continue;
			}

			for (i = 0; i < dtab->map.max_entries; i++) {
"""
if s.count(loop_anchor) != 1:
    raise SystemExit(f"devmap notifier anchor count={s.count(loop_anchor)}")
s = s.replace(loop_anchor, loop_replacement, 1)

devmap.write_text(s)

report = report_dir / "report.txt"
report.write_text(
    "A52 USB RNDIS devmap-hash unregister fix P3\n"
    "baseline=GitHub Actions run 35633887441 / project commit "
    "95278a740d6c4ccca32489247f373a0f6ab96982\n"
    "evidence=P2 traced sole non-FIB dev_get_by_index owner to dev_map_hash_update_elem\n"
    "upstream_fix=ce197d83a9fc42795c248c90983bf05faf0f013b\n"
    "upstream_title=xdp: Handle device unregister for devmap_hash map type\n"
    "changed_file=kernel/bpf/devmap.c\n"
    "behavior=DEVMAP_HASH entries referencing unregistering netdev are removed from hash and RCU-freed\n"
)

print(report.read_text(), end="")
