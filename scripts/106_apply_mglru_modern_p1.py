#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 106_apply_mglru_modern_p1.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
vmscan = root / "mm/vmscan.c"

if not vmscan.is_file():
    raise SystemExit(f"missing required file: {vmscan}")

def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)

s = vmscan.read_text()

# Require the proven Phase80 baseline before touching MGLRU.
if "static bool sort_page(struct lruvec *lruvec, struct page *page, int tier_idx)" not in s:
    raise SystemExit("Phase79 page-based MGLRU sort_page signature not found")
if "wait_event_killable(lruvec->mm_state.wait" in s:
    raise SystemExit("Phase80 no-wait MGLRU hardening is not present")
if "__mod_lruvec_state(lruvec, WORKINGSET_ACTIVATE, delta);" in s[
    s.find("static bool sort_page("):s.find("static bool isolate_page(", s.find("static bool sort_page("))
]:
    raise SystemExit("Phase80 workingset accounting hardening is not present")

# ---------------------------------------------------------------------------
# Modern P1.1
# Android 17 / Linux 6.18 reference:
#   9eff4247bc3998124316da05856e4802ffecc3c9
# FROMLIST: mm/mglru: fix and remove redundant unevictable folio handling
#
# The original MGLRU shortcut removes an unevictable page from its generation
# while PG_lru is still set, then manually puts it on the unevictable LRU.
# Current Android lets normal isolation happen first and lets the generic
# shrink path cull the page. Linux 4.19 shrink_page_list() already has the
# matching page_evictable() check, so the same ordering fix applies here.
# ---------------------------------------------------------------------------

s = replace_once(
    s,
    """static bool sort_page(struct lruvec *lruvec, struct page *page, int tier_idx)
{
	bool success;
""",
    """static bool sort_page(struct lruvec *lruvec, struct page *page, struct scan_control *sc,
		      int tier_idx)
{
""",
    "sort_page signature and obsolete success variable",
)

s = replace_once(
    s,
    """	if (!page_evictable(page)) {
		success = lru_gen_del_page(lruvec, page, true);
		VM_BUG_ON_PAGE(!success, page);
		SetPageUnevictable(page);
		add_page_to_lru_list(page, lruvec);
		__count_vm_events(UNEVICTABLE_PGCULLED, delta);
		return true;
	}
""",
    """	/* A52 MGLRU Modern P1: Android17 9eff4247, generic path culls it */
	if (!page_evictable(page))
		return false;
""",
    "Android17 unevictable handling",
)

# ---------------------------------------------------------------------------
# Modern P1.2
# Android common reference:
#   a7adb988970e13c42f3c7ca4fe157c35e8e885fe
# FROMGIT: Multi-gen LRU: Fix per-zone reclaim
#
# Scan every zone in the oldest generation. Pages above reclaim_idx cannot be
# reclaimed by this allocation context, so promote them to the next generation.
# This lets min_seq advance instead of stalling behind an ineligible zone.
# ---------------------------------------------------------------------------

sort_start = s.find("static bool sort_page(")
sort_end = s.find("static bool isolate_page(", sort_start)
if sort_start < 0 or sort_end < 0:
    raise SystemExit("sort_page boundaries not found")
sort = s[sort_start:sort_end]

sort = replace_once(
    sort,
    """	if (PageLocked(page) || PageWriteback(page) ||
""",
    """	/* A52 MGLRU Modern P1: Android a7adb988 per-zone forward progress */
	if (zone > sc->reclaim_idx) {
		gen = page_inc_gen(lruvec, page, false);
		list_move_tail(&page->lru, &lrugen->lists[gen][type][zone]);
		return true;
	}

	if (PageLocked(page) || PageWriteback(page) ||
""",
    "per-zone ineligible promotion",
)
s = s[:sort_start] + sort + s[sort_end:]

scan_start = s.find("static int scan_pages(")
scan_end = s.find("static int get_tier_idx(", scan_start)
if scan_start < 0 or scan_end < 0:
    raise SystemExit("scan_pages boundaries not found")
scan = s[scan_start:scan_end]

