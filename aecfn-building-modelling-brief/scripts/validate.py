#!/usr/bin/env python3
"""Check a modelling brief before it is handed over. Exit code 1 if any ERROR.

  validate.py --spec spec/spec.json [--markups markups/] [--underlays underlays/] [--brief BRIEF_DIR]

spec      : parses; required top-level sections; every V object ({"value":..}) has src.file + src.method;
            bare numbers inside measured sections are reported (WARN) so they get provenance;
            open_issues all have id + status; assumptions have a reason.
markups   : every sidecar item's spec keys still resolve and still equal the stored values (catches stale
            markups after a spec change); measured items have no literal digits in their label template
            (numbers must come from {placeholders}); non-measured items cite a source; spec_version matches.
underlays : sidecar <-> PNG consistency (size, metres_per_px > 0).
brief     : no absolute paths or scratch paths in .md/.json; files named in the READMEs exist in the brief.
"""
import argparse, json, re, sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from bmb_common import get_many, is_v, load_json, SpecKeyError

REQUIRED = ['schema_version', 'project', 'coordinate_system', 'levels', 'open_issues']
MEASURED_SECTIONS = ['levels', 'grids', 'structure', 'envelope', 'floorplates', 'interior', 'site']
errs, warns = [], []


def E(m): errs.append(m)
def W(m): warns.append(m)


def walk(o, path, fn):
    if fn(o, path) == 'stop':
        return
    if isinstance(o, dict):
        for k, v in o.items():
            walk(v, f'{path}.{k}' if path else k, fn)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            walk(v, f'{path}[{i}]', fn)


def check_spec(spec):
    for k in REQUIRED:
        if k not in spec:
            E(f'spec: missing top-level "{k}"')
    bare = []

    def fn(o, p):
        top = p.split('.')[0].split('[')[0]
        if top in ('assumptions', 'open_issues', 'changelog', 'sanity_checks', 'project', 'calibration_summary'):
            return 'stop'                       # not measured data: own rules below
        if isinstance(o, dict) and ('src' in o or 'source' in o) and not is_v(o):
            return 'stop'                       # object carrying its own provenance (e.g. a polygon with src)
        if is_v(o):
            src = o.get('src') or {}
            if not (src.get('file') or src.get('sheet')) or not src.get('method'):
                E(f'spec: {p} V-object lacks src.file/sheet + src.method')
            return 'stop'                       # numbers inside a V object are its value / uncertainty
        if isinstance(o, (int, float)) and not isinstance(o, bool):
            leaf = re.sub(r'\[\d+\]$', '', p.rsplit('.', 1)[-1])
            if top in MEASURED_SECTIONS and leaf not in ('id', 'n', 'count', 'index', 'order'):
                bare.append(p)
    walk(spec, '', fn)
    if bare:
        W(f'spec: {len(bare)} bare numbers in measured sections (give them src via a V object or a sibling "src"), e.g. ' + ', '.join(bare[:6]))
    for i, oi in enumerate(spec.get('open_issues', [])):
        if not isinstance(oi, dict) or not oi.get('id') or not oi.get('status'):
            E(f'spec: open_issues[{i}] needs id and status')
    for i, a in enumerate(spec.get('assumptions', [])):
        if not isinstance(a, dict) or not a.get('reason'):
            E(f'spec: assumptions[{i}] needs a reason')


def _eq(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= 1e-9 * max(1, abs(a))
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_eq(x, y) for x, y in zip(a, b))
    return a == b


