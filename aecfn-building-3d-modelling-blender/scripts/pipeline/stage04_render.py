"""Render step. Opens build/scene.blend and renders a pass: Cycles for the spec cameras, Workbench line drawings
for the Cam_9x_Overlay_* cameras when --overlays is given (visual aid; the overlay GATE uses pipeline.overlay on Stage 01 linework).
"$BLENDER" -b build/scene.blend -P pipeline/stage04_render.py -- --pass 7 [--final] [--cams Cam_01,Cam_06] [--zone <zone>] [--overlays|--overlays-only]
Output: build/renders/P07/<cam>.png + _paths.json ; build/overlays/P07/<overlay cam>.png + _overlay_meta.json
"""
import argparse, json, os, sys, time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from pipeline.aep_spec import load_spec, out, scene_path  # noqa: E402
from pipeline import aep_blender as B  # noqa: E402
import bpy  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
ap = argparse.ArgumentParser(); ap.add_argument("--pass", dest="pass_no", type=int, required=True)
ap.add_argument("--final", action="store_true"); ap.add_argument("--cams", default=""); ap.add_argument("--zone", default="")
ap.add_argument("--overlays-only", action="store_true"); ap.add_argument("--overlays", action="store_true")
a = ap.parse_args(argv)

spec = load_spec(); prof = spec["_profile"]
if not bpy.data.filepath:
    bpy.ops.wm.open_mainfile(filepath=scene_path(spec))
scene = bpy.context.scene
B.setup_render(scene, prof, final=a.final)
box = bpy.data.objects.get("Global_Atmosphere_Volume_Box")
if box:
    box.hide_render = not (a.final or prof["volume_in_review"])
all_cams = sorted([o for o in bpy.data.objects if o.type == "CAMERA"], key=lambda o: o.name)
ov_cams = [c for c in all_cams if "_Overlay_" in c.name]
cams = [c for c in all_cams if c not in ov_cams]
flagged = [c["name"] for c in spec["cameras"] if c.get("review")]
want = [c for c in a.cams.split(",") if c] or ([] if a.final or prof["review_cams"] == "all" else flagged)
if want:
    cams = [c for c in cams if any(c.name.startswith(w) for w in want)]
    ov_cams = [c for c in ov_cams if any(c.name.startswith(w) or w in c.name for w in want)] if any("Overlay" in w for w in want) else ov_cams
if a.zone:
    if a.zone not in spec["zones"]:
        raise SystemExit(f"[render] unknown zone {a.zone!r}; spec zones: {', '.join(spec['zones'])}")
    keep = {a.zone, *spec["zones"][a.zone].get("depends_on", [])}
    targets = {z.title().replace("_", "") for z in keep}
    for c in bpy.data.collections:
        if c.name[:2].isdigit() and not c.name.startswith(("00_", "90_")):
            c.hide_render = c.name.split("_", 1)[1] not in targets
scene_tag = os.path.splitext(os.environ.get("AEP_SCENE", "scene.blend"))[0]
private = "" if scene_tag == "scene" or a.zone else "_" + scene_tag
tag = f"P{a.pass_no:02d}" + (f"_{a.zone}" if a.zone else "") + private + ("_FINAL" if a.final else "")
t = time.time()
with B.render_lock(spec["_root"]):
    paths = []
    if not a.overlays_only:
        odir = out(spec, "renders", tag)
        paths = B.render_cameras(scene, cams, odir, a.pass_no, {c["name"]: dict(c, _construction_only=spec.get("construction_only", [])) for c in spec["cameras"]})
        pj = os.path.join(odir, "_paths.json")
        prev = json.load(open(pj)) if os.path.exists(pj) else {}
        merged = sorted(set(prev.get("paths", [])) | set(paths)) if want else paths  # partial re-renders merge, never clobber
        with open(pj, "w") as fh:
            json.dump(dict(tag=tag, pass_no=a.pass_no, paths=merged, seconds=round(time.time() - t, 1)), fh)
        print(f"[render] {len(paths)} cams in {time.time()-t:.1f}s -> {odir}")
    if ov_cams and (a.overlays or a.overlays_only) and not a.final:  # optional Workbench drawings (visual aid only)
        t2 = time.time()
        od = out(spec, "overlays", tag)
        B.render_overlay_cameras(scene, ov_cams, od, max_px=prof["overlay_px"])
        print(f"[render] {len(ov_cams)} overlay drawings in {time.time()-t2:.1f}s -> {od}")
