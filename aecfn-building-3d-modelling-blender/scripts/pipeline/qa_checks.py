"""Automated QA gate — runs inside Blender before every critic pass. Numbers first, eyes second.
"$BLENDER" -b build/scene.blend -P pipeline/qa_checks.py -- --pass 7
Writes build/qa/P07.json; exit 1 if any P0/P1 failure. The measured gate (pipeline.measure) runs separately on the
Stage 01 geometry; its failures are P0 and are merged into the same report when build/measure/<tag>.json exists."""
import argparse, fnmatch, json, os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from pipeline.aep_spec import load_spec, out, datum, scene_path  # noqa: E402
from pipeline.aep_blender import NAME_RE  # noqa: E402
import bpy, bmesh  # noqa: E402
from mathutils import Vector  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
ap = argparse.ArgumentParser(); ap.add_argument("--pass", dest="pass_no", type=int, required=True)
a, _ = ap.parse_known_args(argv)
spec = load_spec()
if not bpy.data.filepath:
    bpy.ops.wm.open_mainfile(filepath=scene_path(spec))
F = []


def f(sev, check, obj, msg):
    F.append(dict(severity=sev, check=check, object=obj, msg=msg))


def wbbox(objs):
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    return [min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]


def matches(name, pat):
    pats = pat if isinstance(pat, list) else [pat]
    return any(fnmatch.fnmatch(name, p) or name.startswith(p) for p in pats)


SKIP = ("Section_", "Global_Atmosphere", "Light_Gobo", "Ground_", "REF_")
meshes = [o for o in bpy.data.objects if o.type == "MESH" and not o.name.startswith(SKIP)]
for o in bpy.data.objects:
    if not o.name.startswith("Section_") and not NAME_RE.match(o.name):
        f("P3", "naming", o.name, "name violates convention")
zg = datum(spec, "Z_GROUND") if "Z_GROUND" in spec["datums"] else 0.0
below_ok = spec.get("below_ground_ok", [])          # globs of objects allowed below Z_GROUND (basements, foot nodes, pits)
below_limit = float(spec.get("below_ground_limit_m", 0.05))
seen_data, tris_unique, tris_placed = set(), 0, 0
for o in meshes:
    me = o.data
    if not me.polygons:
        f("P1", "empty_mesh", o.name, "no faces"); continue
    t = sum(len(p.vertices) - 2 for p in me.polygons)
    tris_placed += t
    first = me.name not in seen_data
    if first:
        seen_data.add(me.name); tris_unique += t
        bm = bmesh.new(); bm.from_mesh(me)
        nm = sum(1 for e in bm.edges if not e.is_manifold); bm.free()
        if nm and not o.name.startswith(("Botanical_", "Prop_")) and not spec.get("allow_open_meshes", False):
            f("P2", "non_manifold", o.name, f"{nm} non-manifold edges")
        if not o.material_slots or not any(s.material for s in o.material_slots):
            f("P2", "no_material", o.name, "no material assigned")
        uses_img = any(s.material and any(n.bl_idname == "ShaderNodeTexImage" for n in s.material.node_tree.nodes) for s in o.material_slots)
        if uses_img and "UVMap_Metric_1m" not in me.uv_layers:
            f("P1", "uv_missing", o.name, "image textures but no UVMap_Metric_1m")
    lo, hi = wbbox([o])
    if lo[2] < zg - below_limit and not o.name.startswith("Botanical_") and not matches(o.name, below_ok):
        f("P1", "below_ground", o.name, f"zmin {lo[2]:.2f} < Z_GROUND {zg:.2f} - {below_limit}")
for img in bpy.data.images:
    if img.source == "FILE" and not os.path.exists(bpy.path.abspath(img.filepath)):
        f("P1", "missing_image", img.name, img.filepath)
for c in spec.get("datum_checks", []):  # {"match":"D3_STR_Slab_L17*","edge":"zmax","datum":"SSL_17","tol":0.01}
    objs = [o for o in meshes if matches(o.name, c["match"])]
    if not objs:
        f("P1", "datum_check", str(c["match"]), "no objects match"); continue
    lo, hi = wbbox(objs); val = hi[2] if c["edge"] == "zmax" else lo[2]
    want = datum(spec, c["datum"])
    if abs(val - want) > c.get("tol", 0.05):
        f("P0", "datum_check", str(c["match"]), f"{c['edge']} {val:.3f}m != {c['datum']} {want:.3f}m")
for c in spec.get("dim_checks", []):
    objs = [o for o in meshes if matches(o.name, c["match"])]
    if objs:
        lo, hi = wbbox(objs); i = "xyz".index(c["axis"]); ext = hi[i] - lo[i]
        if abs(ext - c["extent"]) > c.get("tol", 0.1):
            f("P0", "dim_check", str(c["match"]), f"{c['axis']} extent {ext:.3f}m != {c['extent']:.3f}m")
for cam in [o for o in bpy.data.objects if o.type == "CAMERA"]:
    cd = cam.data
    if cd.sensor_fit != "HORIZONTAL" or abs(cd.sensor_width - 36.0) > 1e-3:
        f("P1", "camera_sensor", cam.name, "must be HORIZONTAL 36 mm")
    if cd.type != "ORTHO" and not (cd.dof.use_dof and cd.dof.focus_object):
        f("P2", "camera_dof", cam.name, "DoF off or no Focus_Target")
    if cd.type == "PERSP" and cd.clip_end < float(spec.get("clip_end_min_m", 1000)):
        f("P2", "camera_clip", cam.name, f"clip_end {cd.clip_end:.0f} < {spec.get('clip_end_min_m', 1000)} m")
ref = bpy.data.collections.get("REF_Drawings")
if ref and not ref.hide_render:
    f("P1", "ref_drawings_visible", "REF_Drawings", "underlay planes must never render")
budget = int(spec.get("tri_budget") or spec["_profile"]["tri_budget"])
if tris_unique > budget:
    f("P1", "tri_budget", "*", f"{tris_unique:,} unique tris > budget {budget:,} (placed incl. instances: {tris_placed:,})")
# merge the measured gate (Stage 01 numbers) if it ran for this pass
mp = out(spec, "measure", f"P{a.pass_no:02d}.json")
measured = None
if os.path.exists(mp):
    measured = json.load(open(mp))
    for r in measured["results"]:
        if not r["ok"]:
            f("P0", "measure", r["id"], r.get("msg") or f"value {r.get('value')} expect {r.get('expect')} err {r.get('err', r.get('max_err'))} tol {r['tol']}")
rep = dict(pass_no=a.pass_no, tris_unique=tris_unique, tris_placed=tris_placed, objects=len(meshes), findings=F,
           blocking=[x for x in F if x["severity"] in ("P0", "P1")], measure=(dict(n=measured["n"], failed=measured["failed"]) if measured else None))
with open(out(spec, "qa", f"P{a.pass_no:02d}.json"), "w") as fh:
    json.dump(rep, fh, indent=1)
print(f"[QA] P{a.pass_no:02d}: {len(F)} findings, {len(rep['blocking'])} blocking, {len(meshes):,} objects, {tris_unique:,} unique / {tris_placed:,} placed tris")
sys.exit(1 if rep["blocking"] else 0)
