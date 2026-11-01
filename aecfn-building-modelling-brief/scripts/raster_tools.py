#!/usr/bin/env python3
"""Tools for raster sheets (scans / image-only PDFs): find references by image analysis, in PDF points.

Planning-portal and archive downloads are often image-only PDFs, even when the drawing is modern: no text layer,
no vectors. All coordinates here are PDF page points (same as pdf_vectors.py / calibrate.py), so calibration
records work identically for raster and vector sheets.

Subcommands
  info    FILE                         image size, ppi, px-per-pt, whether the page is image-only
  crop    FILE --box x0 y0 x1 y1 [--grid 50] --out crop.png
                                       native-resolution crop with a labelled point grid: read coordinates off it
  bubbles FILE [--box ...] [--dmin-mm 6 --dmax-mm 16] [--scale-mm-per-pt]  --out bubbles.png
                                       detect grid-bubble circles; prints centres (pt) and writes a numbered strip so
                                       you can read each bubble's label visually and pair it with its grid line
  lines   FILE [--box ...] --orient h|v [--min-len-pt 200] [--max-thick-pt 3]
                                       long straight lines (grid lines, level lines, slab edges): position in pt,
                                       extent and thickness; dash-dot grid lines are bridged
  profile FILE --a x,y --b x,y [--halfwidth-pt 2]
                                       dark runs along a segment (tick marks of a scale bar, lines it crosses)

Typical raster calibration:
  bubbles in the margin bands -> read labels from the strip -> grid x/y in pt -> calibrate.py plan --ref ... (3+ refs, --fit affine)
"""
import argparse, json, sys
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from bmb_common import font, PT_TO_MM


