#!/usr/bin/env python3
"""Read geometry and text straight out of a vector PDF page (in PDF points, y down), optionally in world metres.

Subcommands
  layers FILE [--page N]                       CAD layers (OCGs) with item counts per colour/width
  text   FILE [--page N] [--regex RE] [--clip x0 y0 x1 y1]
                                               words/lines with bbox centres: find grid labels, level tags, dims
  dump   FILE [--page N] [--layer NAME] [--color R,G,B] [--ctol 0.15] [--min-width W] [--max-width W]
              [--clip x0 y0 x1 y1] [--calib calibration.json --id SHEET] [--out items.json]
                                               lines / curves (sampled) / rects / quads as point lists
  circle FILE  (same filters as dump)          least-squares circle through all selected points
                                               (e.g. a curved facade, a column, a round core)

With --calib/--id every point is also reported in world metres using that sheet's calibration record.
Colours are 0..1 floats as pymupdf reports them. Run `layers` first: CAD exports usually keep
layers like A-WALL, S-COLS, A-GLAZ, which make selection far more reliable than colour.
"""
import argparse, json, re, sys
from pathlib import Path

import numpy as np
import pymupdf

sys.path.insert(0, str(Path(__file__).parent))
from bmb_common import fit_circle, load_calibration, world_to_pt, invert, apply


def _bezier(p0, p1, p2, p3, n=12):
    t = np.linspace(0, 1, n)[:, None]
    P = [np.array([p.x, p.y]) for p in (p0, p1, p2, p3)]
    return ((1 - t) ** 3 * P[0] + 3 * (1 - t) ** 2 * t * P[1] + 3 * (1 - t) * t ** 2 * P[2] + t ** 3 * P[3]).tolist()


def items(page, a):
    clip = pymupdf.Rect(*a.clip) if a.clip else None
    want = np.array([float(v) for v in a.color.split(',')]) if a.color else None
    out = []
    for d in page.get_drawings():
        if a.layer and d.get('layer') != a.layer:
            continue
        col = d.get('color') or d.get('fill')
        if want is not None:
            if col is None or np.abs(np.array(col[:3]) - want).max() > a.ctol:
                continue
        w = d.get('width') or 0.0
        if a.min_width is not None and w < a.min_width:
            continue
        if a.max_width is not None and w > a.max_width:
            continue
        if clip and not clip.intersects(d['rect']):
            continue
        for it in d['items']:
            k = it[0]
            if k == 'l':
                pts = [[it[1].x, it[1].y], [it[2].x, it[2].y]]
            elif k == 'c':
                pts = _bezier(*it[1:5])
            elif k == 're':
                r = it[1]; pts = [[r.x0, r.y0], [r.x1, r.y0], [r.x1, r.y1], [r.x0, r.y1], [r.x0, r.y0]]
            elif k == 'qu':
                q = it[1]; pts = [[p.x, p.y] for p in (q.ul, q.ur, q.lr, q.ll, q.ul)]
            else:
                continue
            out.append(dict(kind=k, pts=pts, color=col, width=w, layer=d.get('layer'), closed=bool(d.get('closePath'))))
    return out


def to_world(pts, a):
    if not a.calib:
        return None
    rec = load_calibration(a.calib)[a.id]
    Minv = invert(world_to_pt(rec))
    P = np.asarray(pts, float)
    X, Y = apply(Minv, P[:, 0], P[:, 1])
    return np.c_[X, Y].round(4).tolist()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['layers', 'text', 'dump', 'circle'])
    ap.add_argument('file'); ap.add_argument('--page', type=int, default=0)
    ap.add_argument('--regex'); ap.add_argument('--layer'); ap.add_argument('--color'); ap.add_argument('--ctol', type=float, default=0.15)
    ap.add_argument('--min-width', type=float); ap.add_argument('--max-width', type=float)
    ap.add_argument('--clip', type=float, nargs=4); ap.add_argument('--calib'); ap.add_argument('--id'); ap.add_argument('--out')
    a = ap.parse_args()
    page = pymupdf.open(a.file)[a.page]

    if a.cmd == 'layers':
        stats = {}
        for d in page.get_drawings():
            key = (d.get('layer'), tuple(round(c, 2) for c in (d.get('color') or ())), round(d.get('width') or 0, 2))
            stats[key] = stats.get(key, 0) + len(d['items'])
        for (lay, col, w), n in sorted(stats.items(), key=lambda kv: -kv[1])[:80]:
            print(f'{str(lay):30s} color={col} width={w:<6} items={n}')
        return

    if a.cmd == 'text':
        rx = re.compile(a.regex) if a.regex else None
        clip = pymupdf.Rect(*a.clip) if a.clip else None
        rows = []
        for x0, y0, x1, y1, w, *_ in page.get_text('words', clip=clip):
            if rx and not rx.search(w):
                continue
            rows.append(dict(text=w, cx=round((x0 + x1) / 2, 2), cy=round((y0 + y1) / 2, 2), bbox=[round(v, 2) for v in (x0, y0, x1, y1)]))
        for r in rows:
            if a.calib:
                r['world'] = to_world([[r['cx'], r['cy']]], a)[0]
        out = json.dumps(rows, indent=1)
        (Path(a.out).write_text(out) if a.out else print(out))
        return

    its = items(page, a)
    if a.cmd == 'dump':
        for it in its:
            w = to_world(it['pts'], a)
            if w:
                it['world'] = w
        out = json.dumps(dict(file=a.file, page=a.page, n=len(its), items=its), indent=1)
        (Path(a.out).write_text(out) if a.out else print(out[:20000]))
        print(f'{len(its)} items', file=sys.stderr)
    else:
        P = np.array([p for it in its for p in it['pts']])
        if len(P) < 5:
            sys.exit('not enough points selected for a circle fit')
        cx, cy, r, rms = fit_circle(P)
        res = dict(centre_pt=[cx, cy], r_pt=r, rms_pt=rms, n_points=len(P))
        if a.calib:
            rec = load_calibration(a.calib)[a.id]
            res['centre_world'] = to_world([[cx, cy]], a)[0]
            res['r_m'] = r * rec['m_per_pt']; res['rms_m'] = rms * rec['m_per_pt']
        print(json.dumps(res, indent=1))


if __name__ == '__main__':
    main()
