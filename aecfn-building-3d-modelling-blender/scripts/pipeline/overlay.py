"""Overlay gate — the model's drawing linework over the calibrated underlays, with deviation figures. numpy + Pillow.
  python3 -m pipeline.run overlay --pass N [--side-by-side]
For every spec["overlays"] entry (default: every underlay) the Stage 01 geometry is drawn exactly as a drawing would be:
  plan      cut at underlay z + cut_offset_m (default 1.2): CSG cut polygons + mesh cut segments, plus what is seen
            below the cut within depth_m (default 4.5 = one storey)
  section   cut at the underlay's plane: cut polygons/segments plus what stands within depth_m (default 1.5) behind it
rasterised at the underlay's scale (max overlay_px wide), then compared pixel-wise with the drawing's ink:
  model->drawing   for every model line pixel, distance to the nearest drawn pixel (p50/p90/p95/max, metres)
  drawing->model   for every drawn pixel inside a band around the model, distance to the nearest model line pixel
Pass = drawing->model p90 <= tol: every line the drawing draws has model geometry within tol (misplaced or missing
geometry fails). model->drawing is reported (p50, share within tol): a low share means the model shows detail the
drawing omits (mullions, members) OR extra wrong geometry - the orchestrator reads the composite to tell which.
Entries with "expect_tol_m" (another era) are reported, never failed.
Outputs: build/overlays/PNN/<underlay>_model.png, <underlay>_overlay.png, report.json ; build/overlay_matrix_PNN.png
"--side-by-side": for spec cameras carrying "underlay", the Cycles render beside the underlay at the same world window.
"""
import glob, json, os, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from pipeline.aep_spec import load_spec, out  # noqa: E402
from pipeline import underlays as U  # noqa: E402
from pipeline import geom_io as GIO  # noqa: E402
from pipeline.contact_sheet import _font  # noqa: E402


def edt(mask):
    """Euclidean distance transform (pixels) of ~mask: distance from every pixel to the nearest True pixel.
    scipy if present, else a separable Felzenszwalb pass in numpy."""
    try:
        from scipy import ndimage
        return ndimage.distance_transform_edt(~mask)
    except ImportError:
        pass
    INF = 1e12
    f = np.where(mask, 0.0, INF)

    def dt1d(rows):
        n = rows.shape[1]
        out = np.empty_like(rows)
        for r in range(rows.shape[0]):
            fr = rows[r]; v = np.zeros(n, int); z = np.zeros(n + 1); k = 0; v[0] = 0; z[0] = -INF; z[1] = INF
            for q in range(1, n):
                s = ((fr[q] + q * q) - (fr[v[k]] + v[k] * v[k])) / (2 * q - 2 * v[k])
                while s <= z[k]:
                    k -= 1
                    s = ((fr[q] + q * q) - (fr[v[k]] + v[k] * v[k])) / (2 * q - 2 * v[k])
                k += 1; v[k] = q; z[k] = s; z[k + 1] = INF
            k = 0
            for q in range(n):
                while z[k + 1] < q:
                    k += 1
                out[r, q] = (q - v[k]) ** 2 + fr[v[k]]
        return out
    d = dt1d(f)
    d = dt1d(d.T).T
    return np.sqrt(d)


def draw_model(spec, info, items, cfg, max_px):
    """Rasterise the model's linework for one underlay -> (edge mask, PIL image, metres per pixel)."""
    from pipeline.stage01_blueprint import view_for, _solid_union, _mesh_arrays
    if info["kind"] == "plan":
        sheet = dict(id=info["name"], kind="plan", z=info["z"] + float(cfg.get("cut_offset_m", spec.get("overlay_cut_offset_m", 1.2))), depth=float(cfg.get("depth_m", 4.5)))
    else:
        sheet = dict(id=info["name"], kind="section", axis="y", value=info["y"], depth=float(cfg.get("depth_m", 1.5)))
    v = view_for(spec, sheet, _solid_union(items), list(_mesh_arrays(items)), float(spec.get("sheet_edge_angle_deg", 45.0)))
    s = min(1.0, max_px / max(info["W"], info["H"])); W, H = max(16, int(info["W"] * s)), max(16, int(info["H"] * s))
    im = Image.new("L", (W, H), 255); d = ImageDraw.Draw(im)
    def px(p):
        u, vv = U.world_to_px(info, float(p[0]), float(p[1])); return (u * s, vv * s)
    for p in v["polys_beyond"]:
        if len(p) > 1:
            d.line([px(q) for q in p] + [px(p[0])], fill=120, width=1)
    for sg in v["segs_beyond"]:
        d.line([px(sg[0]), px(sg[1])], fill=120, width=1)
    for p in v["polys_cut"]:
        if len(p) > 1:
            d.line([px(q) for q in p] + [px(p[0])], fill=0, width=2)
    for sg in v["segs_cut"]:
        d.line([px(sg[0]), px(sg[1])], fill=0, width=2)
    arr = np.asarray(im)
    return arr < 200, im, info["mpp"] / s


