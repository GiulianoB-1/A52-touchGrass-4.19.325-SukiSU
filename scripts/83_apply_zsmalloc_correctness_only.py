#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 83_apply_zsmalloc_correctness_only.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
p = root / "mm/zsmalloc.c"
if not p.is_file():
    raise SystemExit(f"missing {p}")

s = p.read_text()
changes = []

# Fix 1: serialize isolated_pages teardown visibility against
# zs_unregister_migration()/wait_for_isolated_drain(). This is upstream
# b19675b6a046da6b4e160a78eedb3dd2584aa889.
needle = """	atomic_long_dec(&pool->isolated_pages);
"""
marker = "\\tsmp_mb__after_atomic();\\n"
fn = "static inline void zs_pool_dec_isolated(struct zs_pool *pool)"
start = s.find(fn)
if start < 0:
    raise SystemExit("zs_pool_dec_isolated() not found")
end = s.find("\n}", start)
if end < 0:
    raise SystemExit("zs_pool_dec_isolated() end not found")
block = s[start:end+2]
if marker not in block:
    if needle not in block:
        raise SystemExit("isolated_pages decrement anchor changed")
    block = block.replace(
        needle,
        needle +
        "	/* Paired with the full barrier in zs_unregister_migration(). */\n" +
        marker,
        1,
    )
    s = s[:start] + block + s[end+2:]
    changes.append("isolated_pages_barrier")
else:
    changes.append("isolated_pages_barrier_already_present")

# Fix 2: protect lock_zspage() traversal against concurrent page migration.
# This is upstream 645996efc2ae391246d595832aaa6f9d3cc338c7.
old = """static void lock_zspage(struct zspage *zspage)
{
	struct page *page = get_first_page(zspage);

	do {
		lock_page(page);
	} while ((page = get_next_page(page)) != NULL);
}
"""
new = """static void lock_zspage(struct zspage *zspage)
{
	struct page *curr_page, *page;

	/*
	 * Pages not locked yet can migrate off the zspage list.  Serialize
	 * list traversal with migration and only wait after pinning the page.
	 */
	while (1) {
		migrate_read_lock(zspage);
		page = get_first_page(zspage);
		if (trylock_page(page))
			break;
		get_page(page);
		migrate_read_unlock(zspage);
		wait_on_page_locked(page);
		put_page(page);
	}

	curr_page = page;
	while ((page = get_next_page(curr_page))) {
		if (trylock_page(page)) {
			curr_page = page;
		} else {
			get_page(page);
			migrate_read_unlock(zspage);
			wait_on_page_locked(page);
			put_page(page);
			migrate_read_lock(zspage);
		}
	}
	migrate_read_unlock(zspage);
}
"""
if old in s:
    s = s.replace(old, new, 1)
    changes.append("lock_zspage_migration_race")
elif (
    "migrate_read_lock(zspage);" in s
    and "wait_on_page_locked(page);" in s
    and "static void lock_zspage(struct zspage *zspage)" in s
):
    changes.append("lock_zspage_migration_race_already_present")
else:
    raise SystemExit("lock_zspage() shape changed unexpectedly")

p.write_text(s)

# Strong postconditions.
out = p.read_text()
if "smp_mb__after_atomic();" not in out:
    raise SystemExit("missing isolated_pages barrier")
if "migrate_read_lock(zspage);" not in out:
    raise SystemExit("missing zspage migration read lock")
if "wait_on_page_locked(page);" not in out:
    raise SystemExit("missing safe page-lock wait")
if "static void lock_zspage(struct zspage *zspage)" not in out:
    raise SystemExit("lock_zspage missing")

report = root.parent.parent / "artifacts" / "phase83-zsmalloc-correctness.txt"
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(
    "phase=83-zsmalloc-correctness-only\n"
    "accounting_fix=already-in-4.19.206\n"
    "isolated_pages_barrier=enabled\n"
    "lock_zspage_migration_race_fix=enabled\n"
    "zram_frontend=untouched\n"
    "zcomp_stream_model=untouched\n"
    "changes=" + ",".join(changes) + "\n"
)
print(report.read_text(), end="")
