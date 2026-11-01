#!/usr/bin/env python3
"""Create / check calibration records that map world metres <-> PDF page points for each sheet.

World frame (decide once per project, write it in spec.coordinate_system):
  plans:              (X, Y) metres, Z up; origin and axes fixed to the building (e.g. grid A/1, axes along the grid)
  sections/elevations:(s, z) metres; s = horizontal distance along the cut/view line, z = 0 at the project datum
Each record stores world_to_pt (2x3) so  pt = M @ [a, b, 1].  PDF points are pymupdf's (origin top-left, y down).

Subcommands
  plan     --id ID --file F [--page N] --scale 100 [--stated-paper A1]
           --ref "LABEL:px,py=X,Y" (one or more)  [--rotation-deg R] [--fit similarity|affine]
           [--check "xa,ya:xb,yb=LENGTH_M"]...  [--era ...] [--level L02] [--note ...]
      1 ref  -> uses the stated scale and --rotation-deg (angle of world +X on the page, CCW, default 0)
      2+ refs-> least-squares similarity (or affine with 3+); fitted scale is compared with the stated scale
  section  --id ID --file F [--page N] --scale 100 [--stated-paper A1] --kind section|elevation
           --zref "py=Z" (one or more, e.g. level tags)  --sref "px=S" (one or more, e.g. grid lines)
           [--line "X0,Y0,AZIMUTH_DEG"]  (where the section/view line sits in plan: origin of s and direction of +s)
           [--check ...]
  autogrid --id ID --file F --bubbles bubbles.json --xgrid "1:0,2:8,..." --ygrid "A:0,B:-7.5,..." [--scale 100]
           grid bubbles (raster_tools.py bubbles, or text positions) are matched to the known grid spacing automatically
           and an affine is fitted through every grid intersection; reports X/Y anisotropy (scans are often stretched)
  check    --id ID --check "xa,ya:xb,yb=LENGTH_M"   measure a known dimension with an existing record
  intersect "x1,y1,x2,y2" "x3,y3,x4,y4"             intersection of two lines in page points (grid crossings)
  list

Records go to --db (default ./calibration.json). Re-running with the same --id replaces the record.
"""
import argparse, datetime, math, os, sys
from pathlib import Path

import numpy as np
import pymupdf

sys.path.insert(0, str(Path(__file__).parent))
from bmb_common import (iso_size, m_per_pt, PT_TO_MM, similarity_from_pairs, affine_from_pairs, apply, invert,
                        load_json, save_json)


def _pair(s):
    a, b = s.split('=')
    lab, _, xy = a.rpartition(':')
    return lab or None, [float(v) for v in xy.split(',')], [float(v) for v in b.split(',')]


def _check(s):
    a, L = s.split('=')
    pa, pb = a.split(':')
    return [float(v) for v in pa.split(',')], [float(v) for v in pb.split(',')], float(L)


def _db(path):
    p = Path(path)
    return load_json(p) if p.exists() else {'schema_version': '1.0', 'records': []}


def _store(db, rec, path):
    db['records'] = [r for r in db['records'] if r['id'] != rec['id']] + [rec]
    db['generated'] = datetime.datetime.now().isoformat(timespec='seconds')
    save_json(db, path)


def _page_info(f, page):
    pg = pymupdf.open(f)[page]
    w, h = pg.rect.width * PT_TO_MM, pg.rect.height * PT_TO_MM
    return iso_size(w, h)[0], [round(w, 1), round(h, 1)]


def _checks(M, checks):
    Minv = invert(M); out = []
    for pa, pb, L in checks:
        Xa, Ya = apply(Minv, *pa); Xb, Yb = apply(Minv, *pb)
        meas = float(math.hypot(Xb - Xa, Yb - Ya))
        out.append(dict(pt_a=pa, pt_b=pb, stated_m=L, measured_m=round(meas, 4), err_m=round(meas - L, 4),
                        err_pct=round(100 * (meas - L) / L, 3)))
    return out


