#!/usr/bin/env python3
"""Curate reference photos: find duplicates, preview, then copy into domain folders under clean names.

  curate_images.py scan SRC_DIR OUT_DIR
      -> OUT_DIR/image_scan.csv (file, size_px, sha1, dHash, near-duplicate group) + OUT_DIR/_previews/*.jpg
         Look at every preview, then fill in a mapping CSV.
  curate_images.py apply MAPPING.csv DEST_DIR
      MAPPING.csv columns: source, new_name, keep (Y/N), notes
      new_name like "01_structure/STR-01_construction_node-deck.jpg"; WEBP/HEIC/TIFF are converted losslessly
      to PNG; JPG/PNG are copied unchanged. Writes DEST_DIR/image_index.csv (new -> original, notes).
Originals are never modified or deleted.
"""
import csv, hashlib, sys
from pathlib import Path

from PIL import Image

EXT = {'.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff', '.heic', '.bmp', '.gif'}


def dhash(im, n=8):
    g = im.convert('L').resize((n + 1, n))
    px = list(g.getdata())
    return ''.join('1' if px[r * (n + 1) + c] > px[r * (n + 1) + c + 1] else '0' for r in range(n) for c in range(n))


def scan(src, out):
    out = Path(out); (out / '_previews').mkdir(parents=True, exist_ok=True)
    rows = []
    for f in sorted(p for p in Path(src).rglob('*') if p.suffix.lower() in EXT):
        try:
            im = Image.open(f); im.seek(0)
        except Exception as e:
            print('skip', f, e); continue
        rows.append(dict(file=str(f), w=im.width, h=im.height, sha1=hashlib.sha1(f.read_bytes()).hexdigest()[:12], dhash=dhash(im)))
        pv = im.convert('RGB'); pv.thumbnail((1400, 1400)); pv.save(out / '_previews' / (f.stem[:90] + '.jpg'), quality=88)
    group = {}
    for i, r in enumerate(rows):
        for j in range(i):
            s = rows[j]
            if r['sha1'] == s['sha1'] or sum(a != b for a, b in zip(r['dhash'], s['dhash'])) <= 6:
                group[i] = group.get(j, j); break
    for i, r in enumerate(rows):
        r['dup_group'] = group.get(i, i) if (i in group or i in group.values()) else ''
        r['identical'] = any(rows[k]['sha1'] == r['sha1'] for k in range(len(rows)) if k != i)
    with open(out / 'image_scan.csv', 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ['file']); w.writeheader(); w.writerows(rows)
    print(f'{len(rows)} images scanned; near-duplicate groups share a dup_group id (keep the highest resolution).')


def apply_map(mapping, dest):
    dest = Path(dest); idx = []
    for r in csv.DictReader(open(mapping)):
        if (r.get('keep') or 'Y').strip().upper() != 'Y':
            continue
        src = Path(r['source']); new = dest / r['new_name']; new.parent.mkdir(parents=True, exist_ok=True)
        im = Image.open(src); im.seek(0)
        if src.suffix.lower() in ('.jpg', '.jpeg', '.png') and new.suffix.lower() == src.suffix.lower():
            new.write_bytes(src.read_bytes())
        else:
            if new.suffix.lower() not in ('.png',):
                new = new.with_suffix('.png')
            im.save(new)
        idx.append(dict(file=str(new.relative_to(dest)), original=src.name, notes=r.get('notes', '')))
    with open(dest / 'image_index.csv', 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=['file', 'original', 'notes']); w.writeheader(); w.writerows(idx)
    print(f'{len(idx)} images written to {dest}')


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] not in ('scan', 'apply'):
        sys.exit(__doc__)
    (scan if sys.argv[1] == 'scan' else apply_map)(sys.argv[2], sys.argv[3])
