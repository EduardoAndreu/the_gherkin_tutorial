"""Stage 01 — compile the generators (CSG solids + parametric meshes + instances) and the N-panel CAD blueprint.
Run with system Python:  python3 -m pipeline.stage01_blueprint [--zone <zone>] [--no-sheet]
Outputs: build/{solids,meshes,instances}/<zone>/*.npz, build/geom/manifest.json, build/blueprint/<discipline>.ifc (spec ifc_export.files),
         blueprint.dxf, blueprint.svg, blueprint.png (one panel per spec["sheets"] entry, underlay backdrops at true scale)
"""
import json, os, shutil, sys, time
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from pipeline.aep_spec import load_spec, zone_module, out, datums_matching, zone_order  # noqa: E402
from pipeline import geom_io as GIO, linework as LW  # noqa: E402


def _swap_in(final_dir, tmp_dir):
    old = final_dir + f".old{os.getpid()}"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(final_dir):
        os.replace(final_dir, old)
    os.replace(tmp_dir, final_dir); shutil.rmtree(old, ignore_errors=True)


def _norm_entry(name, entry, kind):
    """meshes()/instances() entries may be a bare (verts, faces) or a dict(mesh=..., material=, uv=, imprint=, transforms=)."""
    if isinstance(entry, dict):
        mesh = entry.get("mesh")
        meta = {k: v for k, v in entry.items() if k not in ("mesh", "transforms")}
        tr = entry.get("transforms")
    else:
        mesh, meta, tr = entry, {}, None
    if mesh is None or len(mesh) != 2:
        raise SystemExit(f"[Stage01] {kind} {name}: expected (verts, faces) or dict(mesh=(verts, faces), ...)")
    if kind == "instances" and tr is None:
        raise SystemExit(f"[Stage01] instance {name}: missing transforms")
    return mesh, meta, tr


def compile_geometry(spec, only=None):
    """Run every zone's generators -> npz + manifest. --zone runs one zone (its npz dirs are replaced atomically)."""
    import manifold3d as m3
    manifest = GIO.load_manifest(spec) if only else {}
    if only:
        manifest = {k: v for k, v in manifest.items() if v["zone"] != only}
    for zone in zone_order(spec):
        if only and zone != only:
            continue
        z = spec["zones"][zone]
        mod = zone_module(spec, zone)
        ox, oy, oz = z.get("origin", [0, 0, 0]); off = np.array([ox, oy, oz], float)
        dirs = {}
        for kind in ("solids", "meshes", "instances"):
            final = out(spec, kind, zone); tmp = final + f".tmp{os.getpid()}"
            shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp); dirs[kind] = (final, tmp)
        n = 0
        # ---- CSG solids
        for name, man in (mod.solids(spec) if hasattr(mod, "solids") else {}).items():
            _check_name(name, zone)
            man = man.translate([ox, oy, oz])
            st = man.status()
            if st != m3.Error.NoError or man.is_empty() or man.volume() <= 0:
                raise SystemExit(f"[Stage01] invalid solid {zone}/{name}: status={st} volume={man.volume()}")
            mesh = man.to_mesh()
            V = np.asarray(mesh.vert_properties)[:, :3].astype(np.float64)
            T = np.asarray(mesh.tri_verts).astype(np.int64)
            np.savez(os.path.join(dirs["solids"][1], f"{name}.npz"), verts=V, tris=T)
            bb = man.bounding_box()
            manifest[name] = dict(zone=zone, kind="solids", owner=z["owner"], bbox=[list(bb[:3]), list(bb[3:])], tris=int(len(T)), instances=1)
            n += 1
        # ---- parametric meshes
        for name, entry in (mod.meshes(spec) if hasattr(mod, "meshes") else {}).items():
            _check_name(name, zone)
            (V, F), meta, _ = _norm_entry(name, entry, "meshes")
            V = GIO.validate_mesh(name, V, F) + off
            GIO.save_mesh(os.path.join(dirs["meshes"][1], f"{name}.npz"), V, F)
            manifest[name] = dict(zone=zone, kind="meshes", owner=z["owner"], bbox=[V.min(0).tolist(), V.max(0).tolist()],
                                  tris=int(sum(len(f) - 2 for f in F)), instances=1, **meta)
            n += 1
        # ---- instances (prototype local + transforms)
        for name, entry in (mod.instances(spec) if hasattr(mod, "instances") else {}).items():
            _check_name(name, zone)
            (V, F), meta, tr = _norm_entry(name, entry, "instances")
            V = GIO.validate_mesh(name, V, F)
            M = np.asarray(tr, np.float64).reshape(-1, 4, 4).copy()
            if not np.isfinite(M).all():
                raise SystemExit(f"[Stage01] instance {name}: non-finite transform")
            M[:, :3, 3] += off
            GIO.save_mesh(os.path.join(dirs["instances"][1], f"{name}.npz"), V, F, transforms=M)
            it = dict(verts=V, tris=GIO.triangulate(F), transforms=M)
            lo, hi = GIO.world_bbox(it)
            manifest[name] = dict(zone=zone, kind="instances", owner=z["owner"], bbox=[lo, hi],
                                  tris=int(sum(len(f) - 2 for f in F)), instances=int(len(M)), **meta)
            n += 1
        for kind, (final, tmp) in dirs.items():
            _swap_in(final, tmp)
        print(f"[Stage01] zone {zone}: {n} generator outputs")
    mp = GIO.manifest_path(spec)
    lock = open(mp + ".lock", "w")
    try:
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX)   # parallel specialists: merge under a lock, never clobber another zone
    except ImportError:
        pass
    if only:
        cur = GIO.load_manifest(spec)
        manifest = {**{k: v for k, v in cur.items() if v["zone"] != only}, **{k: v for k, v in manifest.items() if v["zone"] == only}}
    tmp = f"{mp}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(manifest, f, indent=1)
    os.replace(tmp, mp); lock.close()
    return manifest


