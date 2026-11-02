#!/usr/bin/env python3
"""Assemble the hand-over brief folder. Only what the modelling agent needs goes in.

  package_brief.py --out /path/to/acme_tower_brief_v1
      --readme 00_README.md --spec spec/spec.json
      --underlays underlays/ --markups markups/ [--markups-readme markups/README.md]
      [--images images/curated/] [--exclude PATTERN ...] [--extra FILE ...] [--stage DIR]

Layout of the brief folder:
  00_README.md  spec/spec.json  underlays/*.png|json
  markups/<SET>/*.png|_preview.png|.json  markups/README.md  images/<folders>/*  images/IMAGE_NOTES.md
Markup definition files, scripts, calibration records, raw PDFs and working notes are left out on purpose:
the underlays carry the calibration, the markups carry the meaning, the spec carries the numbers.
The brief is assembled in a staging folder and validated there (validate.py); only a clean brief is written to --out.
An existing --out is replaced only if it is a previous brief (has 00_README.md and spec/).
"""
import argparse, fnmatch, shutil, subprocess, sys, tempfile
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
    ap.add_argument('--out', required=True, help='the brief folder to write, e.g. projects/acme/acme_tower_brief_v1')
    ap.add_argument('--readme', required=True); ap.add_argument('--spec', required=True)
    ap.add_argument('--underlays', required=True); ap.add_argument('--markups', required=True); ap.add_argument('--markups-readme')
    ap.add_argument('--images'); ap.add_argument('--exclude', nargs='*', default=[]); ap.add_argument('--extra', nargs='*', default=[])
    ap.add_argument('--stage')
    a = ap.parse_args()
    out = Path(a.out).resolve()
    if out.exists() and not ((out / '00_README.md').exists() and (out / 'spec').is_dir()):
        sys.exit(f'{out} exists and is not a previous brief: choose another --out')
    stage_root = Path(a.stage) if a.stage else Path(tempfile.mkdtemp())
    B = stage_root / out.name
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
        sys.exit(f'validate.py reported errors: fix them, then package again. Nothing written to {out}; '
                 f'staged folder kept for inspection: {B}')
    if out.exists():
        shutil.rmtree(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(B), str(out))
    size = sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    print(f'wrote brief folder {out} ({size / 1e6:.1f} MB)')


if __name__ == '__main__':
    main()
