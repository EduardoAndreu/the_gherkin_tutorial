"""Shared helpers for the aecfn-building-modelling-brief scripts.

- Spec access by key path:  levels[id=L02].ffl_m , structure.cores[0].polygon_m , levels[3].ssl_m
- Measured values may be plain numbers or "V objects": {"value": 3.2, "unit": "m", "src": {...}}
- ISO paper sizes, scale parsing, affine helpers, fonts.
"""
import json, math, re
from pathlib import Path

import numpy as np

ISO_MM = {'A0': (841, 1189), 'A1': (594, 841), 'A2': (420, 594), 'A3': (297, 420), 'A4': (210, 297)}
PT_TO_MM = 25.4 / 72.0

# --------------------------------------------------------------------------- paper / scale

def iso_size(w_mm, h_mm, tol_mm=4.0):
    """Return ('A1', 'landscape') if the page matches an ISO size within tol, else (None, orient)."""
    s, l = sorted((w_mm, h_mm))
    orient = 'landscape' if w_mm >= h_mm else 'portrait'
    for k, (a, b) in ISO_MM.items():
        if abs(a - s) <= tol_mm and abs(b - l) <= tol_mm:
            return k, orient
    return None, orient


SCALE_RE = re.compile(r'(?<![\d.])1\s*[:/]\s*(\d{1,5})(?![\d.])(?:\s*(?:@|at|on)\s*(A[0-4]))?', re.I)
NTS_RE = re.compile(r'\bN\.?\s?T\.?\s?S\.?\b|not\s+to\s+scale', re.I)


def parse_scales(text):
    """All '1:N' (+ optional '@A1') occurrences -> list of dicts, largest denominator first, deduplicated."""
    out, seen = [], set()
    for m in SCALE_RE.finditer(text or ''):
        d = int(m.group(1))
        if d < 1 or d > 50000:
            continue
        key = (d, (m.group(2) or '').upper())
        if key in seen:
            continue
        seen.add(key)
        out.append({'denominator': d, 'paper': key[1] or None, 'text': m.group(0)})
    out.sort(key=lambda x: -x['denominator'])
    return out


def m_per_pt(denominator, page_paper=None, stated_paper=None):
    """Metres of real world per PDF point at a stated scale.
    If the drawing states '1:100 @ A1' but the PDF page is A3, the effective scale is different:
    multiply by the linear ratio stated/page."""
    base = PT_TO_MM / 1000.0 * denominator
    if stated_paper and page_paper and stated_paper != page_paper:
        ratio = ISO_MM[stated_paper][1] / ISO_MM[page_paper][1]
        return base * ratio, f'page is {page_paper} but scale is stated @ {stated_paper}: x{ratio:.4f}'
    return base, None

# --------------------------------------------------------------------------- V objects / spec paths

def is_v(o):
    return isinstance(o, dict) and 'value' in o and ('src' in o or 'unit' in o)


def val(o):
    return o['value'] if is_v(o) else o


_TOK = re.compile(r'([^.\[\]]+)|\[(\d+)\]|\[([^=\]]+)=([^\]]+)\]')


def _tokens(path):
    toks = []
    for m in _TOK.finditer(path):
        if m.group(1) is not None:
            toks.append(('k', m.group(1)))
        elif m.group(2) is not None:
            toks.append(('i', int(m.group(2))))
        else:
            toks.append(('sel', (m.group(3).strip(), m.group(4).strip())))
    return toks


class SpecKeyError(KeyError):
    pass


def get_path(data, path, unwrap=True):
    """Resolve 'a.b[2].c', 'levels[id=L03].ffl_m' (selector matches str(item[field]))."""
    cur = data
    for kind, t in _tokens(path):
        try:
            if kind == 'k':
                cur = cur[t]
            elif kind == 'i':
                cur = cur[t]
            else:
                f, want = t
                hits = [x for x in cur if isinstance(x, dict) and str(x.get(f)) == want]
                if len(hits) != 1:
                    raise SpecKeyError(f'{path}: selector [{f}={want}] matched {len(hits)} items')
                cur = hits[0]
        except (KeyError, IndexError, TypeError) as e:
            raise SpecKeyError(f'{path}: cannot resolve at {t!r} ({e})')
    return val(cur) if unwrap else cur