def _check_name(name, zone):
    import re
    if not re.match(r"^D\d_[A-Za-z0-9]+_[A-Za-z0-9_]+$", name):
        raise SystemExit(f"[Stage01] {zone}: object name {name!r} violates D<n>_<Zone>_<Part>[_Variant]")


# ---------- IFC4: pipeline/ifc_export.py (one file per discipline, styled materials) ----------


# ---------- drawings ----------
def _solid_union(items):
    import manifold3d as m3
    mans = []
    for it in items:
        if it["kind"] != "solids":
            continue
        mans.append(m3.Manifold(m3.Mesh(vert_properties=it["verts"].astype(np.float32), tri_verts=it["tris"].astype(np.uint32))))
    return m3.Manifold.batch_boolean(mans, m3.OpType.Add) if mans else None


def _mesh_arrays(items):
    """Every non-solid item as world (V, T) arrays (instances expanded)."""
    for it in items:
        if it["kind"] == "solids":
            continue
        yield (GIO.expand_instances(it) if it["kind"] == "instances" else (it["verts"], it["tris"]))


def view_for(spec, sheet, allm, meshes, ang=45.0):
    """One drawing view. sheet: {"id","kind":plan|section|elevation, "z"|"axis"+"value"|"azimuth_deg"/"psi_deg",
    "title","underlay","depth"?} depth (m) limits the BEYOND linework to a slab of that thickness behind the cut."""
    from pipeline import underlays as U
    sh = sheet; kind = sh["kind"]; depth = sh.get("depth")
    v = dict(id=sh["id"], kind=kind, title="", polys_cut=[], polys_beyond=[], polys_above=[], segs_cut=[], segs_beyond=[], segs_above=[], underlay=None)
    if sh.get("underlay"):
        v["underlay"] = U.load(spec, sh["underlay"])
    if kind == "plan":
        z = float(sh["z"]); v["zcut"] = z; v["labels"] = ("X", "Y")
        v["title"] = f"{sh['id']} {sh.get('title', 'PLAN')} (CUT Z = {z:+.2f} m)"
        if allm is not None:
            v["polys_cut"] = allm.slice(z).to_polygons()
            below = allm.trim_by_plane([0, 0, -1], -z)
            if depth:
                below = below.trim_by_plane([0, 0, 1], z - depth)
            v["polys_beyond"] = below.project().to_polygons()
            if not depth:
                v["polys_above"] = allm.trim_by_plane([0, 0, 1], z).project().to_polygons()
        for V, T in meshes:
            v["segs_cut"].append(LW.project(LW.cut_segments(V, T, 2, z), "plan"))
            E = LW.feature_edges(V, T, ang); S = V[E]
            mid = S[:, :, 2].mean(1) if len(S) else np.zeros(0)
            lo = (z - depth) if depth else -1e9
            v["segs_beyond"].append(LW.project(S[(mid < z) & (mid >= lo)], "plan"))
            if not depth:
                v["segs_above"].append(LW.project(S[mid >= z], "plan"))
    elif kind == "section":
        ax = sh["axis"]; val = float(sh["value"]); v["zcut"] = None
        v["title"] = f"SECTION {sh['id']} {sh.get('title', '')} (CUT {ax.upper()} = {val:+.2f} m)"
        v["labels"] = ("X", "Z") if ax == "y" else ("Y", "Z")
        pk = "section_y" if ax == "y" else "section_x"
        n = [0, 1, 0] if ax == "y" else [1, 0, 0]
        if allm is not None:
            far = allm.trim_by_plane(n, val)
            if depth:
                far = far.trim_by_plane([-c for c in n], -(val + depth))
            if ax == "y":
                v["polys_cut"] = [np.c_[p[:, 0], -p[:, 1]] for p in allm.rotate([90, 0, 0]).slice(val).to_polygons()]
                v["polys_beyond"] = [np.c_[p[:, 0], -p[:, 1]] for p in far.rotate([90, 0, 0]).project().to_polygons()]
            else:
                v["polys_cut"] = [np.c_[p[:, 1], -p[:, 0]] for p in allm.rotate([0, -90, 0]).slice(val).to_polygons()]
                v["polys_beyond"] = [np.c_[p[:, 1], -p[:, 0]] for p in far.rotate([0, -90, 0]).project().to_polygons()]
        axi = 1 if ax == "y" else 0
        for V, T in meshes:
            v["segs_cut"].append(LW.project(LW.cut_segments(V, T, axi, val), pk))
            E = LW.feature_edges(V, T, ang); S = V[E]
            mid = S[:, :, axi].mean(1) if len(S) else np.zeros(0)
            hi = (val + depth) if depth else 1e9
            v["segs_beyond"].append(LW.project(S[(mid > val) & (mid <= hi)], pk))
    elif kind == "elevation":
        right = LW.elevation_right(sh.get("azimuth_deg"), sh.get("psi_deg")); v["zcut"] = None
        v["title"] = f"ELEVATION {sh['id']} {sh.get('title', '')}"; v["labels"] = ("u", "Z")
        if allm is not None:
            import math
            a = math.degrees(math.atan2(right[1], right[0]))
            r = allm.rotate([0, 0, -a]).rotate([90, 0, 0])
            v["polys_beyond"] = [np.c_[p[:, 0], -p[:, 1]] for p in r.project().to_polygons()]
        for V, T in meshes:
            E = LW.feature_edges(V, T, ang); S = V[E]
            v["segs_beyond"].append(LW.project(S, "elevation", right=right))
    else:
        raise SystemExit(f"sheet {sh['id']}: unknown kind {kind!r} (plan|section|elevation)")
    for k in ("segs_cut", "segs_beyond", "segs_above"):
        v[k] = np.concatenate(v[k]) if v[k] else np.zeros((0, 2, 2))
    v["extent"] = sh.get("extent")  # optional [a0, a1, b0, b1] drawing window (e.g. an elevation without the City context)
    return v