def check_markups(spec, mdir):
    sv = spec.get('schema_version') or spec.get('version')
    n = 0
    for sc in sorted(Path(mdir).rglob('*.json')):
        d = load_json(sc)
        if 'items' not in d:
            continue
        n += 1
        if str(d.get('spec_version')) != str(sv):
            E(f'{sc.name}: rendered against spec v{d.get("spec_version")}, spec is v{sv} - re-render')
        if not (sc.with_name(d['file'])).exists():
            E(f'{sc.name}: image {d["file"]} missing')
        for it in d['items']:
            tag = f"{sc.name} #{it['n']}"
            for name, key in (it.get('keys') or {}).items():
                try:
                    cur = get_many(spec, key)
                except SpecKeyError as e:
                    E(f'{tag}: key no longer resolves: {e}'); continue
                vkey = name if name in it.get('values', {}) else None
                if vkey and not _eq(cur, it['values'][vkey]):
                    E(f'{tag}: {key} is now {cur!r}, markup shows {it["values"][name]!r} - re-render')
            tpl = it.get('label_template') or ''
            literal = re.sub(r'\{[^}]*\}', '', tpl)
            # a literal *measurement* = decimal number, or a number with a unit; IDs like L20, B1, Grid 3, 3/5 are fine
            meas = re.findall(r'(?<![A-Za-z\d./-])(\d+\.\d+|\d+(?=\s?(?:mm|m|deg|°|%)\b))', literal)
            if it['kind'] == 'measured' and meas:
                W(f'{tag}: measured label has literal measurement(s) {meas} in "{tpl}" - take them from the spec via placeholders')
            if it['kind'] in ('assumed', 'observed') and not it.get('source'):
                E(f'{tag}: {it["kind"]} item without source')
            if it['kind'] == 'measured' and not it.get('keys') and it['type'] not in ('note', 'issue'):
                W(f'{tag}: measured item with no spec key (literal_ok?)')
    if n == 0:
        W(f'markups: no sidecars found in {mdir}')
    return n


def check_underlays(udir):
    n = 0
    for sc in sorted(Path(udir).glob('*.json')):
        m = load_json(sc); n += 1
        p = sc.with_name(m['file'])
        if not p.exists():
            E(f'{sc.name}: {m["file"]} missing'); continue
        with Image.open(p) as im:
            if list(im.size) != list(m['size_px']):
                E(f'{sc.name}: size_px {m["size_px"]} != image {list(im.size)}')
        if not m.get('metres_per_px', 0) > 0:
            E(f'{sc.name}: bad metres_per_px')
    return n


def check_brief(bdir):
    b = Path(bdir)
    names = {p.name for p in b.rglob('*')} | {str(p.relative_to(b)) for p in b.rglob('*')}
    for f in list(b.rglob('*.md')) + list(b.rglob('*.json')):
        t = f.read_text(errors='ignore')
        for m in re.finditer(r'(/Users/|/Volumes/|/private/tmp|/tmp/|scratchpad|C:\\\\)', t):
            W(f'{f.relative_to(b)}: contains a local/absolute path near "{t[max(0, m.start() - 30):m.end() + 30]!r}"')
            break
    for f in list(b.glob('*.md')):
        for ref in re.findall(r'`([\w./\-]+\.(?:json|png|md|pdf|ifc|blend))`', f.read_text()):
            if '<' in ref or '*' in ref:
                continue
            if ref not in names and Path(ref).name not in names and not ref.startswith(('decisions_log', 'output')):
                W(f'{f.name}: mentions `{ref}` which is not in the brief')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--spec', required=True); ap.add_argument('--markups'); ap.add_argument('--underlays'); ap.add_argument('--brief')
    a = ap.parse_args()
    spec = load_json(a.spec)
    check_spec(spec)
    nm = check_markups(spec, a.markups) if a.markups else 0
    nu = check_underlays(a.underlays) if a.underlays else 0
    if a.brief:
        check_brief(a.brief)
    for w in warns:
        print('WARN ', w)
    for e in errs:
        print('ERROR', e)
    print(f'spec ok={not any(e.startswith("spec") for e in errs)}  markups={nm}  underlays={nu}  errors={len(errs)}  warnings={len(warns)}')
    sys.exit(1 if errs else 0)


if __name__ == '__main__':
    main()