def cmd_plan(a):
    paper, size = _page_info(a.file, a.page)
    mpp, note = m_per_pt(a.scale, paper, a.stated_paper)
    refs = [_pair(r) for r in a.ref]
    if len(refs) == 1:
        lab, pt, w = refs[0]
        k = 1 / mpp; t = math.radians(a.rotation_deg)
        # world +X at angle t (CCW on paper); page y points down -> flip
        A = np.array([[k * math.cos(t), -k * math.sin(t)], [-k * math.sin(t), -k * math.cos(t)]])
        b = np.array(pt) - A @ np.array(w)
        M = np.c_[A, b]; rms = 0.0; method = 'stated scale + 1 reference point + rotation'
    else:
        W = [r[2] for r in refs]; P = [r[1] for r in refs]
        Wf = np.array(W) * [1, -1]  # fit a similarity on a y-flipped world, then fold the flip back in
        if a.fit == 'affine' and len(refs) >= 3:
            M, rms_pt = affine_from_pairs(W, P); method = f'affine fit to {len(refs)} reference points'
        else:
            S, rms_pt = similarity_from_pairs(Wf, P)
            M = np.c_[S[:, :2] @ np.diag([1, -1]), S[:, 2]]; method = f'similarity fit to {len(refs)} reference points'
        fitted_mpp = 1 / math.sqrt(abs(np.linalg.det(M[:, :2])))
        rms = rms_pt * fitted_mpp
        dev = 100 * (fitted_mpp - mpp) / mpp
        method += f'; fitted scale deviates {dev:+.3f}% from stated 1:{a.scale}'
        if abs(dev) > 0.5:
            print(f'WARNING: fitted scale differs from stated scale by {dev:+.2f}% - check references, page size, or "@ paper" note',
                  file=sys.stderr)
        mpp = fitted_mpp
    rot = math.degrees(math.atan2(-M[1, 0], M[0, 0]))
    rec = dict(id=a.id, file=os.path.abspath(a.file), page=a.page, kind='plan', level=a.level, era=a.era,
               stated_scale=dict(denominator=a.scale, paper=a.stated_paper), page_paper=paper, page_size_mm=size,
               m_per_pt=mpp, scale_note=note, rotation_deg=round(rot, 5), method=method,
               references=[dict(label=l, pt=p, world=w) for l, p, w in refs],
               world_to_pt=M.tolist(), rms_m=round(rms, 4), checks=_checks(M, [_check(c) for c in a.check or []]),
               note=a.note)
    return rec


def cmd_section(a):
    paper, size = _page_info(a.file, a.page)
    mpp, note = m_per_pt(a.scale, paper, a.stated_paper)
    z = [tuple(float(v) for v in s.split('=')) for s in a.zref]
    s = [tuple(float(v) for v in t.split('=')) for t in a.sref]
    if len(z) >= 2:
        py, zz = np.array(z).T; kz, cz = np.polyfit(zz, py, 1)  # py = kz*z + cz  (kz < 0)
        rz = float(np.sqrt(np.mean((np.polyval([kz, cz], zz) - py) ** 2)) * abs(1 / kz))
    else:
        kz = -1 / mpp; cz = z[0][0] - kz * z[0][1]; rz = 0.0
    if len(s) >= 2:
        px, ss = np.array(s).T; ks, cs = np.polyfit(ss, px, 1)
    else:
        ks = 1 / mpp; cs = s[0][0] - ks * s[0][1]
    M = np.array([[ks, 0, cs], [0, kz, cz]])
    if ks < 0:
        print('WARNING: +s runs right-to-left on this sheet, so the rectified underlay would be MIRRORED. '
              'Flip the sign of your s values (and the --line azimuth by 180 deg) so +s runs sheet-right.', file=sys.stderr)
    devz = 100 * ((1 / abs(kz)) - mpp) / mpp; devs = 100 * ((1 / abs(ks)) - mpp) / mpp
    if max(abs(devz), abs(devs)) > 0.5:
        print(f'WARNING: fitted scales deviate from stated (z {devz:+.2f}%, s {devs:+.2f}%)', file=sys.stderr)
    line = None
    if a.line:
        x0, y0, az = [float(v) for v in a.line.split(',')]
        line = dict(origin_world=[x0, y0], azimuth_deg=az,
                    note='s = distance from origin_world along azimuth (deg CCW from world +X); plane is vertical')
    rec = dict(id=a.id, file=os.path.abspath(a.file), page=a.page, kind=a.kind, level=None, era=a.era,
               stated_scale=dict(denominator=a.scale, paper=a.stated_paper), page_paper=paper, page_size_mm=size,
               m_per_pt=mpp, scale_note=note, m_per_pt_s=1 / abs(ks), m_per_pt_z=1 / abs(kz),
               anisotropy_pct=round(100 * (1 / abs(ks) - 1 / abs(kz)) / (1 / abs(kz)), 3), s_increases='sheet-right' if ks > 0 else 'sheet-left',
               method=f'linear fits: z from {len(z)} level refs (dev {devz:+.3f}%), s from {len(s)} refs (dev {devs:+.3f}%)',
               references=dict(z=z, s=s), section_line=line, world_to_pt=M.tolist(), rms_m=round(rz, 4),
               checks=[], note=a.note)
    rec['checks'] = _checks(M, [_check(c) for c in a.check or []])
    return rec