def panel_views(spec, items, sheets=None):
    allm = _solid_union(items)
    meshes = list(_mesh_arrays(items))
    ang = float(spec.get("sheet_edge_angle_deg", 45.0))  # feature-edge threshold for mesh linework (round tubes stay clean)
    return [view_for(spec, sh, allm, meshes, ang) for sh in (sheets if sheets is not None else spec["sheets"])]


def write_dxf(spec, views):
    import ezdxf
    doc = ezdxf.new("R2010", setup=True)
    for lay, col in [("CUT", 1), ("BEYOND", 8), ("ABOVE", 5), ("DATUM", 3), ("ANNO", 7), ("MESH_CUT", 2), ("MESH_BEYOND", 9)]:
        doc.layers.add(lay, color=col)
    msp = doc.modelspace()
    pitch = float(spec.get("sheet_pitch_m", 250.0))
    for n, v in enumerate(views):
        dx, dy = (n % 3) * pitch, -(n // 3) * pitch
        for p in v["polys_beyond"]:
            msp.add_lwpolyline([(x + dx, y + dy) for x, y in p], close=True, dxfattribs={"layer": "BEYOND"})
        for p in v["polys_above"]:
            msp.add_lwpolyline([(x + dx, y + dy) for x, y in p], close=True, dxfattribs={"layer": "ABOVE", "linetype": "DASHED"})
        if v["polys_cut"]:
            h = msp.add_hatch(dxfattribs={"layer": "CUT"}); h.set_pattern_fill("ANSI31", scale=0.05)
            for p in v["polys_cut"]:
                pts = [(x + dx, y + dy) for x, y in p]
                h.paths.add_polyline_path(pts, is_closed=True); msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "CUT"})
        for key, lay in (("segs_cut", "MESH_CUT"), ("segs_beyond", "MESH_BEYOND"), ("segs_above", "ABOVE")):
            for s in v[key]:
                msp.add_line((s[0][0] + dx, s[0][1] + dy), (s[1][0] + dx, s[1][1] + dy), dxfattribs={"layer": lay})
        if v["labels"][1] == "Z":
            for dn, dz in datums_matching(spec, spec.get("sheet_datums", "*")):
                msp.add_line((dx - 60, dz + dy), (dx + 60, dz + dy), dxfattribs={"layer": "DATUM"})
                msp.add_text(f"{dn} {dz:+.2f}m", height=0.5, dxfattribs={"layer": "DATUM"}).set_placement((dx - 60, dz + dy + 0.1))
        msp.add_text(v["title"], height=1.2, dxfattribs={"layer": "ANNO"}).set_placement((dx - 60, dy + pitch * 0.45))
    doc.saveas(out(spec, "blueprint", "blueprint.dxf"))


