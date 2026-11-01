#!/usr/bin/env python3
"""Inventory a folder of blueprint PDFs (and images) -> drawing index.

For every PDF page: page size in mm and ISO match, vector vs raster content, CAD layers (OCGs),
text length, scale notations found ('1:100', '1:100 @ A1', NTS), likely drawing number and title
(from the text in the title-block corner), and a thumbnail. Writes:
  OUT/inventory.json      machine-readable, one record per file (pages nested)
  OUT/drawing_index.xlsx  one row per file for the human to review/correct (if openpyxl is installed)
  OUT/thumbs/*.png        ~1200 px thumbnails (first page)
  OUT/contact_*.png       labelled contact sheets of all thumbnails

The title-block guesses are heuristics: always look at the thumbnails and correct the index.

usage: inventory.py SRC_DIR OUT_DIR [--tb-corner br|bl|tr|tl] [--tb-frac 0.35]
"""
import argparse, hashlib, re, sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from bmb_common import iso_size, parse_scales, NTS_RE, PT_TO_MM, save_json, font

DOCNO_RE = re.compile(r'\b([A-Z]{1,4}[-_ ]?\d{2,5}(?:[-_.]\d{1,4}){0,3}[A-Z]?)\b')
DOC_WORDS = re.compile(r'\b(application|statement|report|letter|notice|schedule|appendix|contents|introduction)\b', re.I)


def corner_rect(page, corner, frac):
    r = page.rect
    w, h = r.width * frac, r.height * frac
    x0 = r.x1 - w if corner[1] == 'r' else r.x0
    y0 = r.y1 - h if corner[0] == 'b' else r.y0
    return pymupdf.Rect(x0, y0, x0 + w, y0 + h)


def page_record(doc, page, corner, frac):
    w_mm, h_mm = page.rect.width * PT_TO_MM, page.rect.height * PT_TO_MM
    iso, orient = iso_size(w_mm, h_mm)
    text = page.get_text()
    tb_text = page.get_text(clip=corner_rect(page, corner, frac))
    drawings = page.get_drawings()
    imgs = page.get_images(full=True)
    img_cover = 0.0
    for im in imgs:
        for rect in page.get_image_rects(im[0]):
            img_cover = max(img_cover, rect.get_area() / max(page.rect.get_area(), 1))
    layers = sorted({d.get('layer') for d in drawings if d.get('layer')})
    content = ('vector' if len(drawings) > 50 and img_cover < 0.6 else
               'raster' if img_cover >= 0.6 and len(drawings) < 50 else
               'mixed' if drawings and imgs else 'text' if text.strip() else 'empty')
    scales = parse_scales(tb_text) or parse_scales(text)
    docnos = [m.group(1) for m in DOCNO_RE.finditer(tb_text)]
    title_lines = [l.strip() for l in tb_text.splitlines() if len(l.strip()) > 8 and not DOCNO_RE.fullmatch(l.strip())]
    return dict(size_mm=[round(w_mm, 1), round(h_mm, 1)], iso=iso, orientation=orient, content=content,
                n_vector_items=len(drawings), n_images=len(imgs), image_cover=round(img_cover, 3),
                layers=layers, text_chars=len(text), scales=scales, nts=bool(NTS_RE.search(tb_text or text)),
                drawing_no_candidates=docnos[:5], title_candidates=title_lines[:6])


def classify(rec):
    p = rec['pages'][0]
    if rec['n_pages'] > 3 and p['iso'] in ('A4', 'A3') and p['text_chars'] > 1500:
        return 'document'
    if DOC_WORDS.search(rec['file']) and p['iso'] in ('A4', None):
        return 'document'
    if p['content'] == 'raster':
        return 'raster drawing or photo'
    return 'drawing'


def thumb(page, out_png, long_px=1200):
    z = long_px / max(page.rect.width, page.rect.height)
    page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False).save(out_png)


