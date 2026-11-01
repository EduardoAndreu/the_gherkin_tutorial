#!/usr/bin/env python3
"""Render a colour-coded markup from a declarative definition, drawing spec values onto a rectified underlay.

Traceable by construction: every MEASURED item takes its geometry and numbers from spec keys, never from literals.
The label is a template filled from the resolved values ("Slab edge r = {r} m"), so what is printed is what the spec holds.

usage: markup.py DEF.json [DEF2.json ...] [--out markups/] [--style style.json]

Definition (paths relative to the definition file unless absolute):
{
  "id": "STR_L02_plan", "set": "STR", "build_step": "1 structure",
  "title": "Level 02 - structure", "spec": "../spec/spec.json", "underlay": "../underlays/PLN_L02.json",
  "crop_world": [a0, b0, a1, b1],            optional window in world metres
  "items": [ { "type": ..., "cls": ..., "kind": "measured|assumed|observed|issue", ... }, ... ]
}
Item types (world metres; plans use X,Y, sections use s,z):
  polygon / polyline   pts_key | pts            closed / open line; pts_key may use [*] to gather points
  circle               center_key|center, r_key|r
  points               pts_key | pts, size_px   markers (columns, nodes)
  hline / vline        z_key|z  /  s_key|s, extent [from, to]   level lines on sections, grid lines
  band                 z0_key|z0, z1_key|z1, extent             thickness zone on a section (raised floor, slab)
  dim                  a, b (anchors) , value_key               dimension line; label shows the spec value
  note                 at, text                                 free note (assumed / observed must cite "source")
  issue                at, r, issue_id, text                    red cloud for an open issue
Derived geometry: "vars": {"k": "envelope.slope.k", ...} + any field as "<name>_expr" (e.g. "pts_expr":
  "[[-(k*z+c), z] for z in (z0, z1)]"); vars are recorded as keys, so validate.py re-checks them.
Common fields: label (template; {value}/{r}/{z}/{s}/{n} and var-name placeholders), source (required unless measured),
  at (tag position), literal_ok ("reason": allows literal geometry in a measured item, e.g. the world origin).
"""
import argparse, json, math, re, sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from bmb_common import get_path, get_many, load_json, save_json, font

HERE = Path(__file__).parent
DEFAULT_STYLE = HERE.parent / 'assets' / 'style.json'


def rel(base, p):
    p = Path(p)
    return p if p.is_absolute() else (base / p).resolve()


