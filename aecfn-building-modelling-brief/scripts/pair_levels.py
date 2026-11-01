#!/usr/bin/env python3
"""Pair a level table (name -> z) with level lines detected on a section/elevation, for calibrate.py section --zref.

  pair_levels.py --lines lines.json --levels levels.json --seed L51=323.21 --seed L06=2460.48 [--tol 4] [--z-offset -15.025]
      lines.json : output of raster_tools.py lines --orient h (list of {y, ...}) or pdf_vectors text positions ({cy})
      levels.json: {"L06": 46.952, ...} in the datum the drawing uses (e.g. AOD)
      --seed     : two levels you have identified by eye (name=py); everything else is assigned by the fitted line
      --z-offset : added to z on output (e.g. -15.025 to go from AOD to the project datum)
Prints the pairs, the fit (m per pt, rms) and the unpaired levels, then a ready-made list of --zref arguments.
Unpaired levels and stray lines are normal (level tags with no line, dimension lines); check any level you rely on.
"""
import argparse, json
import numpy as np


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lines', required=True); ap.add_argument('--levels', required=True)
    ap.add_argument('--seed', action='append', required=True); ap.add_argument('--tol', type=float, default=4.0)
    ap.add_argument('--z-offset', type=float, default=0.0); ap.add_argument('--out')
    a = ap.parse_args()
    L = json.load(open(a.lines)); ys = np.array([d.get('y', d.get('cy')) for d in L], float)
    lev = json.load(open(a.levels))
    (n1, p1), (n2, p2) = [(s.split('=')[0], float(s.split('=')[1])) for s in a.seed[:2]]
    k = (p2 - p1) / (lev[n2] - lev[n1]); c = p1 - k * lev[n1]
    pairs = []
    for it in range(2):  # assign, refit, assign again
        pairs = []
        for name, z in lev.items():
            py = k * z + c; j = int(np.argmin(abs(ys - py)))
            if abs(ys[j] - py) < a.tol:
                pairs.append((name, z, float(ys[j])))
        P = np.array([[z, y] for _, z, y in pairs]); k, c = np.polyfit(P[:, 0], P[:, 1], 1)
    res = P[:, 1] - np.polyval([k, c], P[:, 0])
    print(f'{len(pairs)}/{len(lev)} levels paired; m per pt (vertical) = {abs(1 / k):.6f}; rms {res.std():.2f} pt '
          f'({res.std() * abs(1 / k):.3f} m); max {abs(res).max():.2f} pt')
    print('unpaired:', [n for n in lev if n not in {p[0] for p in pairs}])
    out = [dict(name=n, z=round(z + a.z_offset, 4), py=y) for n, z, y in pairs]
    if a.out:
        json.dump(out, open(a.out, 'w'), indent=1)
    print(' '.join(f'--zref "{o["py"]}={o["z"]}"' for o in out))


if __name__ == '__main__':
    main()
