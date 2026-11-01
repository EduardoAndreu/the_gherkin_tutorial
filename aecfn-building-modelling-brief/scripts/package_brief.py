#!/usr/bin/env python3
"""Assemble the hand-over brief and zip it. Only what the modelling agent needs goes in.

  package_brief.py --name acme_tower_brief_v1 --out /path/to/acme_tower_brief_v1.zip
      --readme 00_README.md --spec spec/spec.json
      --underlays underlays/ --markups markups/ [--markups-readme markups/README.md]
      [--images images/curated/] [--exclude PATTERN ...] [--extra FILE ...] [--stage DIR]

Layout of the zip:
  <name>/00_README.md  spec/spec.json  underlays/*.png|json
  <name>/markups/<SET>/*.png|_preview.png|.json  markups/README.md  images/<folders>/*  images/IMAGE_NOTES.md
Markup definition files, scripts, calibration records, raw PDFs and working notes are left out on purpose:
the underlays carry the calibration, the markups carry the meaning, the spec carries the numbers.
Runs validate.py on the staged folder first and refuses to zip if it reports errors.
"""
import argparse, fnmatch, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path

HERE = Path(__file__).parent


def copy_tree(src, dst, patterns, exclude):
    n = 0
    for p in Path(src).rglob('*'):
        if p.is_dir() or p.name.startswith('.'):
            continue
        rel = p.relative_to(src)
        if patterns and not any(fnmatch.fnmatch(p.name, pat) for pat in patterns):
            continue
        if any(fnmatch.fnmatch(str(rel), e) or fnmatch.fnmatch(p.name, e) for e in exclude):
            continue
        q = Path(dst) / rel; q.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(p, q); n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--readme', required=True); ap.add_argument('--spec', required=True)
    ap.add_argument('--underlays', required=True); ap.add_argument('--markups', required=True); ap.add_argument('--markups-readme')
    ap.add_argument('--images'); ap.add_argument('--exclude', nargs='*', default=[]); ap.add_argument('--extra', nargs='*', default=[])
    ap.add_argument('--stage')
    a = ap.parse_args()
    stage_root = Path(a.stage) if a.stage else Path(tempfile.mkdtemp())
    B = stage_root / a.name
    if B.exists():
        shutil.rmtree(B)
    (B / 'spec').mkdir(parents=True)
    shutil.copy2(a.readme, B / '00_README.md')
    shutil.copy2(a.spec, B / 'spec' / Path(a.spec).name)
    nu = copy_tree(a.underlays, B / 'underlays', ['*.png', '*.json'], a.exclude)
    nm = copy_tree(a.markups, B / 'markups', ['*.png', '*.json'], a.exclude + ['*def*.json', 'defs/*'])
    if a.markups_readme:
        shutil.copy2(a.markups_readme, B / 'markups' / 'README.md')
    ni = copy_tree(a.images, B / 'images', ['*.jpg', '*.jpeg', '*.png', 'IMAGE_NOTES.md'], a.exclude) if a.images else 0
    for e in a.extra:
        shutil.copy2(e, B / Path(e).name)
    print(f'staged {B}: underlays {nu}, markup files {nm}, images {ni}')
    r = subprocess.run([sys.executable, str(HERE / 'validate.py'), '--spec', str(B / 'spec' / Path(a.spec).name),
                        '--markups', str(B / 'markups'), '--underlays', str(B / 'underlays'), '--brief', str(B)])
    if r.returncode:
        sys.exit('validate.py reported errors: fix them, then package again (staged folder kept for inspection)')
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in sorted(B.rglob('*')):
            if p.is_file():
                z.write(p, str(p.relative_to(stage_root)))
    print(f'wrote {out} ({out.stat().st_size / 1e6:.1f} MB)')


if __name__ == '__main__':
    main()
