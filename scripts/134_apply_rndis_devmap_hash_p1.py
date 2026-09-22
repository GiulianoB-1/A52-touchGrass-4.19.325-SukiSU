#!/usr/bin/env python3
from pathlib import Path
import sys

def die(msg: str) -> None:
    raise SystemExit(f"A52 RNDIS DEVMAP HASH P1 ERROR: {msg}")

if len(sys.argv) != 3:
    die("usage: 134_apply_rndis_devmap_hash_p1.py <kernel_tree> <artifact_dir>")

root = Path(sys.argv[1]).resolve()
art = Path(sys.argv[2]).resolve()
art.mkdir(parents=True, exist_ok=True)

path = root / "kernel/bpf/devmap.c"
if not path.is_file():
    die(f"missing {path}")

s = path.read_text()
before = s
marker = "/* A52 RNDIS DEVMAP_HASH unregister fix P1 */"

if marker in s:
    die("fix already applied")

# This 4.19 tree already has BPF_MAP_TYPE_DEVMAP_HASH and hash entries acquire
# a net_device reference with dev_get_by_index(), but its NETDEV_UNREGISTER
# notifier only scans the legacy array devmap. Linux 5.4 added the missing
# hash-table removal path. Backport that path only.
required = (
    "const struct bpf_map_ops dev_map_hash_ops = {",
    "dev->dev = dev_get_by_index(net, ifindex);",
    "static int dev_map_notification(struct notifier_block *notifier,",
    "hlist_del_init_rcu(&old_dev->index_hlist);",
)
for needle in required:
    if needle not in s:
        die(f"missing baseline invariant: {needle}")

if "dev_map_hash_remove_netdev" in s:
    die("dev_map_hash_remove_netdev already exists; baseline is not expected 4.19 state")

ops_anchor = """const struct bpf_map_ops dev_map_hash_ops = {
	.map_alloc = dev_map_alloc,
	.map_free = dev_map_free,
	.map_get_next_key = dev_map_hash_get_next_key,
	.map_lookup_elem = dev_map_hash_lookup_elem,
	.map_update_elem = dev_map_hash_update_elem,
	.map_delete_elem = dev_map_hash_delete_elem,
	.map_check_btf = map_check_no_btf,
};

static int dev_map_notification(struct notifier_block *notifier,
"""
if s.count(ops_anchor) != 1:
    die(f"expected one dev_map_hash_ops/notifier anchor, found {s.count(ops_anchor)}")

helper = """const struct bpf_map_ops dev_map_hash_ops = {
	.map_alloc = dev_map_alloc,
	.map_free = dev_map_free,
	.map_get_next_key = dev_map_hash_get_next_key,
	.map_lookup_elem = dev_map_hash_lookup_elem,
	.map_update_elem = dev_map_hash_update_elem,
	.map_delete_elem = dev_map_hash_delete_elem,
	.map_check_btf = map_check_no_btf,
};

/* A52 RNDIS DEVMAP_HASH unregister fix P1 */
/*
 * Backport of the upstream Linux 5.4 DEVMAP_HASH netdevice-unregister
 * cleanup. Hash entries own a net_device reference via dev_get_by_index();
 * they must be removed when that device unregisters, just like array
 * devmap entries.
 */
static void dev_map_hash_remove_netdev(struct bpf_dtab *dtab,
				       struct net_device *netdev)
{
	unsigned long flags;
	u32 i;
	int removed = 0;

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
			removed++;
		}
	}
	spin_unlock_irqrestore(&dtab->index_lock, flags);

	if (removed)
		pr_info("A52_RNDIS_DEVMAP_HASH_RELEASE dev=%s ifindex=%d removed=%d\\n",
			netdev->name, netdev->ifindex, removed);
}

static int dev_map_notification(struct notifier_block *notifier,
"""
s = s.replace(ops_anchor, helper, 1)

loop_old = """		rcu_read_lock();
		list_for_each_entry_rcu(dtab, &dev_map_list, list) {
			for (i = 0; i < dtab->map.max_entries; i++) {
"""
loop_new = """		rcu_read_lock();
		list_for_each_entry_rcu(dtab, &dev_map_list, list) {
			if (dtab->map.map_type == BPF_MAP_TYPE_DEVMAP_HASH) {
				dev_map_hash_remove_netdev(dtab, netdev);
				continue;
			}

			for (i = 0; i < dtab->map.max_entries; i++) {
"""
if s.count(loop_old) != 1:
    die(f"expected one unregister devmap loop, found {s.count(loop_old)}")
s = s.replace(loop_old, loop_new, 1)

post = (
    marker,
    "static void dev_map_hash_remove_netdev(struct bpf_dtab *dtab,",
    "if (dtab->map.map_type == BPF_MAP_TYPE_DEVMAP_HASH) {",
    "dev_map_hash_remove_netdev(dtab, netdev);",
    "A52_RNDIS_DEVMAP_HASH_RELEASE",
    "dtab->items--;",
    "hlist_del_rcu(&dev->index_hlist);",
    "call_rcu(&dev->rcu, __dev_map_entry_free);",
)
for needle in post:
    if needle not in s:
        die(f"post-patch invariant missing: {needle}")

if s.count("dev_map_hash_remove_netdev(dtab, netdev);") != 1:
    die("unexpected dev_map_hash_remove_netdev call count")

path.write_text(s)

report = """A52 RNDIS DEVMAP_HASH unregister fix P1
========================================
baseline=35633887441
target=kernel/bpf/devmap.c
source_reference=Linux 5.4 dev_map_hash_remove_netdev
problem=DEVMAP_HASH entry owns net_device ref via dev_get_by_index but 4.19 notifier only removes array entries
fix=remove matching hash entries on NETDEV_UNREGISTER and release via existing RCU free path
runtime_marker=A52_RNDIS_DEVMAP_HASH_RELEASE
forced_dev_put=no
ipa_pm_change=no
dwc3_change=no
configfs_change=no
"""
(art / "report.txt").write_text(report)
(art / "devmap-before.c").write_text(before)
(art / "devmap-after.c").write_text(s)
print(report, end="")