def get_many(data, path):
    """Like get_path but '[*]' fans out over a list: 'structure.columns[*].xy' -> [xy, xy, ...]."""
    if '[*]' not in path:
        return get_path(data, path)
    head, _, tail = path.partition('[*]')
    items = get_path(data, head, unwrap=False) if head else data
    tail = tail.lstrip('.')
    out = []
    for it in items:
        out.append(get_many(it, tail) if tail else val(it))
    return out


def load_json(p):
    with open(p) as f:
        return json.load(f)


def save_json(o, p):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    with open(p, 'w') as f:
        json.dump(o, f, indent=1, ensure_ascii=False, default=_np_default)


def _np_default(o):
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))

# --------------------------------------------------------------------------- geometry

def fit_circle(pts):
    """Algebraic least-squares circle -> (cx, cy, r, rms)."""
    P = np.asarray(pts, float)
    A = np.c_[2 * P, np.ones(len(P))]
    b = (P ** 2).sum(1)
    cx, cy, c = np.linalg.lstsq(A, b, rcond=None)[0]
    r = math.sqrt(c + cx * cx + cy * cy)
    rms = float(np.sqrt(np.mean((np.hypot(P[:, 0] - cx, P[:, 1] - cy) - r) ** 2)))
    return float(cx), float(cy), float(r), rms


def similarity_from_pairs(src, dst):
    """Least-squares similarity (rotation, uniform scale, translation) mapping src->dst points.
    Returns 2x3 matrix M with dst = M @ [x, y, 1], plus rms residual."""
    S = np.asarray(src, float); D = np.asarray(dst, float)
    n = len(S)
    A = np.zeros((2 * n, 4)); b = np.zeros(2 * n)
    A[0::2] = np.c_[S[:, 0], -S[:, 1], np.ones(n), np.zeros(n)]
    A[1::2] = np.c_[S[:, 1], S[:, 0], np.zeros(n), np.ones(n)]
    b[0::2] = D[:, 0]; b[1::2] = D[:, 1]
    a, c, tx, ty = np.linalg.lstsq(A, b, rcond=None)[0]
    M = np.array([[a, -c, tx], [c, a, ty]])
    res = D - (S @ M[:, :2].T + M[:, 2])
    return M, float(np.sqrt((res ** 2).sum(1).mean())) if n > 2 else 0.0


def affine_from_pairs(src, dst):
    """Least-squares full affine src->dst (needs >=3 non-collinear pairs)."""
    S = np.asarray(src, float); D = np.asarray(dst, float)
    A = np.c_[S, np.ones(len(S))]
    M = np.linalg.lstsq(A, D, rcond=None)[0].T
    res = D - (S @ M[:, :2].T + M[:, 2])
    return M, float(np.sqrt((res ** 2).sum(1).mean()))


def apply(M, x, y):
    M = np.asarray(M, float)
    x = np.asarray(x, float); y = np.asarray(y, float)
    return M[0, 0] * x + M[0, 1] * y + M[0, 2], M[1, 0] * x + M[1, 1] * y + M[1, 2]


def invert(M):
    M = np.asarray(M, float)
    A = np.vstack([M, [0, 0, 1]])
    return np.linalg.inv(A)[:2]

# --------------------------------------------------------------------------- calibration records

def load_calibration(path):
    c = load_json(path)
    return {r['id']: r for r in c['records']}


def world_to_pt(rec):
    """2x3 matrix mapping world (X,Y) or (s,z) metres -> PDF page points (y down, as pymupdf)."""
    return np.asarray(rec['world_to_pt'], float)

# --------------------------------------------------------------------------- fonts

def font(size):
    from PIL import ImageFont
    try:
        import matplotlib.font_manager as fm
        return ImageFont.truetype(fm.findfont('DejaVu Sans'), size)
    except Exception:
        try:
            return ImageFont.load_default(size)
        except TypeError:
            return ImageFont.load_default()