def _match_1d(pos, grid, tol_frac=0.004, min_inliers=3, scale_hint=None, anchor=None):
    """Assign detected bubble positions (pt, one axis) to known grid coordinates (m) by robust 1-D fitting.
    pos: list of floats; grid: {label: metres}. Returns ({label: pos}, k_pt_per_m, offset, rms_pt)."""
    labs = list(grid); G = np.array([grid[l] for l in labs]); P = np.array(sorted(pos))
    span_m = G.max() - G.min()
    best = None
    for i in range(len(P)):
        for j in range(i + 1, len(P)):
            for p in range(len(G)):
                for q in range(len(G)):
                    if G[p] == G[q]:
                        continue
                    k = (P[j] - P[i]) / (G[q] - G[p]); c = P[i] - k * G[p]
                    if scale_hint and not (0.85 < abs(k) / scale_hint < 1.15):
                        continue
                    tol = tol_frac * abs(k) * span_m + 1.0
                    pred = k * G + c
                    if anchor and abs(pred[labs.index(anchor[0])] - anchor[1]) > (anchor[2] if len(anchor) > 2 else tol):
                        continue
                    d = np.abs(P[:, None] - pred[None, :])
                    hits = {labs[g]: P[n] for n, g in enumerate(d.argmin(1)) if d[n, g] < tol}
                    if len(hits) >= min_inliers and (best is None or len(hits) > len(best[0])):
                        best = (hits, k, c)
    if not best:
        return None
    hits, _, _ = best
    gx = np.array([grid[l] for l in hits]); px = np.array(list(hits.values()))
    k, c = np.polyfit(gx, px, 1); rms = float(np.sqrt(np.mean((np.polyval([k, c], gx) - px) ** 2)))
    return hits, float(k), float(c), rms


