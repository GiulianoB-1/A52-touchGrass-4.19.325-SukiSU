#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 3:
    raise SystemExit("usage: 133_apply_bpf_devmap_lifecycle_fix_p4.py <kernel_tree> <report_dir>")

kernel = Path(sys.argv[1]).resolve()
report_dir = Path(sys.argv[2]).resolve()
report_dir.mkdir(parents=True, exist_ok=True)

p = kernel / "kernel/bpf/devmap.c"
if not p.is_file():
    raise SystemExit(f"missing expected kernel file: {p}")

s = p.read_text()

# Samsung's tree has the pre-75cc per-entry bulkq users/free path, but the
# post-75cc __dev_map_alloc_node() which no longer allocates bulkq.  Restore
# the missing allocation semantics that this hybrid backport requires.
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
	 * A52 BPF devmap lifecycle P4:
	 * this tree still stores a per-entry bulkq and frees it from
	 * __dev_map_entry_free(), so every node must own a real percpu queue.
	 * The allocation was accidentally lost when newer devmap code was
	 * partially backported without moving the queue into struct net_device.
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

# Fix DEVMAP_HASH map-free cleanup.  Upstream 071cdecec57f fixed this
# independently of the unregister hook.  Keep Samsung's current allocation
# layout (netdev_map is allocated for both map types) to minimize the backport,
# but correctly walk/finalize hash entries before freeing the backing arrays.
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

p.write_text(s)

report = report_dir / "report.txt"
report.write_text(
    "A52 BPF DEVMAP lifecycle fix P4\n"
    "baseline=35633887441 plus P1/P2/P3 diagnostics/fix\n"
    "ramoops=P3 crash in free_percpu -> __dev_map_entry_free -> rcu_nocb_kthread\n"
    "root_cause=Samsung hybrid devmap backport retains per-entry bulkq users/free but __dev_map_alloc_node never allocates bulkq\n"
    "repair_1=restore __alloc_percpu_gfp(sizeof(*dev->bulkq), sizeof(void *), gfp)\n"
    "repair_2=free bulkq on dev_get_by_index failure\n"
    "repair_3=DEVMAP_HASH map-free bucket walk adapted from upstream 071cdecec57fb5d5df78e6a12114ad7bccea5b0e; direct bucket indexing avoids helper-order dependency\n"
    "unregister_fix=upstream ce197d83a9fc42795c248c90983bf05faf0f013b retained from P3\n"
    "changed_file=kernel/bpf/devmap.c\n"
)

print(report.read_text(), end="")