class Sheet:
    """The page's dominant image as a grayscale array + pt<->px mapping."""

    def __init__(self, path, page=0, min_zoom=None):
        doc = pymupdf.open(path); pg = doc[page]
        self.page_rect = pg.rect
        imgs = pg.get_images(full=True)
        self.image_only = bool(imgs) and len(pg.get_drawings()) == 0 and not pg.get_text().strip()
        best = None
        for im in imgs:
            for r in pg.get_image_rects(im[0]):
                if best is None or r.get_area() > best[1].get_area():
                    best = (im[0], r)
        if best and best[1].get_area() > 0.6 * pg.rect.get_area():
            pix = pymupdf.Pixmap(doc, best[0])
            if pix.n - pix.alpha != 1:
                pix = pymupdf.Pixmap(pymupdf.csGRAY, pix)
            self.img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[..., 0].copy()
            self.rect = best[1]
        else:  # vector or mixed page: render it
            z = min_zoom or 4.0
            pix = pg.get_pixmap(matrix=pymupdf.Matrix(z, z), colorspace=pymupdf.csGRAY, alpha=False)
            self.img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).copy(); self.rect = pg.rect
        if self.img.mean() < 100:  # negative scan
            self.img = 255 - self.img
        H, W = self.img.shape
        self.sx = W / self.rect.width; self.sy = H / self.rect.height
        # ink threshold adapts to the scan: grey linework on colour scans is often far above 128
        otsu, _ = cv2.threshold(self.img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        self.thr = float(min(200, max(100, otsu)))

    def pt2px(self, x, y):
        return (np.asarray(x) - self.rect.x0) * self.sx, (np.asarray(y) - self.rect.y0) * self.sy

    def px2pt(self, u, v):
        return np.asarray(u) / self.sx + self.rect.x0, np.asarray(v) / self.sy + self.rect.y0

    def box_px(self, box):
        if not box:  # the visible page: a sheet cropped by sheet_prep.py keeps the scan beyond its crop box
            box = (self.page_rect.x0, self.page_rect.y0, self.page_rect.x1, self.page_rect.y1)
        (u0, v0), (u1, v1) = self.pt2px(box[0], box[1]), self.pt2px(box[2], box[3])
        return int(max(0, u0)), int(max(0, v0)), int(min(self.img.shape[1], u1)), int(min(self.img.shape[0], v1))


def cmd_info(s, a):
    H, W = s.img.shape
    ppi = s.sx * 72
    print(json.dumps(dict(image_px=[W, H], page_pt=[s.page_rect.width, s.page_rect.height],
                          page_mm=[round(s.page_rect.width * PT_TO_MM, 1), round(s.page_rect.height * PT_TO_MM, 1)],
                          px_per_pt=[round(s.sx, 4), round(s.sy, 4)], ppi=round(ppi, 1), image_only=s.image_only, ink_threshold=round(s.thr),
                          note=('image-only page: the page size is image size / ppi, i.e. the SCAN, not necessarily the paper. '
                                'Calibrate from in-drawing references, never from the stated scale alone.') if s.image_only else None), indent=1))


def cmd_crop(s, a):
    u0, v0, u1, v1 = s.box_px(a.box)
    im = Image.fromarray(s.img[v0:v1, u0:u1]).convert('RGB')
    if a.zoom != 1:
        im = im.resize((int(im.width * a.zoom), int(im.height * a.zoom)), Image.LANCZOS)
    d = ImageDraw.Draw(im); f = font(max(12, im.width // 90))
    x0, y0 = s.px2pt(u0, v0); x1, y1 = s.px2pt(u1, v1)
    g = a.grid
    for x in np.arange(np.ceil(x0 / g) * g, x1, g):
        u = (s.pt2px(x, 0)[0] - u0) * a.zoom
        d.line((u, 0, u, im.height), fill=(255, 0, 180), width=1); d.text((u + 2, 2), f'{x:.0f}', fill=(255, 0, 180), font=f)
    for y in np.arange(np.ceil(y0 / g) * g, y1, g):
        v = (s.pt2px(0, y)[1] - v0) * a.zoom
        d.line((0, v, im.width, v), fill=(0, 140, 255), width=1); d.text((2, v + 2), f'{y:.0f}', fill=(0, 140, 255), font=f)
    im.save(a.out); print(f'{a.out}: pt box {x0:.1f},{y0:.1f} - {x1:.1f},{y1:.1f}, grid every {g} pt (magenta x, blue y)')


def cmd_bubbles(s, a):
    u0, v0, u1, v1 = s.box_px(a.box)
    sub = s.img[v0:v1, u0:u1]
    mm_px = s.sx / PT_TO_MM  # px per mm on the page
    rmin, rmax = int(a.dmin_mm / 2 * mm_px), int(a.dmax_mm / 2 * mm_px)
    blur = cv2.GaussianBlur(sub, (5, 5), 1.5)
    c = cv2.HoughCircles(blur, cv2.HOUGH_GRADIENT, dp=1.2, minDist=rmin * 2.2, param1=120, param2=a.sens,
                         minRadius=rmin, maxRadius=rmax)
    out = []
    H, W = sub.shape
    ang = np.linspace(0, 2 * np.pi, 96, endpoint=False)

    def frac_dark(u, v, rr, w=2):
        best = np.zeros(len(ang), bool)
        for dr in range(-w, w + 1):
            U = np.clip((u + (rr + dr) * np.cos(ang)).astype(int), 0, W - 1)
            V = np.clip((v + (rr + dr) * np.sin(ang)).astype(int), 0, H - 1)
            best |= sub[V, U] < s.thr
        return best.mean()

    if c is not None:
        for (u, v, r) in c[0]:
            # a grid bubble is a complete dark ring, standing clear of other linework (only its leader crosses the halo)
            ring = frac_dark(u, v, r)
            halo = frac_dark(u, v, r * 1.35, w=1)
            if ring >= a.ring_min and halo <= a.halo_max:
                x, y = s.px2pt(u + u0, v + v0)
                out.append(dict(x=round(float(x), 2), y=round(float(y), 2), r_pt=round(float(r / s.sx), 2),
                                ring=round(float(ring), 2), halo=round(float(halo), 2)))
    if out and not a.box:  # default search: keep bubbles in the outer margin bands of the sheet
        xs = np.array([b['x'] for b in out]); ys = np.array([b['y'] for b in out])
        R = s.rect; mx, my = R.width * a.margin, R.height * a.margin
        keep = (xs < R.x0 + mx) | (xs > R.x1 - mx) | (ys < R.y0 + my) | (ys > R.y1 - my)
        if a.margin < 0.5:
            out = [b for b, k in zip(out, keep) if k]
    out.sort(key=lambda b: (round(b['y'] / 30), b['x']))
    for i, b in enumerate(out):
        b['i'] = i
    cell = int(rmax * 2.6); cols = 10; rows = max(1, (len(out) + cols - 1) // cols)
    strip = Image.new('RGB', (cols * cell, rows * (cell + 22)), 'white'); d = ImageDraw.Draw(strip); f = font(16)
    for b in out:
        u, v = s.pt2px(b['x'], b['y']); u, v = int(u), int(v)
        crop = s.img[max(0, v - cell // 2):v + cell // 2, max(0, u - cell // 2):u + cell // 2]
        X, Y = (b['i'] % cols) * cell, (b['i'] // cols) * (cell + 22)
        strip.paste(Image.fromarray(crop).convert('RGB'), (X, Y + 22)); d.text((X + 4, Y + 2), f"#{b['i']}", fill=(220, 0, 0), font=f)
    strip.save(a.out)
    print(json.dumps(out, indent=1)); print(f'{len(out)} bubbles; read their labels in {a.out}', file=sys.stderr)


def cmd_lines(s, a):
    u0, v0, u1, v1 = s.box_px(a.box)
    sub = (s.img[v0:v1, u0:u1] < s.thr).astype(np.uint8) * 255
    L = int(a.min_len_pt * (s.sx if a.orient == 'h' else s.sy))
    bridge = int(a.bridge_pt * s.sx)
    if a.orient == 'h':
        sub = cv2.morphologyEx(sub, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (bridge, 1)))
        m = cv2.morphologyEx(sub, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (L, 1)))
        prof = (m > 0).sum(1)
    else:
        sub = cv2.morphologyEx(sub, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, bridge)))
        m = cv2.morphologyEx(sub, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, L)))
        prof = (m > 0).sum(0)
    on = prof > 0; res = []; i = 0
    while i < len(on):
        if on[i]:
            j = i
            while j + 1 < len(on) and on[j + 1]:
                j += 1
            w = prof[i:j + 1].astype(float); c = (np.arange(i, j + 1) * w).sum() / w.sum()
            band = m[i:j + 1] if a.orient == 'h' else m[:, i:j + 1]
            idx = np.where(band.max(0 if a.orient == 'h' else 1) > 0)[0]
            if a.orient == 'h':
                y = s.px2pt(0, c + v0)[1]; xa, xb = s.px2pt(idx.min() + u0, 0)[0], s.px2pt(idx.max() + u0, 0)[0]
                rec = dict(y=round(float(y), 2), x_from=round(float(xa), 1), x_to=round(float(xb), 1))
            else:
                x = s.px2pt(c + u0, 0)[0]; ya, yb = s.px2pt(0, idx.min() + v0)[1], s.px2pt(0, idx.max() + v0)[1]
                rec = dict(x=round(float(x), 2), y_from=round(float(ya), 1), y_to=round(float(yb), 1))
            rec['thick_pt'] = round((j - i + 1) / (s.sy if a.orient == 'h' else s.sx), 2)
            if rec['thick_pt'] <= a.max_thick_pt:
                res.append(rec)
            i = j + 1
        else:
            i += 1
    print(json.dumps(res, indent=1)); print(f'{len(res)} {a.orient}-lines >= {a.min_len_pt} pt', file=sys.stderr)


def cmd_profile(s, a):
    (xa, ya), (xb, yb) = [[float(v) for v in p.split(',')] for p in (a.a, a.b)]
    ua, va = s.pt2px(xa, ya); ub, vb = s.pt2px(xb, yb)
    n = int(np.hypot(ub - ua, vb - va)); t = np.linspace(0, 1, n)
    nx, ny = -(vb - va) / n, (ub - ua) / n; hw = int(a.halfwidth_pt * s.sx)
    vals = np.zeros(n)
    for k in range(-hw, hw + 1):
        U = np.clip((ua + (ub - ua) * t + k * nx).astype(int), 0, s.img.shape[1] - 1)
        V = np.clip((va + (vb - va) * t + k * ny).astype(int), 0, s.img.shape[0] - 1)
        vals += s.img[V, U] < s.thr
    dark = vals > max(1, hw); runs = []; i = 0
    while i < n:
        if dark[i]:
            j = i
            while j + 1 < n and dark[j + 1]:
                j += 1
            tc = (i + j) / 2 / n
            runs.append(dict(x=round(xa + (xb - xa) * tc, 2), y=round(ya + (yb - ya) * tc, 2), along_pt=round(tc * np.hypot(xb - xa, yb - ya), 2),
                             width_pt=round((j - i + 1) / s.sx, 2)))
            i = j + 1
        else:
            i += 1
    print(json.dumps(runs, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['info', 'crop', 'bubbles', 'lines', 'profile']); ap.add_argument('file')
    ap.add_argument('--page', type=int, default=0); ap.add_argument('--box', type=float, nargs=4)
    ap.add_argument('--out', default='out.png'); ap.add_argument('--grid', type=float, default=50); ap.add_argument('--zoom', type=float, default=1.0)
    ap.add_argument('--dmin-mm', type=float, default=7); ap.add_argument('--dmax-mm', type=float, default=22); ap.add_argument('--sens', type=float, default=30)
    ap.add_argument('--ring-min', type=float, default=0.8); ap.add_argument('--halo-max', type=float, default=0.25); ap.add_argument('--margin', type=float, default=0.2)
    ap.add_argument('--orient', choices=['h', 'v'], default='h'); ap.add_argument('--min-len-pt', type=float, default=200)
    ap.add_argument('--max-thick-pt', type=float, default=3); ap.add_argument('--bridge-pt', type=float, default=6)
    ap.add_argument('--a'); ap.add_argument('--b'); ap.add_argument('--halfwidth-pt', type=float, default=1.5)
    a = ap.parse_args()
    s = Sheet(a.file, a.page)
    dict(info=cmd_info, crop=cmd_crop, bubbles=cmd_bubbles, lines=cmd_lines, profile=cmd_profile)[a.cmd](s, a)


if __name__ == '__main__':
    main()