def contact(thumbs, out_dir, per=20, cell=420):
    f = font(14)
    for k in range(0, len(thumbs), per):
        chunk = thumbs[k:k + per]; cols = 5; rows = (len(chunk) + cols - 1) // cols
        sheet = Image.new('RGB', (cols * cell, rows * (cell + 24)), 'white'); d = ImageDraw.Draw(sheet)
        for i, (name, p) in enumerate(chunk):
            im = Image.open(p).convert('RGB'); im.thumbnail((cell - 10, cell - 10))
            x, y = (i % cols) * cell, (i // cols) * (cell + 24)
            sheet.paste(im, (x + 5, y + 5)); d.text((x + 5, y + cell), name[:52], fill='black', font=f)
        sheet.save(out_dir / f'contact_{k // per:02d}.png')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src'); ap.add_argument('out')
    ap.add_argument('--tb-corner', default='br', choices=['br', 'bl', 'tr', 'tl'])
    ap.add_argument('--tb-frac', type=float, default=0.35)
    a = ap.parse_args()
    src, out = Path(a.src), Path(a.out); (out / 'thumbs').mkdir(parents=True, exist_ok=True)
    recs, thumbs = [], []
    files = sorted(p for p in src.rglob('*') if p.is_file() and not p.name.startswith('.'))
    for f in files:
        ext = f.suffix.lower()
        rec = dict(file=str(f.relative_to(src)), bytes=f.stat().st_size,
                   sha1=hashlib.sha1(f.read_bytes()).hexdigest()[:12])
        try:
            if ext == '.pdf':
                doc = pymupdf.open(f)
                rec['n_pages'] = len(doc)
                rec['producer'] = (doc.metadata or {}).get('producer')
                rec['ocg_layers'] = sorted({v['name'] for v in (doc.get_ocgs() or {}).values()})
                rec['pages'] = [page_record(doc, doc[i], a.tb_corner, a.tb_frac) for i in range(min(len(doc), 50))]
                rec['class'] = classify(rec)
                tp = out / 'thumbs' / (f.stem[:80] + '.png'); thumb(doc[0], tp); thumbs.append((f.name, tp))
            elif ext in ('.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff'):
                im = Image.open(f); rec.update(n_pages=1, class_='image', size_px=list(im.size))
                rec['class'] = 'image'; rec.pop('class_', None)
                tp = out / 'thumbs' / (f.stem[:80] + '.png'); im.convert('RGB').resize(
                    (max(1, im.width * 1200 // max(im.size)), max(1, im.height * 1200 // max(im.size)))).save(tp)
                thumbs.append((f.name, tp))
            else:
                rec['class'] = 'other'
        except Exception as e:
            rec['error'] = repr(e)
        recs.append(rec)
        print(f"{rec.get('class','?'):24s} {rec['file']}")
    dup = {}
    for r in recs:
        dup.setdefault(r['sha1'], []).append(r['file'])
    for r in recs:
        others = [x for x in dup[r['sha1']] if x != r['file']]
        if others:
            r['identical_to'] = others
    save_json({'source_dir': str(src), 'files': recs}, out / 'inventory.json')
    contact(thumbs, out)
    try:
        import openpyxl
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'Drawing index'
        hdr = ['File', 'Class', 'Pages', 'Paper (page)', 'Content', 'Scale(s) found', 'NTS', 'Drawing no. (guess)',
               'Title (guess)', 'CAD layers', 'Identical to', 'Use in brief? (Y/N)', 'Era (existing/proposed/as-built)', 'Notes']
        ws.append(hdr)
        for r in recs:
            p = (r.get('pages') or [{}])[0]
            ws.append([r['file'], r.get('class'), r.get('n_pages'), p.get('iso') or (str(p.get('size_mm')) if p else None),
                       p.get('content'), ', '.join(s['text'] for s in p.get('scales', [])) or None, p.get('nts'),
                       ', '.join(p.get('drawing_no_candidates', [])) or None, ' | '.join(p.get('title_candidates', [])[:2]) or None,
                       len(p.get('layers', []) or r.get('ocg_layers', [])) or None, ', '.join(r.get('identical_to', [])) or None,
                       None, None, r.get('error')])
        for col, w in zip('ABCDEFGHIJKLMN', [48, 14, 6, 12, 9, 22, 6, 20, 48, 8, 30, 10, 18, 30]):
            ws.column_dimensions[col].width = w
        ws.freeze_panes = 'A2'; ws.auto_filter.ref = ws.dimensions
        wb.save(out / 'drawing_index.xlsx')
    except ImportError:
        print('openpyxl not installed: skipped drawing_index.xlsx (inventory.json has everything)')
    print(f'{len(recs)} files -> {out}')


if __name__ == '__main__':
    main()
