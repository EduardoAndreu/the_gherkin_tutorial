"""Markups -> per-domain checklists and the open-issue / assumption registers.

Reads brief markups/<SET>/*.json (callouts with kind measured|assumed|observed|issue, spec_key, spec_key_query, value,
issue_id) and writes, per zone whose spec entry has "markups": "<SET>":
  build/passes/domain_<zone>.md     what the zone must contain, with the spec key of every measured value, the sheet to
                                    read it on, which values are ASSUMED (must be logged), which are PHOTO-only
  build/passes/registers.json       {"issues": {issue_id: [...]}, "assumed": {callout_id: text}, "sets": {...}}
Run: python3 -m pipeline.run checklist
"""
import glob, json, os, re
from pipeline.aep_spec import brief_path, out


def read_set(spec, set_name):
    d = brief_path(spec, "markups", set_name)
    sheets = []
    for jp in sorted(glob.glob(os.path.join(d, "*.json"))):
        with open(jp) as f:
            m = json.load(f)
        m["_json"] = jp
        m["_png"] = os.path.join(d, m.get("file", os.path.basename(jp)[:-5] + ".png"))
        m["_preview"] = os.path.join(d, os.path.basename(jp)[:-5] + "_preview.png")
        sheets.append(m)
    return sheets


def _issue_ids(text):
    return re.findall(r"\bOI-\d+\b|\b[A-Z]{2,4}-[A-Z]{2,4}-\d+\b", text or "")


def build(spec):
    regs = dict(issues={}, assumed={}, sets={})
    for zone, z in spec["zones"].items():
        set_name = z.get("markups")
        if not set_name:
            continue
        sheets = read_set(spec, set_name)
        if not sheets:
            print(f"[checklist] zone {zone}: no markups in set {set_name!r}")
            continue
        lines = [f"# Domain checklist — {zone} (markup set {set_name})", "",
                 "Ground truth for this specialist: the markups below, in this order. Every MEASURED row cites the spec key",
                 "the number must come from (read it with `python3 -m pipeline.run q \"<spec_key_query>\"`). ASSUMED rows need a",
                 "line in decisions_log.md. PHOTO/OBSERVED rows are look-and-assembly only (never dimensions). ISSUE rows need a",
                 "logged decision before the zone can be frozen.", ""]
        n_meas = n_ass = n_obs = n_iss = 0
        for m in sheets:
            lines += [f"## {os.path.basename(m['_png'])}", f"- sheet: {m.get('sheet', '')} · era: {m.get('era', '')} · layer: {m.get('layer', '')}",
                      f"- read: `{os.path.relpath(m['_preview'], spec['_root'])}` first, `{os.path.relpath(m['_png'], spec['_root'])}` to zoom",
                      f"- transform: {m.get('transform', {}).get('kind', '?')} ({m.get('transform', {}).get('note', '')})", ""]
            geo = m.get("geometry", [])
            if geo:
                lines.append("| geometry id | cls | kind | spec keys |"); lines.append("|---|---|---|---|")
                for g in geo:
                    lines.append(f"| {g.get('id')} | {g.get('cls')} | {g.get('kind')} | {', '.join(g.get('spec_keys_query') or g.get('spec_keys') or [])} |")
                lines.append("")
            lines.append("| id | kind | cls | text | spec key | value |"); lines.append("|---|---|---|---|---|---|")
            for c in m.get("callouts", []):
                kind = c.get("kind", "")
                txt = " ".join((c.get("text") or "").split())[:160]
                key = c.get("spec_key_query") or c.get("spec_key") or (c.get("source") or "")[:80]
                val = c.get("value")
                lines.append(f"| {c.get('id')} | {kind} | {c.get('cls') or ''} | {txt} | `{key}` | {'' if val is None else val} |")
                if kind == "measured":
                    n_meas += 1
                elif kind == "assumed":
                    n_ass += 1; regs["assumed"][c["id"]] = txt
                elif kind == "observed":
                    n_obs += 1
                elif kind == "issue":
                    n_iss += 1
                    for iid in set(_issue_ids(c.get("issue_id") or "") + _issue_ids(c.get("text") or "")):
                        regs["issues"].setdefault(iid, []).append(dict(callout=c["id"], sheet=os.path.basename(m["_png"]), text=txt))
            lines.append("")
        lines.insert(6, f"Counts: {n_meas} measured · {n_ass} assumed · {n_obs} observed/photo · {n_iss} issue callouts across {len(sheets)} sheets.\n")
        p = out(spec, "passes", f"domain_{zone}.md")
        with open(p, "w") as f:
            f.write("\n".join(lines))
        regs["sets"][set_name] = dict(zone=zone, sheets=[os.path.basename(m["_png"]) for m in sheets], measured=n_meas, assumed=n_ass, observed=n_obs, issues=n_iss)
        print(f"[checklist] {zone}: {len(sheets)} sheets, {n_meas} measured, {n_ass} assumed, {n_obs} observed, {n_iss} issue callouts -> {os.path.relpath(p, spec['_root'])}")
    # open issues from the source spec itself (unresolved ones need a decision too)
    for n, oi in enumerate(spec["_src"].get("open_issues", []) or []):
        if isinstance(oi, dict) and not str(oi.get("status", "")).lower().startswith("resolved"):
            regs["issues"].setdefault(f"OI-{n}", []).append(dict(callout=None, sheet="source_spec.open_issues", text=(oi.get("issue") or "")[:160], domain=oi.get("domain")))
    with open(out(spec, "passes", "registers.json"), "w") as f:
        json.dump(regs, f, indent=1)
    return regs
