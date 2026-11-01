#!/usr/bin/env python3
"""Look at / measure on a rectified underlay in world metres.

  view_underlay.py UNDERLAY.json --window a0 b0 a1 b1 [--grid 1.0] [--zoom 2] [--mark X,Y ...] --out view.png
      crop of the world window with a labelled metre grid (major every 5 grid steps), plus optional marked points.
      Read positions straight off it in the spec's frame (plans X/Y, sections s/z).
  view_underlay.py UNDERLAY.json --profile=a0,b0:a1,b1 [--halfwidth-m 0.05]   (use "=" when a0 is negative)
      dark runs (lines crossed) along a world-space segment -> their world positions. Use it to put numbers on what
      you see: e.g. the slab edge crossing a line at X = 24 m, every level line on a section at s = 10 m.
"""
import argparse, json, sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from bmb_common import load_json, font


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('underlay'); ap.add_argument('--window', type=float, nargs=4); ap.add_argument('--grid', type=float, default=1.0)
    ap.add_argument('--zoom', type=float, default=1.0); ap.add_argument('--mark', action='append', default=[]); ap.add_argument('--out', default='view.png')
    ap.add_argument('--profile'); ap.add_argument('--halfwidth-m', type=float, default=0.05); ap.add_argument('--thr', type=float)
    a = ap.parse_args()
    m = load_json(a.underlay); img = Image.open(Path(a.underlay).with_name(m['file'])).convert('L')
    ppm = 1 / m['metres_per_px']; ox, oy = m['world_origin_px']
    w2p = lambda A, B: (ox + A * ppm, oy - B * ppm)
    arr = np.asarray(img)
    thr = a.thr or min(200, max(100, float(np.percentile(arr, 50)) - 60))
    if a.profile:
        (A0, B0), (A1, B1) = [[float(v) for v in p.split(',')] for p in a.profile.split(':')]
        L = float(np.hypot(A1 - A0, B1 - B0)); n = max(2, int(L * ppm * 2)); t = np.linspace(0, 1, n)
        nx, ny = -(B1 - B0) / L, (A1 - A0) / L; hw = max(1, int(a.halfwidth_m * ppm))
        dark = np.zeros(n)
        for k in np.linspace(-a.halfwidth_m, a.halfwidth_m, 2 * hw + 1):
            u, v = w2p(A0 + (A1 - A0) * t + k * nx, B0 + (B1 - B0) * t + k * ny)
            u = np.clip(u.astype(int), 0, arr.shape[1] - 1); v = np.clip(v.astype(int), 0, arr.shape[0] - 1)
            dark += arr[v, u] < thr
        on = dark > hw; runs = []; i = 0
        while i < n:
            if on[i]:
                j = i
                while j + 1 < n and on[j + 1]:
                    j += 1
                tc = (i + j) / 2 / (n - 1)
                runs.append(dict(a=round(A0 + (A1 - A0) * tc, 3), b=round(B0 + (B1 - B0) * tc, 3),
                                 along_m=round(tc * L, 3), width_m=round((j - i + 1) / (n - 1) * L, 3)))
                i = j + 1
            else:
                i += 1
        print(json.dumps(runs, indent=1)); return
    a0, b0, a1, b1 = a.window
    x0, y0 = w2p(a0, b1); x1, y1 = w2p(a1, b0)
    im = img.crop((int(x0), int(y0), int(x1), int(y1))).convert('RGB')
    if a.zoom != 1:
        im = im.resize((int(im.width * a.zoom), int(im.height * a.zoom)), Image.LANCZOS)
    d = ImageDraw.Draw(im); f = font(max(11, int(12 * a.zoom ** 0.5)))
    z = a.zoom; g = a.grid
    for k, A in enumerate(np.arange(np.ceil(a0 / g) * g, a1 + 1e-9, g)):
        u = (w2p(A, 0)[0] - int(x0)) * z; major = abs(A / (5 * g) - round(A / (5 * g))) < 1e-6
        d.line((u, 0, u, im.height), fill=(255, 0, 170) if major else (255, 170, 220), width=1)
        if major or g >= 1:
            d.text((u + 2, 2), f'{A:g}', fill=(200, 0, 140), font=f)
    for B in np.arange(np.ceil(b0 / g) * g, b1 + 1e-9, g):
        v = (w2p(0, B)[1] - int(y0)) * z; major = abs(B / (5 * g) - round(B / (5 * g))) < 1e-6
        d.line((0, v, im.width, v), fill=(0, 120, 255) if major else (170, 210, 255), width=1)
        if major or g >= 1:
            d.text((2, v + 2), f'{B:g}', fill=(0, 90, 220), font=f)
    for mk in a.mark:
        A, B = [float(v) for v in mk.split(',')[:2]]; u, v = (w2p(A, B)[0] - int(x0)) * z, (w2p(A, B)[1] - int(y0)) * z
        d.ellipse((u - 6, v - 6, u + 6, v + 6), outline=(255, 0, 0), width=2)
    im.save(a.out); print(f'{a.out}: world window {a0},{b0} .. {a1},{b1}, grid {g} m')


if __name__ == '__main__':
    main()