def write_sheet(spec, views):
    import math
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.path import Path
    from matplotlib.patches import PathPatch
    from PIL import Image
    bg, ink, cutc, dat, ul = "#0b1a2e", "#cfe3ff", "#9fd0ff", "#ffcf6e", "#ff7a59"
    plt.rcParams.update({"font.family": "monospace", "hatch.color": cutc, "hatch.linewidth": 0.6})
    n = len(views); cols = 2 if n <= 4 else 3; rows = math.ceil(n / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(10 * cols, 5.6 * rows), facecolor=bg, squeeze=False)
    for ax in axes.flat[n:]:
        ax.axis("off")
    for ax, v in zip(axes.flat, views):
        ax.set_facecolor(bg)
        if v["underlay"]:  # calibrated raster at true scale under the model linework
            u = v["underlay"]; im = Image.open(u["png"]).convert("L")
            if im.width > 2600:
                im = im.resize((2600, int(im.height * 2600 / im.width)), Image.LANCZOS)
            g = np.asarray(im).astype(np.float32) / 255.0            # paper -> transparent, ink -> orange
            rgba = np.zeros(g.shape + (4,), np.float32); rgba[..., 0], rgba[..., 1], rgba[..., 2] = 1.0, 0.55, 0.3
            rgba[..., 3] = np.clip((1.0 - g) * 1.6, 0, 0.95)
            ax.imshow(rgba, extent=u["extent"], origin="upper", interpolation="bilinear", zorder=1)
        for polys, kw in [(v["polys_beyond"], dict(fc="none", ec=ink, lw=0.5, alpha=0.6)),
                          (v["polys_above"], dict(fc="none", ec=ink, lw=0.4, ls="--", alpha=0.5)),
                          (v["polys_cut"], dict(fc="#16314f", ec=cutc, lw=1.2, hatch="////", alpha=0.9))]:
            if not polys:
                continue
            verts, codes = [], []
            for p in polys:
                p = np.asarray(p)
                if len(p) < 3:
                    continue
                verts += p.tolist() + [p[0].tolist()]
                codes += [Path.MOVETO] + [Path.LINETO] * (len(p) - 1) + [Path.CLOSEPOLY]
            if verts:
                ax.add_patch(PathPatch(Path(verts, codes), zorder=2, **kw))
        for key, kw in (("segs_beyond", dict(colors=ink, linewidths=0.35, alpha=0.55)),
                        ("segs_above", dict(colors=ink, linewidths=0.3, alpha=0.4, linestyles="dashed")),
                        ("segs_cut", dict(colors=cutc, linewidths=1.0, alpha=0.95))):
            if len(v[key]):
                ax.add_collection(LineCollection(v[key], zorder=3, **kw))
        ax.autoscale_view(); ax.set_aspect("equal")
        if v["underlay"]:
            e = v["underlay"]["extent"]; ax.set_xlim(e[0], e[1]); ax.set_ylim(e[2], e[3])
        if v.get("extent"):
            e = v["extent"]; ax.set_xlim(e[0], e[1]); ax.set_ylim(e[2], e[3])
        if v["labels"][1] == "Z":
            (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
            ds = datums_matching(spec, spec.get("sheet_datums", "*"))
            step = max(1, math.ceil(len(ds) / 28))
            min_gap = (y1 - y0) / 32; last, col = -1e9, 0
            for k, (dn, dz) in enumerate(ds):
                if not (y0 <= dz <= y1):
                    continue
                ax.axhline(dz, color=dat, lw=0.4, ls="-.", alpha=0.55, zorder=4)
                if k % step:
                    continue
                col = col + 1 if dz - last < min_gap else 0
                if col == 0:
                    last = dz
                ax.text(x0 + (col % 4) * (x1 - x0) / 4.2, dz, f" {dn} {dz:+.2f}", color=dat, fontsize=5.5, va="bottom", zorder=5)
        if v.get("zcut") is not None and v["underlay"]:
            ax.text(0.01, 0.01, f"underlay: {v['underlay']['name']} ({v['underlay']['era']}) at Z={v['underlay']['z']:+.2f}", transform=ax.transAxes, color=ul, fontsize=6)
        elif v["underlay"]:
            ax.text(0.01, 0.01, f"underlay: {v['underlay']['name']} ({v['underlay']['era']})", transform=ax.transAxes, color=ul, fontsize=6)
        ax.grid(color=ink, alpha=0.08); ax.tick_params(colors=ink, labelsize=6)
        for s in ax.spines.values():
            s.set_color(ink); s.set_alpha(0.3)
        ax.set_title(v["title"], color=dat, fontsize=9, loc="left")
        ax.set_xlabel(v["labels"][0] + " (m)", color=ink, fontsize=6); ax.set_ylabel(v["labels"][1] + " (m)", color=ink, fontsize=6)
    fig.suptitle(f"MEASURED-BRIEF MASTER BLUEPRINT — {spec.get('title', spec['project']).upper()}\n"
                 f"CSG SOLIDS (MANIFOLD3D + IFC4) · PARAMETRIC MESHES + INSTANCES (feature edges) · ANSI31 CUT HATCH · UNITS: METRES"
                 + (f" · source spec sha {spec['_src_sha']}" if spec.get("_src_sha") else ""),
                 color=ink, fontsize=11)
    for ext in ("png", "svg"):
        fig.savefig(out(spec, "blueprint", f"blueprint.{ext}"), facecolor=bg, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    t = time.time()
    spec = load_spec()
    only = sys.argv[sys.argv.index("--zone") + 1] if "--zone" in sys.argv else None
    manifest = compile_geometry(spec, only)
    if only:
        print(f"[Stage01] zone {only}: ok in {time.time()-t:.1f}s"); sys.exit(0)
    items = GIO.load_all(spec)
    if "--no-sheet" not in sys.argv:
        from pipeline.ifc_export import write_ifcs
        write_ifcs(spec, items)
        views = panel_views(spec, items)
        if views:
            write_dxf(spec, views); write_sheet(spec, views)
        else:
            print("[Stage01] no sheets defined in spec['sheets']; skipping DXF/PNG")
    tot_inst = sum(m["instances"] for m in manifest.values())
    print(f"[Stage01] {len(manifest)} named outputs ({tot_inst} placed objects), IFC+DXF+SVG+PNG written in {time.time()-t:.1f}s")
