#!/usr/bin/env python3
"""First step for every drawing: crop each sheet tight to its drawing frame, straighten it, and record its title block.

  sheet_prep.py detect SRC_DIR OUT_DIR [--only ID ...] [--keep-boxes] [--tb-frac 0.35] [--default-ppi 200]
      For every drawing (PDF page, or TIF/PNG/JPG scan) finds the DRAWING FRAME: the ruled border around the drawing
      and its title-block column. Scans are often tilted and sheared (a sheet fed askew through a roller scanner), so
      the tilt of horizontal and vertical lines is measured separately and corrected before the frame is searched.
      Frames that are open at the bottom (border lines running down to a print-size ruler) are handled.
      If no frame is found the paper edge is proposed instead and flagged: set the corners by eye.
      Writes OUT/sheets.json (one entry per sheet, with an empty "read" block to fill in), plus
        OUT/previews/<id>.png     the whole page with the frame (red) and a ruler in PDF points
        OUT/corners/<id>.png      the four frame corners zoomed in, each marked: check the frame sits on the border line
        OUT/titleblocks/<id>.png  the title-block corner of the straightened sheet at native resolution, for reading

  Then LOOK at every preview, corner sheet and title block, and edit OUT/sheets.json:
      - correct frame.corners_pt ([TL, TR, BR, BL] in PDF points, read off the preview ruler / corner tiles) where the
        frame is wrong, then re-render that sheet with `detect --only ID --keep-boxes --snap`
        (--snap pulls each side onto the drawn border line within 12 pt, so corners only need to be roughly right);
      - move title_block_frac ([x0, y0, x1, y1] as fractions of the frame) if the title block is elsewhere;
      - fill "read" from the title block exactly as written: drawing_no, title, revision, date, project, author,
        scale (e.g. "1:100 @ A1", "1:500", "NTS"), paper (e.g. "A1"), status. Write "not shown" if absent;
      - set "reviewed": true.

  sheet_prep.py apply OUT_DIR/sheets.json [--out OUT_DIR/cropped] [--margin-mm 3]
      Writes, per sheet, OUT_DIR/png/<id>.png (straightened, cropped to the frame plus a small margin, at the scan's own
      resolution: the one to look at) and OUT_DIR/cropped/<id>.pdf (that PNG at the same physical size, so PDF points
      keep the scan's scale; vector PDFs keep their vectors and are cropped with a crop box instead). Measures the frame, compares it with the stated paper size,
      and writes m_per_pt_stated (only when the scan is at paper size) plus notes back into sheets.json.
      Exit code 1 if any sheet is unreviewed or has an empty required field in "read".

Every later step (inventory, calibration, rectify, markups) uses the PDFs in OUT_DIR/cropped, never the originals.
"""
import argparse, math, sys
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from bmb_common import ISO_MM, PT_TO_MM, parse_scales, NTS_RE, load_json, save_json, font, affine_from_pairs, apply as aff

IMG_EXT = ('.tif', '.tiff', '.png', '.jpg', '.jpeg')
READ_FIELDS = ['drawing_no', 'title', 'revision', 'date', 'project', 'author', 'scale', 'paper', 'status']
REQUIRED = ['drawing_no', 'title', 'scale', 'paper']

# --------------------------------------------------------------------------- page access


def open_page(path, page, default_ppi):
    """Return (doc, page, note). Image files are wrapped in a one-page PDF at their own resolution."""
    if Path(path).suffix.lower() in IMG_EXT:
        with Image.open(path) as im:
            dpi = float(im.info.get('dpi', (0, 0))[0] or 0)
            w, h = im.size
        note = None
        if not dpi or dpi < 50:
            dpi = default_ppi; note = f'image has no usable dpi tag: assumed {default_ppi} ppi'
        doc = pymupdf.open()
        pg = doc.new_page(width=w / dpi * 72, height=h / dpi * 72)
        pg.insert_image(pg.rect, filename=str(path))
        return doc, pg, note
    doc = pymupdf.open(path)
    return doc, doc[page], None