scan = replace_once(
    scan,
    "	int gen, zone;\n",
    "	int i;\n	int gen;\n",
    "scan_pages zone iterator declaration",
)
scan = replace_once(
    scan,
    """	for (zone = sc->reclaim_idx; zone >= 0; zone--) {
		LIST_HEAD(moved);
		int skipped = 0;
		struct list_head *head = &lrugen->lists[gen][type][zone];
""",
    """	for (i = MAX_NR_ZONES; i > 0; i--) {
		LIST_HEAD(moved);
		int skipped = 0;
		int zone = (sc->reclaim_idx + i) % MAX_NR_ZONES;
		struct list_head *head = &lrugen->lists[gen][type][zone];
""",
    "scan_pages all-zone rotation",
)
scan = replace_once(
    scan,
    "if (sort_page(lruvec, page, tier))",
    "if (sort_page(lruvec, page, sc, tier))",
    "sort_page scan_control call",
)
s = s[:scan_start] + scan + s[scan_end:]

# ---------------------------------------------------------------------------
# Modern P1.3
# Android common reference:
#   a550d93c939a54df5557cfbf2e354e663896b9b2
# UPSTREAM: mm: multi-gen LRU: fix crash during cgroup migration
#
# cgroup migration can race lru_gen_add_mm() for a freshly cloned task. Do not
# touch the MGLRU mm list until the mm has actually been added.
# ---------------------------------------------------------------------------

migrate_start = s.find("void lru_gen_migrate_mm(struct mm_struct *mm)")
migrate_end = s.find("#endif", migrate_start)
if migrate_start < 0 or migrate_end < 0:
    raise SystemExit("lru_gen_migrate_mm boundaries not found")
migrate = s[migrate_start:migrate_end]

migrate = replace_once(
    migrate,
    """	if (mem_cgroup_disabled())
		return;

	rcu_read_lock();
""",
    """	if (mem_cgroup_disabled())
		return;

	/* A52 MGLRU Modern P1: migration can happen before addition */
	if (!mm->lru_gen.memcg)
		return;

	rcu_read_lock();
""",
    "cgroup migration pre-add guard",
)
migrate = replace_once(
    migrate,
    "	VM_BUG_ON_MM(!mm->lru_gen.memcg, mm);\n",
    "",
    "obsolete null memcg BUG",
)
s = s[:migrate_start] + migrate + s[migrate_end:]

# Structural safety checks.
sort_start = s.find("static bool sort_page(")
sort_end = s.find("static bool isolate_page(", sort_start)
scan_start = s.find("static int scan_pages(")
scan_end = s.find("static int get_tier_idx(", scan_start)
migrate_start = s.find("void lru_gen_migrate_mm(struct mm_struct *mm)")
migrate_end = s.find("#endif", migrate_start)

sort = s[sort_start:sort_end]
scan = s[scan_start:scan_end]
migrate = s[migrate_start:migrate_end]

checks = [
    ("Android17 unevictable pass-through missing",
     "if (!page_evictable(page))\n\t\treturn false;" in sort),
    ("old unsafe unevictable shortcut remains",
     "SetPageUnevictable(page);" not in sort),
    ("per-zone reclaim guard missing",
     "if (zone > sc->reclaim_idx)" in sort),
    ("ineligible page promotion missing",
     "list_move_tail(&page->lru, &lrugen->lists[gen][type][zone]);" in sort),
    ("all-zone scan order missing",
     "int zone = (sc->reclaim_idx + i) % MAX_NR_ZONES;" in scan),
    ("sort_page does not receive scan_control",
     "sort_page(lruvec, page, sc, tier)" in scan),
    ("cgroup migration guard missing",
     "if (!mm->lru_gen.memcg)\n\t\treturn;" in migrate),
    ("obsolete cgroup migration null BUG remains",
     "VM_BUG_ON_MM(!mm->lru_gen.memcg, mm);" not in migrate),
    ("Linux 4.19 generic unevictable cull path missing",
     "if (unlikely(!page_evictable(page)))\n\t\t\tgoto activate_locked;" in s),
]
for label, ok in checks:
    if not ok:
        raise SystemExit(label)

vmscan.write_text(s)

report_dir = root.parent.parent / "artifacts"
report_dir.mkdir(parents=True, exist_ok=True)
(report_dir / "mglru-phase106-modern-p1.txt").write_text(
    "phase=106\n"
    "baseline_mglru=phase80\n"
    "android17_unevictable_fix=9eff4247bc3998124316da05856e4802ffecc3c9\n"
    "per_zone_reclaim_fix=a7adb988970e13c42f3c7ca4fe157c35e8e885fe\n"
    "cgroup_migration_fix=a550d93c939a54df5557cfbf2e354e663896b9b2\n"
    "unevictable_handling=generic-shrink-path\n"
    "per_zone_scan=all-zones-with-ineligible-promotion\n"
    "cgroup_migration=pre-add-guard\n"
)

print("Phase106 Modern MGLRU P1 applied")