def cmd_autogrid(a):
    """Plans with grid bubbles: bubbles.json from raster_tools.py (or text positions from pdf_vectors.py) +
    known grid coordinates -> labels assigned automatically -> affine fit through all grid intersections."""
    b = load_json(a.bubbles)
    if a.min_r_pt:
        b = [d for d in b if d.get('r_pt', 1e9) >= a.min_r_pt]
    pts = [(d['x'], d['y']) for d in b]
    ax = (a.anchor_x.split(':')[0], float(a.anchor_x.split(':')[1]), a.anchor_tol_pt) if a.anchor_x else None
    ay = (a.anchor_y.split(':')[0], float(a.anchor_y.split(':')[1]), a.anchor_tol_pt) if a.anchor_y else None
    xg = {k: float(v) for k, v in (s.split(':') for s in a.xgrid.split(','))}   # grids running N-S: label -> world X
    yg = {k: float(v) for k, v in (s.split(':') for s in a.ygrid.split(','))}   # grids running E-W: label -> world Y
    paper, size = _page_info(a.file, a.page)
    hint = 1 / m_per_pt(a.scale)[0] if a.scale else None
    pg = pymupdf.open(a.file)[a.page].rect
    band = a.band * min(pg.width, pg.height)
    xs = [x for x, y in pts if y < pg.y0 + band or y > pg.y1 - band]      # bubbles in top/bottom bands mark X grids
    ys = [y for x, y in pts if x < pg.x0 + band or x > pg.x1 - band]      # left/right bands mark Y grids
    mx = _match_1d(xs, xg, scale_hint=hint, anchor=ax); my = _match_1d(ys, yg, scale_hint=hint, anchor=ay)
    if not mx or not my:
        sys.exit(f'autogrid: could not match {"X" if not mx else "Y"} grid (detected {len(xs)} / {len(ys)} candidates)')
    (hx, kx, cx, rx), (hy, ky, cy, ry) = mx, my
    refs = [(f'{lx}/{ly}', [hx[lx], hy[ly]], [xg[lx], yg[ly]]) for lx in hx for ly in hy]
    W = [r[2] for r in refs]; P = [r[1] for r in refs]
    M, rms_pt = affine_from_pairs(W, P)
    sx_, sy_ = abs(M[0, 0]), abs(M[1, 1])
    mpp = 1 / math.sqrt(abs(np.linalg.det(M[:, :2])))
    stated = m_per_pt(a.scale, paper, a.stated_paper)[0] if a.scale else None
    aniso = 100 * (sx_ - sy_) / ((sx_ + sy_) / 2)
    dev = 100 * (mpp - stated) / stated if stated else 0.0
    if abs(aniso) > a.max_aniso or abs(dev) > a.max_scale_dev:
        sys.exit(f'autogrid REJECTED: anisotropy {aniso:+.2f}% / scale vs stated {dev:+.2f}% - labels are probably mis-assigned '
                 f'(off by one bay, or sub-grid bubbles mixed in). Give --anchor-x/--anchor-y from a visually read bubble, '
                 f'and/or --min-r-pt to drop small bubbles. Matched X={list(hx)} Y={list(hy)}')
    if not ax or not ay:
        print('WARNING: no --anchor-x/--anchor-y: on an evenly spaced grid a missing end bubble can shift every label by one bay. '
              'Check the matched labels against the bubble strip.', file=sys.stderr)
    rec = dict(id=a.id, file=os.path.abspath(a.file), page=a.page, kind='plan', level=a.level, era=a.era,
               stated_scale=dict(denominator=a.scale, paper=a.stated_paper), page_paper=paper, page_size_mm=size,
               m_per_pt=mpp, m_per_pt_x=1 / sx_, m_per_pt_y=1 / sy_, anisotropy_pct=round(aniso, 3),
               scale_vs_stated_pct=round(100 * (mpp - stated) / stated, 3) if stated else None,
               rotation_deg=0.0, method=(f'autogrid: {len(hx)} X-grid + {len(hy)} Y-grid bubbles matched to known grid '
                                         f'(1-D rms {rx:.2f}/{ry:.2f} pt), affine through {len(refs)} intersections. '
                                         'Assumes the sheet is deskewed (bubbles give one position per grid line).'),
               matched=dict(x={k: round(v, 2) for k, v in hx.items()}, y={k: round(v, 2) for k, v in hy.items()}),
               references=[dict(label=l, pt=p, world=w) for l, p, w in refs],
               world_to_pt=M.tolist(), rms_m=round(rms_pt * mpp, 4),
               checks=_checks(M, [_check(c) for c in a.check or []]), note=a.note)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['plan', 'section', 'autogrid', 'check', 'intersect', 'list'])
    ap.add_argument('--bubbles'); ap.add_argument('--anchor-x'); ap.add_argument('--anchor-y'); ap.add_argument('--min-r-pt', type=float); ap.add_argument('--anchor-tol-pt', type=float, default=60.0)
    ap.add_argument('--max-aniso', type=float, default=2.0); ap.add_argument('--max-scale-dev', type=float, default=4.0)
    ap.add_argument('--xgrid'); ap.add_argument('--ygrid'); ap.add_argument('--band', type=float, default=0.2)
    ap.add_argument('lines', nargs='*')
    ap.add_argument('--db', default='calibration.json')
    ap.add_argument('--id'); ap.add_argument('--file'); ap.add_argument('--page', type=int, default=0)
    ap.add_argument('--scale', type=float); ap.add_argument('--stated-paper')
    ap.add_argument('--ref', action='append'); ap.add_argument('--rotation-deg', type=float, default=0.0)
    ap.add_argument('--fit', default='similarity', choices=['similarity', 'affine'])
    ap.add_argument('--zref', action='append'); ap.add_argument('--sref', action='append'); ap.add_argument('--line')
    ap.add_argument('--kind', default='section', choices=['section', 'elevation'])
    ap.add_argument('--check', action='append'); ap.add_argument('--era', default='existing')
    ap.add_argument('--level'); ap.add_argument('--note')
    a = ap.parse_args()
    db = _db(a.db)
    if a.cmd == 'list':
        for r in db['records']:
            print(f"{r['id']:16s} {r['kind']:9s} 1:{r['stated_scale']['denominator']:<6g} rms={r['rms_m']} m  {Path(r['file']).name}")
        return
    if a.cmd == 'intersect':
        (x1, y1, x2, y2), (x3, y3, x4, y4) = [[float(v) for v in l.split(',')] for l in a.lines]
        d = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / d
        print(f'{x1 + t * (x2 - x1):.3f},{y1 + t * (y2 - y1):.3f}')
        return
    if a.cmd == 'check':
        rec = next(r for r in db['records'] if r['id'] == a.id)
        res = _checks(np.array(rec['world_to_pt']), [_check(c) for c in a.check])
        rec.setdefault('checks', []).extend(res); _store(db, rec, a.db)
        for c in res:
            print(f"stated {c['stated_m']} m  measured {c['measured_m']} m  err {c['err_m']:+.4f} m ({c['err_pct']:+.3f}%)")
        return
    rec = cmd_plan(a) if a.cmd == 'plan' else cmd_autogrid(a) if a.cmd == 'autogrid' else cmd_section(a)
    _store(db, rec, a.db)
    print(f"{rec['id']}: m_per_pt={rec['m_per_pt']:.6f} rms={rec['rms_m']} m  {rec['method']}")
    if rec.get('scale_note'):
        print('NOTE:', rec['scale_note'])
    for c in rec['checks']:
        print(f"  check: stated {c['stated_m']} m, measured {c['measured_m']} m ({c['err_pct']:+.3f}%)")


if __name__ == '__main__':
    main()