def native_zoom(pg):
    """px per pt of the largest page-covering image (a scan's own resolution), else 200/72 (render vector at 200 ppi)."""
    best = None
    for im in pg.get_images(full=True):
        for r in pg.get_image_rects(im[0]):
            if best is None or r.get_area() > best[1].get_area():
                best = (im[0], r)
    if best and best[1].get_area() > 0.6 * pg.rect.get_area():
        return pymupdf.Pixmap(pg.parent, best[0]).width / best[1].width
    return 200 / 72


def raster_of(pg, max_px=None):
    """Grey render of the page at native resolution (capped at max_px on the long side) + px per pt."""
    z = native_zoom(pg)
    if max_px:
        z = min(z, max_px / max(pg.rect.width, pg.rect.height))
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(z, z), colorspace=pymupdf.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).copy(), z


def pooled(g, z, target=4000):
    """Shrink by min-pooling (keeps thin, faint lines dark, unlike averaging)."""
    k = max(1, int(np.ceil(max(g.shape) / target)))
    H, W = (g.shape[0] // k) * k, (g.shape[1] // k) * k
    return g[:H, :W].reshape(H // k, k, W // k, k).min(axis=(1, 3)), z / k


def content_of(pg):
    drawings = len(pg.get_drawings())
    cover = 0.0
    for im in pg.get_images(full=True):
        for r in pg.get_image_rects(im[0]):
            cover = max(cover, r.get_area() / max(pg.rect.get_area(), 1))
    if cover >= 0.6 and drawings < 50:
        return 'raster', cover
    return ('vector' if drawings > 50 and cover < 0.6 else 'mixed' if drawings else 'text'), cover

# --------------------------------------------------------------------------- paper edge (scanner background)


def _trim(profile, th):
    ix = np.where(profile > th)[0]
    if not len(ix) or ix[-1] - ix[0] < 0.5 * len(profile):
        return 0, len(profile) - 1
    return int(ix[0]), int(ix[-1])


def paper_box_px(g, th=0.6, inset_px=0):
    """Paper inside a dark scanner background: trim rows / columns that are less than th bright from each side."""
    br = (g > 140).astype(float); k = 5
    rows = np.convolve(br.mean(1), np.ones(k) / k, 'same'); cols = np.convolve(br.mean(0), np.ones(k) / k, 'same')
    y0, y1 = _trim(rows, th); x0, x1 = _trim(cols, th)
    return x0 + inset_px, y0 + inset_px, x1 - inset_px, y1 - inset_px

# --------------------------------------------------------------------------- frame detection


def _ink(g):
    t = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0]
    return (g < min(200, max(100, t))).astype(np.uint8)


def _tilts(ink):
    """The two strongest length-weighted angle modes (deg) of long near-horizontal lines (slope) and near-vertical
    lines (lean). Scans can be sheared, so the two are measured separately."""
    H, W = ink.shape
    segs = cv2.HoughLinesP(ink * 255, 1, np.pi / 3600, threshold=150, minLineLength=int(0.15 * min(H, W)),
                           maxLineGap=int(0.002 * max(H, W)))
    hs, vs = [], []
    for x1, y1, x2, y2 in ([] if segs is None else segs.reshape(-1, 4)):
        dx, dy = float(x2 - x1), float(y2 - y1); L = math.hypot(dx, dy)
        if abs(dy) < 0.05 * abs(dx):
            hs.append((math.degrees(math.atan(dy / dx)), L))
        elif abs(dx) < 0.05 * abs(dy):
            vs.append((math.degrees(math.atan(dx / dy)), L))

    def modes(v):
        tot = {}
        for a, L in v:
            b = round(round(a / 0.05) * 0.05, 2); tot[b] = tot.get(b, 0) + L
        out = []
        for b in sorted(tot, key=lambda b: -tot[b]):
            if all(abs(b - o) > 0.15 for o in out):
                out.append(b)
            if len(out) == 2:
                break
        return out or [0.0]
    return modes(hs), modes(vs)


def _straighten(h_deg, v_deg, W, H):
    """2x3 affine (about the image centre) that makes lines of slope h horizontal and lines of lean v vertical."""
    A = np.array([[1.0, -math.tan(math.radians(v_deg))], [-math.tan(math.radians(h_deg)), 1.0]])
    c = np.array([W / 2, H / 2])
    return np.c_[A, c - A @ c]


def _lines(ink, horiz):
    """Axis-parallel lines: Hough segments clustered by position -> [(position, [[from, to], ...]), ...]."""
    H, W = ink.shape; L = W if horiz else H
    segs = cv2.HoughLinesP(ink * 255, 1, np.pi / 1800, threshold=60, minLineLength=int(0.03 * L),
                           maxLineGap=int(0.004 * L))
    out = []
    for x1, y1, x2, y2 in ([] if segs is None else segs.reshape(-1, 4)):
        dx, dy = abs(x2 - x1), abs(y2 - y1)
        if horiz and dy <= 0.004 * dx + 1:
            out.append(((y1 + y2) / 2, min(x1, x2), max(x1, x2)))
        if not horiz and dx <= 0.004 * dy + 1:
            out.append(((x1 + x2) / 2, min(y1, y2), max(y1, y2)))
    out.sort(); cl = []
    for c, a, b in out:
        if cl and c - cl[-1]['cs'][-1] <= max(3, 0.0015 * L):
            cl[-1]['cs'].append(c); cl[-1]['iv'].append((a, b))
        else:
            cl.append(dict(cs=[c], iv=[(a, b)]))
    res = []
    for k in cl:
        iv = sorted(k['iv']); m = [list(iv[0])]
        for a, b in iv[1:]:
            if a <= m[-1][1]:
                m[-1][1] = max(m[-1][1], b)
            else:
                m.append([a, b])
        if sum(b - a for a, b in m) >= 0.15 * L:
            res.append((float(np.median(k['cs'])), m))
    return res


def _cover(iv, a, b):
    return sum(max(0, min(e, b) - max(s, a)) for s, e in iv) / max(1, b - a)


def _over(iv, a, b):
    return sum(max(0, min(e, a) - s) for s, e in iv) + sum(max(0, e - max(s, b)) for s, e in iv)


def _rect(ink, need=0.45, oh=0.02, reach=0.005):
    """Largest rectangle whose sides are >= need covered by lines that stop at its corners (overhang <= oh).
    A closing bottom line must be reached by both side lines (within reach x size); otherwise the bottom is open,
    where the side lines end (border lines running down to a print-size ruler)."""
    H, W = ink.shape; hs = _lines(ink, True); vs = _lines(ink, False); best = None
    tol = reach * max(H, W)
    for j, (xl, il) in enumerate(vs):
        for xr, ir in vs[j + 1:]:
            if xr - xl < 0.3 * W:
                continue
            end_l, end_r = max(e for s, e in il), max(e for s, e in ir)
            for i, (yt, it) in enumerate(hs):
                if _cover(it, xl, xr) < need or _over(it, xl, xr) / W > oh:
                    continue
                # a closing bottom line counts only if both side lines reach it (else it is e.g. a ruler below)
                opts = [(yb, 'closed') for yb, ib in hs[i + 1:]
                        if yb - yt >= 0.3 * H and _cover(ib, xl, xr) >= need and _over(ib, xl, xr) / W <= oh
                        and min(end_l, end_r) >= yb - tol]
                # open bottom: where the side lines end (the lower one if they end close together, so nothing is cut)
                opts.append((max(end_l, end_r) if abs(end_l - end_r) <= 0.02 * H else min(end_l, end_r), 'open at the bottom'))
                for yb, kind in opts:
                    if yb - yt < 0.3 * H or _cover(il, yt, yb) < need or _cover(ir, yt, yb) < need:
                        continue
                    if _over(il, yt, yb) / H > oh or _over(ir, yt, yb) / H > oh:
                        continue
                    ar = (xr - xl) / (yb - yt)
                    if not (1.25 <= max(ar, 1 / ar) <= 1.6):       # drawing sheets: ISO 1.41, ARCH/ANSI 1.29-1.55
                        continue
                    A = (xr - xl) * (yb - yt)
                    if best is None or A > best[0]:
                        best = (A, (xl, yt, xr, yb), kind)
    return best


def find_frame(pg):
    """-> dict(corners_pt=[TL, TR, BR, BL], method=..., tilt_h_deg, tilt_v_deg) in page points."""
    g, z = pooled(*raster_of(pg))
    H, W = g.shape
    x0, y0, x1, y1 = paper_box_px(g, inset_px=int(8 / PT_TO_MM * z))          # ignore scanner background + edge
    m = np.full_like(g, 255); m[y0:y1, x0:x1] = g[y0:y1, x0:x1]
    hm, vm = _tilts(_ink(m)); best = None
    for h in hm:
        for v in vm:
            M = _straighten(h, v, W, H)
            r = _rect(_ink(cv2.warpAffine(m, M, (W, H), flags=cv2.INTER_NEAREST, borderValue=255)))
            if r and (best is None or r[0] > best[0][0]):
                best = (r, h, v, M)
    if best:
        (_, (a0, b0, a1, b1), kind), h, v, M = best
        Mi = cv2.invertAffineTransform(M)
        pts = [aff(Mi, x, y) for x, y in ((a0, b0), (a1, b0), (a1, b1), (a0, b1))]
        corners = [[float(x) / z, float(y) / z] for x, y in pts]
        corners, snaps = snap_frame(pg, corners, skip=('bottom',) if kind.startswith('open') else ())
        return dict(corners_pt=[[round(x, 1), round(y, 1)] for x, y in corners],
                    method=f'auto: drawing frame ({kind}), straightened h {h:+.2f} deg / v {v:+.2f} deg, sides snapped',
                    snap=snaps, found=True)
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return dict(corners_pt=[[round(x / z, 1), round(y / z, 1)] for x, y in pts], found=False,
                method='NO FRAME FOUND: paper edge proposed. Set the frame corners by eye from the preview.')

def _intersect(p, q, r, s):
    p, q, r, s = map(np.asarray, (p, q, r, s)); d1, d2 = q - p, s - r
    cr = lambda u, v: u[0] * v[1] - u[1] * v[0]
    t = cr(r - p, d2) / cr(d1, d2)
    return p + t * d1


def snap_frame(pg, corners, search_pt=12.0, skip=()):
    """Move each side of the frame onto the border line actually drawn: sample across the side, take the dark run
    nearest the proposed line, fit a straight line through the hits (robust), and intersect the four sides.
    Sides are fitted independently, so per-side tilt and shear are absorbed. Returns (corners, report)."""
    z = min(native_zoom(pg), 2.0)
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(z, z), colorspace=pymupdf.csGRAY, alpha=False)
    g = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).astype(np.float32)
    TL, TR, BR, BL = [np.array(c, float) for c in corners]
    sides = dict(top=(TL, TR), right=(TR, BR), bottom=(BL, BR), left=(TL, BL))
    fitted, report = {}, {}
    offs = np.arange(-search_pt, search_pt + 1e-9, 0.5 / z)
    along = np.linspace(-1.5, 1.5, 7)                     # average a short stretch: bridges speckle and gaps
    for name, (P, Q) in sides.items():
        L = np.linalg.norm(Q - P); d = (Q - P) / L; n = np.array([-d[1], d[0]])
        if name in skip:
            fitted[name] = (P, Q); report[name] = 'not snapped (open side)'; continue
        ts = np.linspace(0.04, 0.96, 80)
        S = P[None, :] + ts[:, None] * (Q - P)[None, :]
        X = S[:, None, None, 0] + offs[None, :, None] * n[0] + along[None, None, :] * d[0]
        Y = S[:, None, None, 1] + offs[None, :, None] * n[1] + along[None, None, :] * d[1]
        prof = cv2.remap(g, (X * z).astype(np.float32).reshape(len(ts), -1), (Y * z).astype(np.float32).reshape(len(ts), -1),
                         cv2.INTER_LINEAR, borderValue=255).reshape(len(ts), len(offs), len(along)).mean(2)
        hits = []
        for t, row in zip(ts, prof):
            dark = row < 150
            if not dark.any():
                continue
            runs, i = [], 0
            while i < len(row):
                if dark[i]:
                    j = i
                    while j + 1 < len(row) and dark[j + 1]:
                        j += 1
                    runs.append(offs[(i + j) // 2] if j - i < 0.75 * len(row) else None); i = j + 1
                else:
                    i += 1
            runs = [r for r in runs if r is not None]
            if runs:
                hits.append((t, min(runs, key=abs)))
        if len(hits) < 0.3 * len(ts):
            fitted[name] = (P, Q); report[name] = f'not snapped ({len(hits)}/{len(ts)} samples found a line)'; continue
        T, O = np.array(hits).T; keep = np.ones(len(T), bool)
        for _ in range(4):
            b, a = np.polyfit(T[keep], O[keep], 1); res = O - (a + b * T)
            keep = np.abs(res) <= max(1.0, 2.5 * np.median(np.abs(res[keep])))
        fitted[name] = (P + a * n, Q + (a + b) * n)
        report[name] = dict(shift_pt=round(float(a + b / 2), 2), twist_pt=round(float(b), 2), hits=int(keep.sum()),
                            rms_pt=round(float(np.sqrt(np.mean(res[keep] ** 2))), 2))
    c = [_intersect(*fitted['top'], *fitted['left']), _intersect(*fitted['top'], *fitted['right']),
         _intersect(*fitted['bottom'], *fitted['right']), _intersect(*fitted['bottom'], *fitted['left'])]
    return [[float(x), float(y)] for x, y in c], report

# --------------------------------------------------------------------------- straighten + crop


def frame_geometry(corners, margin_pt=0.0):
    """Affine page-pt -> output-pt of the straightened frame (+margin), and the output size in pt."""
    TL, TR, BR, BL = [np.array(c, float) for c in corners]
    w = (np.linalg.norm(TR - TL) + np.linalg.norm(BR - BL)) / 2
    h = (np.linalg.norm(BL - TL) + np.linalg.norm(BR - TR)) / 2
    dst = [(margin_pt, margin_pt), (w + margin_pt, margin_pt), (w + margin_pt, h + margin_pt), (margin_pt, h + margin_pt)]
    M, rms = affine_from_pairs([TL, TR, BR, BL], dst)
    return M, w + 2 * margin_pt, h + 2 * margin_pt, rms, (w, h)


def warp_sheet(pg, corners, z, margin_pt=0.0, frac=None, gray=True):
    """Straightened frame as an image at z px/pt. frac=[x0,y0,x1,y1] returns only that part of the frame."""
    M, Wpt, Hpt, _, (w, h) = frame_geometry(corners, margin_pt)
    ox, oy, ow, oh = 0.0, 0.0, Wpt, Hpt
    if frac:
        ox, oy = margin_pt + frac[0] * w, margin_pt + frac[1] * h
        ow, oh = (frac[2] - frac[0]) * w, (frac[3] - frac[1]) * h
    # source region needed, rendered at z
    Mi = cv2.invertAffineTransform(M)
    xs, ys = aff(Mi, [ox, ox + ow, ox + ow, ox], [oy, oy, oy + oh, oy + oh])
    clip = pymupdf.Rect(min(xs) - 2, min(ys) - 2, max(xs) + 2, max(ys) + 2) & pg.rect
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=clip, alpha=False,
                        colorspace=pymupdf.csGRAY if gray else pymupdf.csRGB)
    src = np.frombuffer(pix.samples, np.uint8).reshape((pix.height, pix.width) if pix.n == 1 else (pix.height, pix.width, pix.n))
    # output px -> output pt -> page pt -> src px
    S = np.array([[1 / z, 0, ox], [0, 1 / z, oy], [0, 0, 1]])
    P = np.vstack([Mi, [0, 0, 1]]) @ S
    T = np.array([[z, 0, -clip.x0 * z], [0, z, -clip.y0 * z], [0, 0, 1]]) @ P
    out = cv2.warpAffine(src, T[:2], (int(round(ow * z)), int(round(oh * z))), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    return out

# --------------------------------------------------------------------------- review images


def render_preview(pg, e, out_png, long_px=1600):
    z = long_px / max(pg.rect.width, pg.rect.height)
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
    im = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
    d = ImageDraw.Draw(im); f = font(13)
    step = 100 if max(pg.rect.width, pg.rect.height) < 2500 else 250
    for x in np.arange(0, pg.rect.width, step):
        d.line((x * z, 0, x * z, 8), fill=(255, 0, 180)); d.text((x * z + 2, 9), f'{x:.0f}', fill=(255, 0, 180), font=f)
    for y in np.arange(0, pg.rect.height, step):
        d.line((0, y * z, 8, y * z), fill=(0, 140, 255)); d.text((10, y * z + 2), f'{y:.0f}', fill=(0, 140, 255), font=f)
    c = [(x * z, y * z) for x, y in e['frame']['corners_pt']]
    d.line(c + [c[0]], fill=(230, 0, 0), width=3)
    for (x, y), lab in zip(c, ('TL', 'TR', 'BR', 'BL')):
        d.text((x + 4, y + 4), lab, fill=(230, 0, 0), font=f)
    d.text((20, im.height - 22), f"{e['id']}  red = drawing frame (PDF points). {e['frame']['method']}",
           fill=(220, 0, 0), font=f)
    im.save(out_png)


def render_corners(pg, corners, out_png, half_pt=60, px=420):
    """Four zoomed tiles, one per frame corner, with the corner marked: does it sit on the border line?"""
    tiles = []
    for (x, y), lab in zip(corners, ('TL', 'TR', 'BR', 'BL')):
        r = pymupdf.Rect(x - half_pt, y - half_pt, x + half_pt, y + half_pt)
        z = px / (2 * half_pt)
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=r, alpha=False)
        im = Image.new('RGB', (px, px), 'white')
        rc = r & pg.rect
        im.paste(Image.frombytes('RGB', (pix.width, pix.height), pix.samples), (int((rc.x0 - r.x0) * z), int((rc.y0 - r.y0) * z)))
        d = ImageDraw.Draw(im); c = px / 2
        d.line((c - 30, c, c + 30, c), fill=(230, 0, 0), width=2); d.line((c, c - 30, c, c + 30), fill=(230, 0, 0), width=2)
        d.text((6, 6), f'{lab} ({x:.0f}, {y:.0f}) pt, tile {2 * half_pt:.0f} pt', fill=(230, 0, 0), font=font(14))
        tiles.append(im)
    sheet = Image.new('RGB', (4 * px + 30, px), (120, 120, 120))
    for i, t in enumerate(tiles):
        sheet.paste(t, (i * (px + 10), 0))
    sheet.save(out_png)


def render_titleblock(pg, e, out_png, max_px=4000):
    fr = e['title_block_frac']
    _, _, _, _, (w, h) = frame_geometry(e['frame']['corners_pt'])
    z = min(native_zoom(pg), max_px / max((fr[2] - fr[0]) * w, (fr[3] - fr[1]) * h))
    Image.fromarray(warp_sheet(pg, e['frame']['corners_pt'], z, frac=fr)).save(out_png)

# --------------------------------------------------------------------------- commands


def cmd_detect(a):
    src, out = Path(a.src), Path(a.out)
    for sub in ('previews', 'corners', 'titleblocks'):
        (out / sub).mkdir(parents=True, exist_ok=True)
    db_path = out / 'sheets.json'
    old = {e['id']: e for e in load_json(db_path)['sheets']} if db_path.exists() else {}
    files = sorted(p for p in src.rglob('*') if p.is_file() and p.suffix.lower() in ('.pdf',) + IMG_EXT
                   and not p.name.startswith('.'))
    sheets = dict(old)
    for f in files:
        n_pages = len(pymupdf.open(f)) if f.suffix.lower() == '.pdf' else 1
        for page in range(n_pages):
            sid = f.stem if n_pages == 1 else f'{f.stem}_p{page + 1}'
            if a.only and sid not in a.only:
                continue
            doc, pg, note = open_page(f, page, a.default_ppi)
            content, cover = content_of(pg)
            e = old.get(sid, {}) if a.keep_boxes else {}
            e.update(id=sid, source=str(f.relative_to(src)), page=page, content=content, image_cover=round(cover, 3),
                     source_size_mm=[round(pg.rect.width * PT_TO_MM, 1), round(pg.rect.height * PT_TO_MM, 1)],
                     source_note=note)
            if 'frame' not in e:
                e['frame'] = find_frame(pg)
            elif a.snap:          # corners set by eye: pull each side onto the drawn border line
                c, rep = snap_frame(pg, e['frame']['corners_pt'], skip=('bottom',) if 'open' in e['frame']['method'] else ())
                e['frame'].update(corners_pt=[[round(x, 1), round(y, 1)] for x, y in c], snap=rep)
                e['frame']['method'] = e['frame']['method'].split(' | ')[0] + ' | corners edited by eye, then snapped'
            e.setdefault('title_block_frac', [1 - a.tb_frac, 1 - a.tb_frac, 1.0, 1.0])
            if content != 'raster':
                xs = [c[0] for c in e['frame']['corners_pt']]; ys = [c[1] for c in e['frame']['corners_pt']]
                fr = e['title_block_frac']; x0, y0 = min(xs), min(ys); w, h = max(xs) - x0, max(ys) - y0
                t = pg.get_text(clip=pymupdf.Rect(x0 + fr[0] * w, y0 + fr[1] * h, x0 + fr[2] * w, y0 + fr[3] * h)) or ''
                e['text_candidates'] = dict(scales=[s['text'] for s in parse_scales(t) or parse_scales(pg.get_text())],
                                            nts=bool(NTS_RE.search(t)),
                                            lines=[l.strip() for l in t.splitlines() if len(l.strip()) > 2][:25])
            e['preview'] = f'previews/{sid}.png'; e['corners_png'] = f'corners/{sid}.png'
            e['title_block_png'] = f'titleblocks/{sid}.png'
            e.setdefault('read', {k: (old.get(sid, {}).get('read') or {}).get(k) for k in READ_FIELDS})
            e.setdefault('reviewed', False)
            render_preview(pg, e, out / e['preview'])
            render_corners(pg, e['frame']['corners_pt'], out / e['corners_png'])
            render_titleblock(pg, e, out / e['title_block_png'])
            sheets[sid] = e
            _, _, _, _, (w, h) = frame_geometry(e['frame']['corners_pt'])
            print(f"{content:7s} {sid}: frame {w * PT_TO_MM:.0f} x {h * PT_TO_MM:.0f} mm  {e['frame']['method']}"
                  f"{'  NOTE: ' + note if note else ''}")
    save_json({'schema_version': '2.0', 'source_dir': str(src.resolve()), 'sheets': list(sheets.values())}, db_path)
    print(f'{len(sheets)} sheets -> {db_path}\nNext: look at every preview, corner sheet and title block, '
          'edit sheets.json, then `apply`.')


def scale_check(frame_mm, rd):
    """Is the scan at paper size? The frame must fit inside the stated paper with a normal border (frame long side
    90-100 % of the paper's). Returns (m_per_pt or None, basis, note)."""
    sc = parse_scales(rd.get('scale') or ''); nts = bool(NTS_RE.search(rd.get('scale') or ''))
    paper = (rd.get('paper') or '').strip().upper()
    if not sc:
        if nts or (rd.get('scale') or '').lower() == 'not shown':
            return None, 'no scale stated', None
        return None, None, f'scale "{rd.get("scale")}" not understood: write e.g. "1:100 @ A1" or "NTS"'
    den = sc[0]['denominator']; sp = sc[0]['paper'] or (paper if paper in ISO_MM else None)
    if not sp:
        return PT_TO_MM / 1000 * den, f'1:{den} at page size, UNVERIFIED (no paper size stated)', None
    r = max(frame_mm) / ISO_MM[sp][1]
    if 0.90 <= r <= 1.0:
        return PT_TO_MM / 1000 * den, (f'1:{den} @ {sp}: frame is {100 * r:.1f} % of {sp}, consistent with a scan at paper '
                                       'size. A first estimate only: calibration decides (scans drift by a few %).'), None
    return None, f'none: the scan is not at paper size', (
        f'frame {frame_mm[0]:.0f} x {frame_mm[1]:.0f} mm is {r:.3f} x the {sp} sheet ({ISO_MM[sp][1]} mm), so the scan is '
        f'not at paper size (expected 0.90-1.00). The stated scale cannot be applied to PDF points: calibrate from '
        'references (grid bubbles, level tags, the scale bar).')


def cmd_apply(a):
    db_path = Path(a.sheets); db = load_json(db_path)
    src = Path(db['source_dir']); out = Path(a.out or db_path.parent / 'cropped'); out.mkdir(parents=True, exist_ok=True)
    png_dir = out.parent / 'png'; png_dir.mkdir(exist_ok=True)
    errs = 0
    for e in db['sheets']:
        rd = e.get('read') or {}
        missing = [k for k in REQUIRED if not (rd.get(k) or '').strip()]
        if not e.get('reviewed') or missing:
            print(f"ERROR {e['id']}: {'not reviewed' if not e.get('reviewed') else ''}"
                  f"{'; ' if not e.get('reviewed') and missing else ''}{'empty: ' + ', '.join(missing) if missing else ''}")
            errs += 1; continue
        doc, pg, _ = open_page(src / e['source'], e['page'], a.default_ppi)
        corners = e['frame']['corners_pt']; m = a.margin_mm / PT_TO_MM
        M, Wpt, Hpt, rms, (w, h) = frame_geometry(corners, m)
        pdf_path, png_path = out / f"{e['id']}.pdf", png_dir / f"{e['id']}.png"
        z = native_zoom(pg)
        img = warp_sheet(pg, corners, z, margin_pt=m)
        Image.fromarray(img).save(png_path, dpi=(z * 72, z * 72), optimize=True)
        if e['content'] == 'raster':          # straightened scan, wrapped at the scan's own size
            new = pymupdf.open(); p = new.new_page(width=img.shape[1] / z, height=img.shape[0] / z)
            p.insert_image(p.rect, filename=str(png_path)); how = 'straightened + cropped raster'
        else:                                 # vector: keep vectors, crop box to the frame's bounding box
            new = pymupdf.open(); new.insert_pdf(doc, from_page=pg.number, to_page=pg.number); p = new[0]
            xs = [c[0] for c in corners]; ys = [c[1] for c in corners]
            p.set_cropbox((pymupdf.Rect(min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m) & p.rect) * p.derotation_matrix)
            how = 'vector, crop box (not straightened)'
        new.save(pdf_path, garbage=3, deflate=True)
        frame_mm = [round(w * PT_TO_MM, 1), round(h * PT_TO_MM, 1)]
        mpp, basis, note = scale_check(frame_mm, rd)
        e.update(sheet_pdf=str(pdf_path.relative_to(db_path.parent)), sheet_png=str(png_path.relative_to(db_path.parent)),
                 crop=dict(how=how, margin_mm=a.margin_mm, corner_fit_rms_pt=round(rms, 2), ppi=round(z * 72, 1),
                           size_px=[int(img.shape[1]), int(img.shape[0])]),
                 sheet_size_mm=frame_mm, m_per_pt_stated=mpp, m_per_pt_basis=basis, check_note=note)
        print(f"ok    {e['id']}: {rd.get('drawing_no')} rev {rd.get('revision')} '{rd.get('title')}' scale {rd.get('scale')} "
              f"paper {rd.get('paper')} | frame {frame_mm[0]:.0f} x {frame_mm[1]:.0f} mm, {img.shape[1]} x {img.shape[0]} px"
              f"{'  NOTE: ' + note if note else ''}")
    save_json(db, db_path)
    print(f"{len(db['sheets']) - errs} sheets -> {out}  errors={errs}")
    sys.exit(1 if errs else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    d = sp.add_parser('detect'); d.add_argument('src'); d.add_argument('out')
    d.add_argument('--tb-frac', type=float, default=0.35, help='default title-block box: this fraction of the frame, bottom-right')
    d.add_argument('--only', nargs='*'); d.add_argument('--keep-boxes', action='store_true')
    d.add_argument('--snap', action='store_true', help='with --keep-boxes: snap edited frame corners onto the border lines')
    d.add_argument('--default-ppi', type=float, default=200)
    p = sp.add_parser('apply'); p.add_argument('sheets'); p.add_argument('--out')
    p.add_argument('--margin-mm', type=float, default=3.0); p.add_argument('--default-ppi', type=float, default=200)
    a = ap.parse_args()
    cmd_detect(a) if a.cmd == 'detect' else cmd_apply(a)


if __name__ == '__main__':
    main()
