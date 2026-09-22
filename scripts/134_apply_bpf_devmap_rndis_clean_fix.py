#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 3:
    raise SystemExit("usage: 134_apply_bpf_devmap_rndis_clean_fix.py <kernel_tree> <report_dir>")

kernel = Path(sys.argv[1]).resolve()
report_dir = Path(sys.argv[2]).resolve()
report_dir.mkdir(parents=True, exist_ok=True)

p = kernel / "kernel/bpf/devmap.c"
if not p.is_file():
    raise SystemExit(f"missing expected kernel file: {p}")

s = p.read_text()

# 1) Restore the per-entry bulk queue ownership required by this Samsung tree.
old_alloc = """static struct bpf_dtab_netdev *__dev_map_alloc_node(struct net *net,
						    struct bpf_dtab *dtab,
						    u32 ifindex,
						    unsigned int idx)
{
	gfp_t gfp = GFP_ATOMIC | __GFP_NOWARN;
	struct bpf_dtab_netdev *dev;

	dev = kmalloc_node(sizeof(*dev), gfp, dtab->map.numa_node);
	if (!dev)
		return ERR_PTR(-ENOMEM);

	dev->dev = dev_get_by_index(net, ifindex);
	if (!dev->dev) {
		kfree(dev);
		return ERR_PTR(-EINVAL);
	}

	dev->bit = idx;
	dev->dtab = dtab;

	return dev;
}
"""

new_alloc = """static struct bpf_dtab_netdev *__dev_map_alloc_node(struct net *net,
						    struct bpf_dtab *dtab,
						    u32 ifindex,
						    unsigned int idx)
{
	gfp_t gfp = GFP_ATOMIC | __GFP_NOWARN;
	struct bpf_dtab_netdev *dev;

	dev = kmalloc_node(sizeof(*dev), gfp, dtab->map.numa_node);
	if (!dev)
		return ERR_PTR(-ENOMEM);

	/*
	 * This tree retains the per-entry bulkq lifetime model, so every
	 * devmap node must own a valid percpu queue until RCU destruction.
	 */
	dev->bulkq = __alloc_percpu_gfp(sizeof(*dev->bulkq),
					sizeof(void *), gfp);
	if (!dev->bulkq) {
		kfree(dev);
		return ERR_PTR(-ENOMEM);
	}

	dev->dev = dev_get_by_index(net, ifindex);
	if (!dev->dev) {
		free_percpu(dev->bulkq);
		kfree(dev);
		return ERR_PTR(-EINVAL);
	}

	dev->bit = idx;
	dev->dtab = dtab;

	return dev;
}
"""

if s.count(old_alloc) != 1:
    raise SystemExit(f"bulkq allocation anchor count={s.count(old_alloc)}")
s = s.replace(old_alloc, new_alloc, 1)

# 2) Correct DEVMAP_HASH cleanup when the map itself is freed.
old_free = """	for (i = 0; i < dtab->map.max_entries; i++) {
		struct bpf_dtab_netdev *dev;

		dev = dtab->netdev_map[i];
		if (!dev)
			continue;

		free_percpu(dev->bulkq);
		dev_put(dev->dev);
		kfree(dev);
	}

	free_percpu(dtab->flush_needed);
	bpf_map_area_free(dtab->netdev_map);
	kfree(dtab->dev_index_head);
	kfree(dtab);
}
"""

new_free = """	if (dtab->map.map_type == BPF_MAP_TYPE_DEVMAP_HASH) {
		for (i = 0; i < dtab->n_buckets; i++) {
			struct bpf_dtab_netdev *dev;
			struct hlist_head *head;
			struct hlist_node *next;

			head = &dtab->dev_index_head[i];

			hlist_for_each_entry_safe(dev, next, head, index_hlist) {
				hlist_del_rcu(&dev->index_hlist);
				free_percpu(dev->bulkq);
				dev_put(dev->dev);
				kfree(dev);
			}
		}
	} else {
		for (i = 0; i < dtab->map.max_entries; i++) {
			struct bpf_dtab_netdev *dev;

			dev = dtab->netdev_map[i];
			if (!dev)
				continue;

			free_percpu(dev->bulkq);
			dev_put(dev->dev);
			kfree(dev);
		}
	}

	free_percpu(dtab->flush_needed);
	bpf_map_area_free(dtab->netdev_map);
	kfree(dtab->dev_index_head);
	kfree(dtab);
}
"""

if s.count(old_free) != 1:
    raise SystemExit(f"DEVMAP_HASH map-free anchor count={s.count(old_free)}")
s = s.replace(old_free, new_free, 1)

# 3) Handle NETDEV_UNREGISTER for DEVMAP_HASH entries.
ops_anchor = """const struct bpf_map_ops dev_map_hash_ops = {
	.map_alloc = dev_map_alloc,
	.map_free = dev_map_free,
	.map_get_next_key = dev_map_hash_get_next_key,
	.map_lookup_elem = dev_map_hash_lookup_elem,
	.map_update_elem = dev_map_hash_update_elem,
	.map_delete_elem = dev_map_hash_delete_elem,
};

static int dev_map_notification(struct notifier_block *notifier,
"""

ops_replacement = """const struct bpf_map_ops dev_map_hash_ops = {
	.map_alloc = dev_map_alloc,
	.map_free = dev_map_free,
	.map_get_next_key = dev_map_hash_get_next_key,
	.map_lookup_elem = dev_map_hash_lookup_elem,
	.map_update_elem = dev_map_hash_update_elem,
	.map_delete_elem = dev_map_hash_delete_elem,
};

/*
 * Backport of upstream ce197d83a9fc:
 * DEVMAP_HASH entries are stored in dev_index_head and must be removed when
 * their net_device unregisters, otherwise the dev_get_by_index() reference
 * prevents unregister_netdev() from completing.
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

if s.count(ops_anchor) != 1:
    raise SystemExit(f"devmap unregister helper anchor count={s.count(ops_anchor)}")
s = s.replace(ops_anchor, ops_replacement, 1)

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

p.write_text(s)

report = report_dir / "report.txt"
report.write_text(
    "A52 clean BPF DEVMAP/RNDIS production fix P134\n"
    "baseline=GitHub Actions run 35633887441 / project commit 95278a740d6c4ccca32489247f373a0f6ab96982\n"
    "validated_fix_source=P4 successful run 35771604848\n"
    "upstream_unregister_fix=ce197d83a9fc42795c248c90983bf05faf0f013b\n"
    "upstream_map_free_fix=071cdecec57fb5d5df78e6a12114ad7bccea5b0e adapted\n"
    "samsung_lifecycle_repair=restore per-entry bulkq allocation required by existing free/flush path\n"
    "diagnostics=none\n"
    "changed_file=kernel/bpf/devmap.c\n"
)

print(report.read_text(), end="")