def _ink_from_underlay(info, size):
    im = Image.open(info["png"]).convert("L").resize(size, Image.LANCZOS)
    g = np.asarray(im).astype(np.int32)
    return g < 128, im


def analyse(spec, tag):
    od = out(spec, "overlays", tag)
    items = GIO.load_all(spec)
    if not items:
        raise SystemExit("[overlay] no Stage 01 geometry: run `pipeline.run blueprint` first")
    ovs = spec.get("overlays") or U.list_underlays(spec)
    ovs = [(o["underlay"], o) if isinstance(o, dict) else (o, {}) for o in ovs]
    max_px = spec["_profile"]["overlay_px"]
    rows, report = [], {}
    for name, c in ovs:
        info = U.load(spec, name)
        tol = float(c.get("tol_m", 0.10)); expect_tol = c.get("expect_tol_m")
        model_edges, model_im, mpp = draw_model(spec, info, items, c, max_px)
        H, W = model_edges.shape
        model_im.save(os.path.join(od, f"{name}_model.png"))
        ink, under = _ink_from_underlay(info, (W, H))
        if c.get("radius_m"):  # analysis window: ignore drawing ink (frame, title block, clipped context) beyond radius_m of the axis
            uu, vv = np.meshgrid(np.arange(W), np.arange(H))
            a = (uu / (W / info["W"]) - info["u0"]) * info["mpp"]; b = (info["v0"] - vv / (H / info["H"])) * info["mpp"]
            ink = ink & (np.hypot(a, b) <= float(c["radius_m"]))
        d_ink = edt(ink) * mpp
        dm = d_ink[model_edges]
        band = np.asarray(Image.fromarray(model_edges.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(15))) > 0
        d_model = edt(model_edges) * mpp
        di = d_model[ink & band]
        res = dict(underlay=name, era=info["era"], kind=info["kind"], tol_m=tol, expect_tol_m=expect_tol, mpp=round(mpp, 4),
                   n_model_px=int(model_edges.sum()), n_ink_px_in_band=int((ink & band).sum()))
        m2d = d2m = None
        if len(dm):
            m2d = dict(p50=round(float(np.percentile(dm, 50)), 3), p90=round(float(np.percentile(dm, 90)), 3),
                       p95=round(float(np.percentile(dm, 95)), 3), max=round(float(dm.max()), 3), within_tol=round(float((dm <= tol).mean()), 3))
            res["model_to_drawing"] = m2d
        if len(di):
            d2m = dict(p50=round(float(np.percentile(di, 50)), 3), p90=round(float(np.percentile(di, 90)), 3), within_tol=round(float((di <= tol).mean()), 3))
            res["drawing_to_model"] = d2m
        ok = bool(m2d) and bool(d2m) and d2m["p90"] <= tol
        res["ok"] = True if expect_tol is not None else ok
        res["note"] = (f"other era: offsets up to {expect_tol} m expected, reported only" if expect_tol is not None
                       else ("" if ok else ("no model linework" if not m2d else ("no drawn ink near the model" if not d2m else "drawn lines without model geometry within tol"))))
        if ok and m2d and m2d["within_tol"] < 0.5:
            res["note"] = f"OK but only {m2d['within_tol']:.0%} of model lines sit on drawn ink: check the composite for extra geometry"
        base = Image.merge("RGB", [under.point(lambda v: 110 + v * 145 // 255)] * 3)
        red = Image.new("RGB", base.size, (220, 30, 30))
        mask = Image.fromarray((model_edges * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3))
        comp = Image.composite(red, base, mask)
        dr = ImageDraw.Draw(comp); fnt = _font(max(14, W // 70))
        txt = (f"{name} | {info['era']} | model->drawing p50 {m2d['p50'] if m2d else '-'} p90 {m2d['p90'] if m2d else '-'} m | "
               f"drawing->model p90 {d2m['p90'] if d2m else '-'} m | tol {tol} m | {'OK' if res['ok'] else 'FAIL'} {res['note']}")
        dr.rectangle([0, 0, W, fnt.size + 10], fill=(255, 255, 255)); dr.text((6, 4), txt, fill=(20, 20, 20), font=fnt)
        cp = os.path.join(od, f"{name}_overlay.png"); comp.save(cp); res["composite"] = cp
        report[name] = res
        rows.append((under.convert("RGB"), model_im.convert("RGB"), comp, txt))
        print(f"[overlay] {name}: {txt.split('|', 2)[2].strip()}")
    with open(os.path.join(od, "report.json"), "w") as f:
        json.dump(report, f, indent=1)
    if rows:
        tw = 700; tiles = []
        for under, model, comp, txt in rows:
            h = int(tw * under.height / under.width)
            tiles.append(([under.resize((tw, h)), model.resize((tw, h)), comp.resize((tw, h))], txt, h))
        hdr = 26; fnt = _font(16)
        sheet = Image.new("RGB", (3 * tw, sum(t[2] + hdr for t in tiles)), (11, 26, 46))
        d = ImageDraw.Draw(sheet); y = 0
        for ims, txt, h in tiles:
            d.text((6, y + 5), txt[:230], fill=(255, 207, 110), font=fnt); y += hdr
            for i, im in enumerate(ims):
                sheet.paste(im, (i * tw, y))
            y += h
        mp_ = out(spec, f"overlay_matrix_{tag}.png"); sheet.save(mp_)
        print(f"[overlay] matrix -> {os.path.relpath(mp_, spec['_root'])}")
    fails = [r for r in report.values() if not r["ok"]]
    return report, fails


def side_by_side(spec, tag):
    """For spec cameras that carry "underlay": the Cycles render next to the underlay resampled to the same world window
    (the section cutaway beside the as-built section drawing at the same scale)."""
    rd = out(spec, "renders", tag)
    outs = []
    for c in spec["cameras"]:
        if not c.get("underlay"):
            continue
        rp = os.path.join(rd, f"{c['name']}.png")
        if not os.path.exists(rp):
            continue
        info = U.load(spec, c["underlay"]); ren = Image.open(rp).convert("RGB"); W, H = ren.size
        os_ = float(c.get("ortho_scale", 60)); mpp = os_ / W
        if c.get("class") != "ortho":
            print(f"[overlay] {c['name']}: side-by-side needs an ortho camera"); continue
        # world window of the render: centred on the camera location, width ortho_scale, height ortho_scale*H/W
        loc = c["loc"]
        if info["kind"] == "section":
            a_c, b_c = loc[0] if abs(c["target"][1] - loc[1]) >= abs(c["target"][0] - loc[0]) else loc[1], loc[2]
        else:
            a_c, b_c = loc[0], loc[1]
        a0, a1 = a_c - os_ / 2, a_c + os_ / 2; b0, b1 = b_c - os_ * H / W / 2, b_c + os_ * H / W / 2
        u0, v0 = U.world_to_px(info, a0, b1); u1, v1 = U.world_to_px(info, a1, b0)
        crop = Image.open(info["png"]).convert("RGB").crop((int(u0), int(v0), int(u1), int(v1))).resize((W, H), Image.LANCZOS)
        sheet = Image.new("RGB", (2 * W + 8, H + 30), (11, 26, 46)); sheet.paste(ren, (0, 30)); sheet.paste(crop, (W + 8, 30))
        d = ImageDraw.Draw(sheet); f = _font(16)
        d.text((6, 6), f"{c['name']}  |  {info['name']} ({info['era']})  |  same world window {os_:.1f} m wide, {mpp:.3f} m/px", fill=(255, 207, 110), font=f)
        p = os.path.join(out(spec, "overlays", tag), f"{c['name']}_vs_{info['name']}.png"); sheet.save(p); outs.append(p)
        print(f"[overlay] side-by-side -> {os.path.relpath(p, spec['_root'])}")
    return outs


if __name__ == "__main__":
    spec = load_spec(); a = sys.argv[1:]
    n = int(a[a.index("--pass") + 1]) if "--pass" in a else 0
    tag = f"P{n:02d}" + ("_FINAL" if "--final" in a else "")
    if "--side-by-side" in a:
        side_by_side(spec, tag); sys.exit(0)
    report, fails = analyse(spec, tag)
    sys.exit(1 if fails else 0)
