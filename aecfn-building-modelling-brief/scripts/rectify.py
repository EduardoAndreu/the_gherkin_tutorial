#!/usr/bin/env python3
"""Resample calibrated sheets into the world frame -> true-scale underlays for Blender and markups.

Plans:    image +x = world +X, image up = world +Y.
Sections: image +x = s, image up = z.
Each PNG gets a sidecar JSON {metres_per_px, size_px, world_origin_px, world_extent_m, kind, level, section_line, ...}.
A Blender plane W*mpp x H*mpp metres, shifted so pixel world_origin_px sits on the world origin, is at true scale.

usage:
  rectify.py --db calibration.json --id L02 [--id L03 ...] --out underlays/ [--ppm 50]
             [--extent a0 b0 a1 b1]  (world window to keep; default = whole sheet)
             [--name PLN_L02_existing]  (only with a single --id)
"""
import argparse, sys
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from bmb_common import load_calibration, world_to_pt, invert, apply, save_json


def _find(f, db_dir=None):
    """Calibration records may hold relative paths: try as given, then relative to the db folder and its parents."""
    p = Path(f)
    if p.exists() or p.is_absolute():
        return str(p)
    for base in [Path(db_dir)] + list(Path(db_dir).parents) if db_dir else []:
        if (base / p).exists():
            return str(base / p)
    return str(p)


def rectify(rec, out_dir, ppm=50.0, extent=None, name=None, max_px=12000, db_dir=None):
    M = world_to_pt(rec)
    page = pymupdf.open(_find(rec['file'], db_dir))[rec.get('page', 0)]
    if extent is None:
        Minv = invert(M)
        r = page.rect
        a, b = apply(Minv, [r.x0, r.x1, r.x1, r.x0], [r.y0, r.y0, r.y1, r.y1])
        extent = [float(np.min(a)), float(np.min(b)), float(np.max(a)), float(np.max(b))]
    a0, b0, a1, b1 = extent
    ow, oh = int(np.ceil((a1 - a0) * ppm)), int(np.ceil((b1 - b0) * ppm))
    if max(ow, oh) > max_px:
        f = max_px / max(ow, oh); ppm *= f; ow, oh = int(ow * f), int(oh * f)
        print(f"  {rec['id']}: capped to {max_px}px -> {ppm:.2f} px/m", file=sys.stderr)
    # render only the page region needed, at >= target resolution
    ca, cb = apply(M, [a0, a1, a1, a0], [b0, b0, b1, b1])
    clip = pymupdf.Rect(min(ca), min(cb), max(ca), max(cb)) & page.rect
    zoom = max(1.0, 1.25 * ppm * rec['m_per_pt'])
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False, colorspace=pymupdf.csRGB)
    src = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    gx, gy = np.meshgrid(np.arange(ow), np.arange(oh))
    A = a0 + (gx + 0.5) / ppm
    B = b1 - (gy + 0.5) / ppm
    px, py = apply(M, A, B)
    mx = ((px - clip.x0) * zoom).astype(np.float32); my = ((py - clip.y0) * zoom).astype(np.float32)
    res = cv2.remap(src, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    name = name or f"{'PLN' if rec['kind'] == 'plan' else 'SEC' if rec['kind'] == 'section' else 'ELV'}_{rec['id']}"
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    Image.fromarray(res).save(out_dir / f'{name}.png', optimize=True)
    meta = dict(file=f'{name}.png', calibration_id=rec['id'], source_drawing=Path(rec['file']).name, kind=rec['kind'],
                level=rec.get('level'), era=rec.get('era'),
                axes=('image +x = world +X (m), image up = world +Y (m)' if rec['kind'] == 'plan'
                      else 'image +x = s along the cut/view line (m), image up = z (m)'),
                metres_per_px=round(1 / ppm, 6), size_px=[ow, oh],
                world_extent_m=dict(min_a=round(a0, 3), min_b=round(b0, 3), max_a=round(a1, 3), max_b=round(b1, 3)),
                world_origin_px=[round(-a0 * ppm - 0.5, 2), round(b1 * ppm - 0.5, 2)],
                section_line=rec.get('section_line'), calibration_rms_m=rec.get('rms_m'))
    save_json(meta, out_dir / f'{name}.json')
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default='calibration.json'); ap.add_argument('--id', action='append', required=True)
    ap.add_argument('--out', required=True); ap.add_argument('--ppm', type=float, default=50.0)
    ap.add_argument('--extent', type=float, nargs=4); ap.add_argument('--name')
    a = ap.parse_args()
    recs = load_calibration(a.db)
    for i in a.id:
        m = rectify(recs[i], a.out, a.ppm, a.extent, a.name if len(a.id) == 1 else None, db_dir=str(Path(a.db).resolve().parent))
        print(f"{m['file']}: {m['size_px']} px at {1 / m['metres_per_px']:.1f} px/m, origin px {m['world_origin_px']}")


if __name__ == '__main__':
    main()