class Canvas:
    def __init__(self, underlay_json, style, crop=None, fade=None, title='', subtitle=''):
        self.meta = load_json(underlay_json)
        img = Image.open(Path(underlay_json).with_name(self.meta['file'])).convert('L')
        self.ppm = 1 / self.meta['metres_per_px']; self.ox, self.oy = self.meta['world_origin_px']
        if crop:
            a0, b0, a1, b1 = crop
            x0, y0 = self.w2p(a0, b1); x1, y1 = self.w2p(a1, b0)
            img = img.crop((int(x0), int(y0), int(x1), int(y1))); self.ox -= int(x0); self.oy -= int(y0)
        f = style['underlay']['fade'] if fade is None else fade
        arr = np.asarray(img, np.float32); arr = 255 - f * (255 - arr)
        self.style = style; self.title, self.subtitle = title, subtitle
        self.img = Image.fromarray(arr.clip(0, 255).astype(np.uint8)).convert('RGB')
        self.d = ImageDraw.Draw(self.img)
        self.lw = max(2, int(round(self.img.width / 1500)))
        self.tag_font = font(max(14, self.img.width // 110))

    def w2p(self, a, b):
        return self.ox + a * self.ppm, self.oy - b * self.ppm

    def colour(self, cls, kind):
        if kind == 'issue':
            return tuple(self.style['kinds']['issue']['colour'])
        return tuple(self.style['classes'].get(cls, self.style['classes']['default']))

    def line(self, pts, col, kind, closed=False, width=None):
        w = width or self.lw * (2 if kind == 'issue' else 1)
        P = [self.w2p(*p) for p in pts] + ([self.w2p(*pts[0])] if closed else [])
        dash = self.style['kinds'][kind].get('dash_px')
        if not dash:
            self.d.line(P, fill=col, width=w, joint='curve'); return
        on, off = [v * self.lw / 2 for v in dash]
        for (x0, y0), (x1, y1) in zip(P[:-1], P[1:]):
            L = math.hypot(x1 - x0, y1 - y0); t = 0.0
            while t < L:
                t1 = min(t + on, L)
                self.d.line([(x0 + (x1 - x0) * t / L, y0 + (y1 - y0) * t / L), (x0 + (x1 - x0) * t1 / L, y0 + (y1 - y0) * t1 / L)],
                            fill=col, width=w)
                t = t1 + off

    def tag(self, n, a, b, col):
        x, y = self.w2p(a, b); r = self.tag_font.size * 0.9
        self.d.ellipse((x - r, y - r, x + r, y + r), fill='white', outline=col, width=self.lw)
        self.d.text((x, y), str(n), fill=col, font=self.tag_font, anchor='mm')

    def cloud(self, a, b, r_m, col):
        x, y = self.w2p(a, b); R = r_m * self.ppm; k = 10
        for i in range(k):
            t = 2 * math.pi * i / k
            cx, cy = x + R * math.cos(t), y + R * math.sin(t); rr = R * 0.38
            self.d.arc((cx - rr, cy - rr, cx + rr, cy + rr), 0, 360, fill=col, width=self.lw * 2)

    def compose(self, legend, notes, out_png):
        style = self.style; W, H = self.img.size
        pw = max(700, W // 3); th = max(90, H // 25)
        canvas = Image.new('RGB', (W + pw, H + th), 'white'); canvas.paste(self.img, (0, th))
        d = ImageDraw.Draw(canvas)
        tw = W + pw - 40   # fit the title strip to the canvas width (tall, narrow sections clip otherwise)
        f1 = font(max(14, min(th // 2, int(tw / max(1, len(self.title)) / 0.58))))
        f2 = font(max(12, min(th // 4, int(tw / max(1, len(self.subtitle)) / 0.58)))); f3 = font(max(13, pw // 42))
        need = int(f1.size * 1.35 + f2.size * 1.5)
        if need > th:   # grow the strip so the subtitle stays inside it
            th2 = need; canvas2 = Image.new('RGB', (W + pw, H + th2), 'white'); canvas2.paste(self.img, (0, th2))
            canvas, th = canvas2, th2; d = ImageDraw.Draw(canvas)
        d.rectangle((0, 0, W + pw, th), fill=tuple(style['title_strip']))
        d.text((20, int(f1.size * 0.15)), self.title, fill='white', font=f1)
        d.text((20, int(f1.size * 1.35)), self.subtitle, fill=(220, 220, 220), font=f2)
        x0, y = W + 30, th + 30; lh = int(f3.size * 1.6)
        d.text((x0, y), 'LEGEND', fill='black', font=f3); y += lh
        for cls, kind, lab in legend:
            col = self.colour(cls, kind); d.line((x0, y + lh // 3, x0 + 60, y + lh // 3), fill=col, width=self.lw * 2)
            d.text((x0 + 75, y), f'{lab}  [{kind}]', fill='black', font=f3); y += lh
        y += lh // 2; d.text((x0, y), 'LINE TYPES', fill='black', font=f3); y += lh
        for k, v in style['kinds'].items():
            d.text((x0, y), f"{v['label']}", fill='black', font=f3); y += lh
        sb = 10 if W / self.ppm > 40 else 5 if W / self.ppm > 15 else 1
        y += lh // 2; d.line((x0, y + 10, x0 + sb * self.ppm, y + 10), fill='black', width=self.lw * 2)
        d.text((x0, y + 18), f'{sb} m (true scale on this image)', fill='black', font=f3); y += lh * 2
        d.text((x0, y), 'CALLOUTS', fill='black', font=f3); y += lh
        maxch = max(30, int(pw / (f3.size * 0.55)))
        for n, kind, text in notes:
            for j, chunk in enumerate(_wrap(f'{n}. [{kind}] {text}', maxch)):
                d.text((x0 + (0 if j == 0 else 25), y), chunk, fill=self.colour(None, kind) if kind == 'issue' else 'black', font=f3)
                y += lh
        canvas.save(out_png, optimize=True)
        pv = canvas.copy(); pv.thumbnail((1600, 1600)); pv.save(str(out_png).replace('.png', '_preview.png'))
        return canvas.size


def _wrap(s, n):
    words, lines, cur = s.split(), [], ''
    for w in words:
        if len(cur) + len(w) + 1 > n:
            lines.append(cur); cur = w
        else:
            cur = f'{cur} {w}'.strip()
    return lines + [cur]


SAFE = {'abs': abs, 'min': min, 'max': max, 'round': round, 'range': range, 'len': len, 'sin': math.sin, 'cos': math.cos,
        'tan': math.tan, 'radians': math.radians, 'degrees': math.degrees, 'sqrt': math.sqrt, 'pi': math.pi}


def _resolve(spec, it, name, required=True, env=None):
    if f'{name}_expr' in it:   # derived geometry: expression over the item's named spec keys ("vars")
        return eval(it[f'{name}_expr'], {'__builtins__': {}}, {**SAFE, **(env or {})}), 'expr:' + it[f'{name}_expr']
    if f'{name}_key' in it:
        v = get_many(spec, it[f'{name}_key']); return v, it[f'{name}_key']
    if name in it:
        return it[name], None
    if required:
        raise ValueError(f"item {it.get('label', it['type'])}: needs '{name}' or '{name}_key'")
    return None, None


def render(def_path, out_dir, style):
    base = Path(def_path).parent; D = load_json(def_path)
    spec = load_json(rel(base, D['spec']))
    und = rel(base, D['underlay'])
    sv = spec.get('schema_version') or spec.get('version')
    cv = Canvas(und, style, D.get('crop_world'), D.get('fade'), D.get('title', D['id']),
                f"{D.get('set', '')}  |  {D.get('build_step', '')}  |  spec v{sv}  |  underlay {Path(und).stem}")
    legend, notes, side = [], [], []
    for n, it in enumerate(D['items'], 1):
        t, cls = it['type'], it.get('cls', 'default')
        kind = it.get('kind', 'issue' if t == 'issue' else 'measured')
        if kind != 'measured' and not it.get('source') and t != 'issue':
            raise ValueError(f'item {n}: {kind} items must cite a "source"')
        col = cv.colour(cls, kind); keys, vals = {}, {}
        env = {}
        for vn, vk in (it.get('vars') or {}).items():
            env[vn] = get_many(spec, vk); keys[f'var:{vn}'] = vk; vals[f'var:{vn}'] = env[vn]

        def R(name, required=True):
            v, k = _resolve(spec, it, name, required, env)
            if k and k.startswith('expr:'):
                vals[f'expr:{name}'] = it[f'{name}_expr']
            elif k:
                keys[name] = k
            elif v is not None and kind == 'measured' and not it.get('literal_ok') and name not in ('at', 'extent', 'a', 'b'):
                raise ValueError(f"item {n} ({it.get('label', t)}): measured item uses literal '{name}' - use '{name}_key' or set literal_ok")
            if v is not None:
                vals[name] = v
            return v

        anchor = None
        if t in ('polygon', 'polyline', 'points'):
            pts = R('pts')
            pts = [p for p in pts] if pts and isinstance(pts[0][0], (int, float)) else [q for p in pts for q in p]
            if t == 'points':
                s = it.get('size_px', cv.lw * 4)
                for a, b in pts:
                    x, y = cv.w2p(a, b); cv.d.ellipse((x - s, y - s, x + s, y + s), outline=col, width=cv.lw)
            else:
                cv.line(pts, col, kind, closed=(t == 'polygon'))
            anchor = it.get('at') or list(np.mean(np.array(pts, float), 0)); vals['n'] = len(pts)
        elif t == 'circle':
            c = R('center'); r = R('r'); th = np.linspace(0, 2 * np.pi, 361)
            cv.line(list(zip(c[0] + r * np.cos(th), c[1] + r * np.sin(th))), col, kind)
            anchor = it.get('at') or [c[0] + r * math.cos(math.radians(it.get('tag_angle_deg', 35))), c[1] + r * math.sin(math.radians(it.get('tag_angle_deg', 35)))]
        elif t in ('hline', 'vline'):
            ext = it.get('extent') or ([-1e3, 1e3])
            if t == 'hline':
                z = R('z'); cv.line([(ext[0], z), (ext[1], z)], col, kind); anchor = it.get('at') or [ext[1], z]
            else:
                s = R('s'); cv.line([(s, ext[0]), (s, ext[1])], col, kind); anchor = it.get('at') or [s, ext[1]]
        elif t == 'band':
            z0, z1 = R('z0'), R('z1'); ext = it.get('extent') or [-1e3, 1e3]
            cv.line([(ext[0], z0), (ext[1], z0), (ext[1], z1), (ext[0], z1)], col, kind, closed=True)
            anchor = it.get('at') or [ext[1], (z0 + z1) / 2]; vals['thickness'] = abs(z1 - z0)
        elif t == 'dim':
            a, b = it['a'], it['b']; R('value')
            cv.line([a, b], col, kind); anchor = it.get('at') or [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
        elif t == 'note':
            anchor = it['at']
        elif t == 'issue':
            anchor = it['at']; cv.cloud(anchor[0], anchor[1], it.get('r', 1.5), col)
        else:
            raise ValueError(f'unknown item type {t}')
        rnd = lambda v: round(v, 3) if isinstance(v, float) else v
        fmt = {k: rnd(v) for k, v in vals.items() if not isinstance(v, (list, dict)) and ':' not in k}
        computed = {k: fmt[k] for k in list(fmt) if f'expr:{k}' in vals}
        fmt.update({k[4:]: rnd(v) for k, v in vals.items() if k.startswith('var:') and not isinstance(v, (list, dict))})
        if computed:          # derived geometry: the computed result is {value}; var names keep their own values
            fmt['value'] = next(iter(computed.values()))
        if 'value' not in fmt:
            for k in ('r', 'z', 's', 'thickness'):
                if k in fmt:
                    fmt['value'] = fmt[k]; break
        label = (it.get('label') or it.get('text') or cls).format(**fmt)
        if it.get('issue_id'):
            label = f"{it['issue_id']}: {label}"
        if keys:
            label += '  [' + ', '.join(dict.fromkeys(keys.values())) + ']'
        if it.get('source'):
            label += f"  (src: {it['source']})"
        cv.tag(n, anchor[0], anchor[1], col)
        notes.append((n, kind, label))
        if (cls, kind) not in [(c, k) for c, k, _ in legend] and t not in ('note', 'issue'):
            legend.append((cls, kind, cls.replace('_', ' ')))
        side.append(dict(n=n, type=t, cls=cls, kind=kind, keys=keys,
                         values={k: v for k, v in vals.items() if not isinstance(v, list) or len(v) < 200},
                         label_template=it.get('label') or it.get('text'), label=label, source=it.get('source'),
                         issue_id=it.get('issue_id'), anchor_world=[float(anchor[0]), float(anchor[1])]))
    out_dir = Path(out_dir) / D.get('set', '')
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / f"{D['id']}.png"
    size = cv.compose(legend, notes, png)
    save_json(dict(id=D['id'], set=D.get('set'), build_step=D.get('build_step'), title=D.get('title'),
                   file=png.name, preview=png.name.replace('.png', '_preview.png'), underlay=Path(und).name,
                   spec_version=sv, definition=str(Path(def_path).name), size_px=list(size), items=side),
              out_dir / f"{D['id']}.json")
    return png


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('defs', nargs='+'); ap.add_argument('--out', default='markups')
    ap.add_argument('--style', default=str(DEFAULT_STYLE))
    a = ap.parse_args()
    style = load_json(a.style)
    for d in a.defs:
        print('->', render(d, a.out, style))


if __name__ == '__main__':
    main()
