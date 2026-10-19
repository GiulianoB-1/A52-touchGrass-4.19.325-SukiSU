#!/usr/bin/env python3
"""Phase447B read-only observed /dev path -> three-layer adapter audit.

A log snippet mentioning /dev is not proof of a successful open(). Separate
kernel/SELinux denied attempts, open failures, syscall traces, static init
references and ls listings. Unmatched observed opens are blockers, never
silently assigned to a guessed provider.
"""
import argparse,csv,json,re
from collections import defaultdict,Counter
from pathlib import Path

COLS=("pattern","layer","function","preferred_source","source_path",
      "required_dependency","userspace_contract","nv_write_policy","status")
PATH_RE=re.compile(r"/dev/[a-zA-Z0-9_@:.,+*=-]+(?:/[a-zA-Z0-9_@:.,+*=-]+)*")
OPEN_RE=re.compile(r"\b(?:open|openat|openat2|fopen|couldn.t open|failed to open|cannot open|error opening)\b",re.I)
MISSING_RE=re.compile(r"(?:no such file|ENOENT|permission denied|EACCES|open fail|cannot open|couldn.t open|unable to open)",re.I)
TRACE_RE=re.compile(r"(?:sys_enter_openat2?|sys_exit_openat2?|strace:|openat2?\()",re.I)
INIT_RE=re.compile(r"(?:\.rc:|init.*service|ueventd|/vendor/etc/init|/odm/etc/init)",re.I)
MAX_BYTES=8_000_000
MAX_FILES=800

def verify_map(p):
    with p.open(newline="",encoding="utf-8-sig") as f:
        reader=csv.DictReader(f)
        if tuple(reader.fieldnames or ())!=COLS:
            raise RuntimeError("P447B node map header mismatch")
        rows=list(reader)
    if not rows:raise RuntimeError("P447B no contract mappings")
    seen=set()
    for r in rows:
        if r["pattern"] in seen:raise RuntimeError("P447B duplicate rule "+r["pattern"])
        seen.add(r["pattern"])
        try:re.compile(r["pattern"])
        except re.error as e:raise RuntimeError("P447B invalid node pattern "+str(e))
        if r["layer"] not in ("L1","L2","L3"):
            raise RuntimeError("P447B invalid layer "+r["layer"])
        if not r["nv_write_policy"].strip():
            raise RuntimeError("P447B NV write policy missing for "+r["pattern"])
    print(f"P447B DEV MAP PASS nodes={len(rows)} three_layers=1 unresolved_explicit=1")
    return [(re.compile(r["pattern"]),r) for r in rows]

def classify(line,name):
    if line.startswith("Command:"):return "collection-command"
    if name=="vendor-dev-nodes.txt" and ("->" in line or "No such file" in line
                                          or "cannot access" in line.lower()):
        return "node-listing"
    if ("avc: denied" in line or "avc : denied" in line) and (
            "open" in line or "read" in line or "write" in line):
        return "avc-denied"
    if TRACE_RE.search(line):
        return "open-syscall-trace"
    if OPEN_RE.search(line):
        return "open-failure" if MISSING_RE.search(line) else "open-mentioned"
    if INIT_RE.search(line):return "init-reference"
    return "other-reference"

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input",type=Path,help="existing P447B capture directory")
    p.add_argument("--map",type=Path,default=Path("merge/userspace-nodes.csv"))
    p.add_argument("--out",type=Path,default=Path("phase447b-devnode-audit"))
    p.add_argument("--strict",action="store_true",
                   help="exit nonzero if an actual attempted-open path is unmapped")
    p.add_argument("--verify-only",action="store_true",
                   help="validate mapping schema without device logs")
    args=p.parse_args()
    mapping=verify_map(args.map)
    if args.verify_only:return
    if not args.input or not args.input.is_dir():
        p.error("--input must be an existing capture directory")
    evidence=defaultdict(list)
    count=0
    for fp in sorted(args.input.rglob("*")):
        if not fp.is_file() or fp.is_symlink() or fp.suffix.lower() not in (".txt",".log",".rc"):
            continue
        if fp.stat().st_size>MAX_BYTES:continue
        count+=1
        if count>MAX_FILES:raise RuntimeError("P447B capture has too many files")
        for lineno,line in enumerate(fp.read_text(encoding="utf-8-sig",errors="replace").splitlines(),1):
            paths={x.group(0).rstrip(",.=:;") for x in PATH_RE.finditer(line)}
            if not paths:continue
            cat=classify(line,fp.name)
            for path in paths:
                if path=="/dev/" or len(path)>160:continue
                evidence[path].append({"file":str(fp.relative_to(args.input)),
                                       "line":lineno,"kind":cat,
                                       "excerpt":line.strip()[:220]})
    args.out.mkdir(parents=True,exist_ok=True)
    result=[];unmapped=[];all_counts=Counter()
    for path,items in sorted(evidence.items()):
        m=next((r for pat,r in mapping if pat.fullmatch(path)),None)
        attempted=[x for x in items if x["kind"] in
                   ("open-failure","open-mentioned","avc-denied","open-syscall-trace")]
        categories=Counter(x["kind"] for x in items)
        for k,v in categories.items():all_counts[k]+=v
        row={
            "path":path,"layer":m["layer"] if m else "UNMAPPED",
            "function":m["function"] if m else "UNMAPPED",
            "source":m["preferred_source"] if m else "REQUIRES_INVESTIGATION",
            "state":m["status"] if m else "BLOCKER",
            "attempt_evidence_count":len(attempted),
            "mention_evidence_count":len(items),
            "categories":dict(categories),
            "evidence":items[:35]}
        result.append(row)
        if attempted and not m:unmapped.append(row)
    fields=["path","layer","function","source","state","attempt_evidence_count",
            "mention_evidence_count","categories","first_evidence"]
    with (args.out/"observed-dev-paths.csv").open("w",newline="",encoding="utf-8") as f:
        writer=csv.writer(f);writer.writerow(fields)
        for r in result:
            writer.writerow([r["path"],r["layer"],r["function"],r["source"],r["state"],
                             r["attempt_evidence_count"],r["mention_evidence_count"],
                             json.dumps(r["categories"],sort_keys=True),
                             json.dumps(r["evidence"][0],sort_keys=True)])
    with (args.out/"unmapped-open-attempts.csv").open("w",newline="",encoding="utf-8") as f:
        writer=csv.writer(f);writer.writerow(fields)
        for r in unmapped:
            writer.writerow([r["path"],r["layer"],r["function"],r["source"],r["state"],
                             r["attempt_evidence_count"],r["mention_evidence_count"],
                             json.dumps(r["categories"],sort_keys=True),
                             json.dumps(r["evidence"][0],sort_keys=True)])
    summary={
        "scanned_log_files":count,"unique_dev_paths_mentioned":len(result),
        "unique_dev_paths_with_open_or_avc_evidence":sum(r["attempt_evidence_count"]>0 for r in result),
        "unmapped_open_attempts":len(unmapped),"evidence_types":dict(all_counts),
        "caveat":"logcat/dmesg can miss successful opens; no negative coverage assertion without process-scoped syscall traces",
        "observations":result,
    }
    (args.out/"observed-dev-paths.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    print(f"P447B DEV AUDIT scanned={count} mentioned={len(result)} unmapped_open={len(unmapped)}")
    for u in unmapped:print("P447B UNMAPPED OPEN "+u["path"])
    if args.strict and unmapped:raise SystemExit(2)
if __name__=="__main__":main()
