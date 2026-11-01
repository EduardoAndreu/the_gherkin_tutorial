"""Decisions-log lint: every ASSUMPTION a zone declares and every open issue (markup `issue` callouts + the source spec's
unresolved open_issues) must have a decision line in decisions_log.md. Numbers before eyes, decisions before freezes.

decisions_log.md format (templates/decisions_log.md): markdown tables whose first column is the id:
  | OI-17 | decision ... | reason ... |        open issue decided
  | A-STR-01 | assumed_slab_depth_m | 0.15 | SSL (down) | reason |     assumption
  | C-01 | sources | values | adopted | reason |                          conflict between sources
Run: python3 -m pipeline.run decisions    (exit 1 when something is missing; writes build/passes/decisions_report.json)
"""
import json, os, re
from pipeline.aep_spec import out, zone_module


def _ids_in_log(path):
    if not os.path.exists(path):
        return set()
    ids = set()
    with open(path) as f:
        for line in f:
            m = re.match(r"\s*\|\s*([A-Za-z]{1,6}-[A-Za-z0-9]{1,6}(?:-\d+[a-z]?)?|OI-\d+)\s*\|", line)
            if m:
                ids.add(m.group(1))
    return ids


def lint(spec, zones=None):
    logp = os.path.join(spec["_root"], spec.get("decisions_log", "decisions_log.md"))
    have = _ids_in_log(logp)
    missing, assumptions = [], {}
    for zn in (zones or spec["zones"]):
        try:
            mod = zone_module(spec, zn)
        except Exception as e:  # a zone that does not import yet is a Stage 01 problem, not a decisions one
            print(f"[decisions] zone {zn}: cannot import ({e})"); continue
        for aid, a in (getattr(mod, "ASSUMPTIONS", {}) or {}).items():
            assumptions[aid] = dict(a, zone=zn)
            if aid not in have:
                missing.append(dict(id=aid, kind="assumption", zone=zn, text=f"{a.get('key')} = {a.get('value')} ({a.get('reason', '')})"))
    regp = out(spec, "passes", "registers.json")
    if os.path.exists(regp):
        with open(regp) as f:
            regs = json.load(f)
        for iid, refs in regs.get("issues", {}).items():
            if iid not in have:
                missing.append(dict(id=iid, kind="open_issue", zone=None, text=refs[0].get("text", "")))
    else:
        print("[decisions] no registers.json yet: run `pipeline.run checklist` first to register the markup issues")
    rep = dict(log=os.path.relpath(logp, spec["_root"]), ids_in_log=sorted(have), assumptions=assumptions, missing=missing)
    with open(out(spec, "passes", "decisions_report.json"), "w") as f:
        json.dump(rep, f, indent=1)
    with open(out(spec, "assumptions.json"), "w") as f:
        json.dump(assumptions, f, indent=1)
    if missing:
        print(f"[decisions] {len(missing)} undecided items (see build/passes/decisions_report.json):")
        for m in missing[:40]:
            print(f"   {m['id']:<14} {m['kind']:<11} {m['text'][:100]}")
    else:
        print(f"[decisions] ok: {len(have)} decided ids cover {len(assumptions)} assumptions and all registered issues")
    return rep
