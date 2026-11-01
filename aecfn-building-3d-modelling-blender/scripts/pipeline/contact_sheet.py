"""Labelled contact sheets and the Before-vs-After progression matrix (Pillow only, no bpy)."""
import glob, math, os, re, sys
from PIL import Image, ImageDraw, ImageFont


def _font(sz):
    for f in ("/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/SFNSMono.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"):
        if os.path.exists(f):
            return ImageFont.truetype(f, sz)
    return ImageFont.load_default()


def contact_sheet(paths, dest, label_prefix="", cols=4, tile_w=640):
    paths = [p for p in paths if os.path.exists(p)]
    if not paths:
        return None
    ims = [Image.open(p).convert("RGB") for p in paths]
    th = int(tile_w * ims[0].height / ims[0].width)
    rows = math.ceil(len(ims) / cols)
    sheet = Image.new("RGB", (cols * tile_w, rows * th), (11, 26, 46))
    d = ImageDraw.Draw(sheet); f = _font(max(12, tile_w // 45))
    for i, (im, p) in enumerate(zip(ims, paths)):
        x, y = (i % cols) * tile_w, (i // cols) * th
        sheet.paste(im.resize((tile_w, th)), (x, y))
        lab = f"{label_prefix} {os.path.splitext(os.path.basename(p))[0]}".strip()
        d.rectangle([x, y, x + tile_w * 0.75, y + f.size + 8], fill=(11, 26, 46))
        d.text((x + 6, y + 4), lab, fill=(230, 236, 245), font=f)
    sheet.save(dest)
    return dest


def progression_matrix(render_root, dest, passes=None, ref_dir=None, tile_w=420, cams=None, labels=None):
    """Rows = cameras, columns = selected passes (+ optional reference column first).
    passes: explicit render folder names in column order (any name, e.g. P07_BEFORE); default = every P\\d\\d[_FINAL].
    cams: optional camera-name prefixes (row filter, in order); labels: optional column titles."""
    if passes:
        dirs = [os.path.join(render_root, p) for p in passes if os.path.isdir(os.path.join(render_root, p))]
    else:
        dirs = sorted(d for d in glob.glob(os.path.join(render_root, "P*")) if re.fullmatch(r"P\d\d(_FINAL)?", os.path.basename(d)))
    found = sorted({os.path.basename(p) for d in dirs for p in glob.glob(os.path.join(d, "Cam_*.png"))})
    cams = [c for w in cams for c in found if c.startswith(w)] if cams else found
    cols = (["REF"] if ref_dir else []) + [os.path.basename(d) for d in dirs]
    titles = (["REF"] if ref_dir else []) + (list(labels) if labels else [os.path.basename(d) for d in dirs])
    th = int(tile_w * 9 / 16); hdr = 40; f = _font(18)
    sheet = Image.new("RGB", (len(cols) * tile_w, hdr + len(cams) * th), (11, 26, 46))
    dr = ImageDraw.Draw(sheet)
    for ci, col in enumerate(cols):
        dr.text((ci * tile_w + 8, 10), titles[ci], fill=(255, 207, 110), font=f)
        for ri, cam in enumerate(cams):
            src = os.path.join(ref_dir, cam) if col == "REF" else os.path.join(render_root, col, cam)
            if col != "REF" and not os.path.exists(src):  # a column may borrow a camera from a sibling folder (P07_BEFORE)
                alt = glob.glob(os.path.join(render_root, col + "_*", cam))
                src = alt[0] if alt else src
            if os.path.exists(src):
                sheet.paste(Image.open(src).convert("RGB").resize((tile_w, th)), (ci * tile_w, hdr + ri * th))
    sheet.save(dest)
    return dest


if __name__ == "__main__":  # python3 -m pipeline.contact_sheet build/renders build/progression_matrix.png [P00,P10,P20,P30_FINAL]
    progression_matrix(sys.argv[1], sys.argv[2], sys.argv[3].split(",") if len(sys.argv) > 3 else None)
