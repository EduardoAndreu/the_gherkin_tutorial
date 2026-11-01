"""Stage 01c — compile Stage 01 geometry 1:1 into Blender 5.2: solids, parametric meshes, instances (linked duplicates),
underlay reference planes (hidden REF_Drawings), overlay cameras, materials, dressing, polish, rig, section cutaway.
Blender: "$BLENDER" -b --factory-startup -P pipeline/stage03_compile.py -- [--final] [--zone Z]
Writes build/scene.blend (AEP_SCENE overrides). Never hand-edit the .blend: every change goes through spec/, zones/ or pipeline/.
"""
import fnmatch, glob, importlib, json, os, sys, time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from pipeline.aep_spec import load_spec, zone_module, out, scene_path, datums_matching, zone_order  # noqa: E402
from pipeline import aep_blender as B  # noqa: E402
from pipeline import underlays as U  # noqa: E402
import bpy  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
FINAL = "--final" in argv
ONLY = argv[argv.index("--zone") + 1] if "--zone" in argv else None


def _mat_for(spec, name, meta, matmap, tex_dir, hooks, zone):
    key = meta.get("material") or next((v for k, v in matmap.items() if k in name), None)
    for h in hooks:
        key = h.classify(zone, name, key) if hasattr(h, "classify") else key
    if not key:
        return None, None
    m = spec["materials"].get(key, {})
    return key, B.pbr_material(key, tex_dir, tuple(m.get("fallback_rgb", (0.5, 0.5, 0.5))), m)


def main():
    t = time.time()
    spec = load_spec(); prof = spec["_profile"]
    manifest = json.load(open(out(spec, "geom", "manifest.json"))) if os.path.exists(out(spec, "geom", "manifest.json")) else {}
    scene = B.reset_scene()
    scene.unit_settings.system, scene.unit_settings.scale_length = "METRIC", 1.0
    B.setup_render(scene, prof, final=FINAL)
    scene.view_settings.exposure = spec.get("exposure", 0.0)
    sun = B.setup_world_and_sun(scene, spec["sun"])
    imprint_zs = [z for _, z in datums_matching(spec, spec.get("imprint", "*"))]
    tex_dir = os.path.join(spec["_root"], "textures")
    hooks = [importlib.import_module(m) for m in spec.get("material_hooks", [])]
    zone_colls, n_obj = [], 0
    for i, zone in enumerate(zone_order(spec), start=1):
        if ONLY and zone != ONLY and zone not in spec["zones"][ONLY].get("depends_on", []):
            continue  # private specialist scene: the zone + what it depends on
        z = spec["zones"][zone]
        coll = B.ensure_collection(f"{i:02d}_{zone.title().replace('_', '')}")
        zone_colls.append(coll)
        mod = zone_module(spec, zone)
        matmap = mod.materials(spec) if hasattr(mod, "materials") else {}
        for f in sorted(glob.glob(out(spec, "solids", zone) + "/*.npz")):
            name = os.path.splitext(os.path.basename(f))[0]
            ob = B.npz_to_object(f, name, coll); n_obj += 1
            meta = manifest.get(name, {})
            if meta.get("imprint", True):
                B.imprint_z_datums(ob, imprint_zs)
            B.metric_uv(ob, mode=meta.get("uv", "box"))
            key, mat = _mat_for(spec, name, meta, matmap, tex_dir, hooks, zone)
            if mat:
                ob.data.materials.append(mat)
            for h in hooks:
                if hasattr(h, "uv"):
                    h.uv(ob, key)
        for f in sorted(glob.glob(out(spec, "meshes", zone) + "/*.npz")):
            name = os.path.splitext(os.path.basename(f))[0]
            ob = B.npz_to_object(f, name, coll); n_obj += 1
            meta = manifest.get(name, {})
            if meta.get("imprint", True):
                B.imprint_z_datums(ob, imprint_zs)
            B.metric_uv(ob, mode=meta.get("uv", "box"))
            if meta.get("smooth"):
                for p in ob.data.polygons:
                    p.use_smooth = True
            key, mat = _mat_for(spec, name, meta, matmap, tex_dir, hooks, zone)
            if mat:
                ob.data.materials.append(mat)
            for h in hooks:
                if hasattr(h, "uv"):
                    h.uv(ob, key)
        for f in sorted(glob.glob(out(spec, "instances", zone) + "/*.npz")):
            name = os.path.splitext(os.path.basename(f))[0]
            obs = B.npz_to_instances(f, name, coll); n_obj += len(obs)
            meta = manifest.get(name, {})
            proto = obs[0]
            B.metric_uv(proto, world=False, mode=meta.get("uv", "axis"))  # shared datablock: object-space UVs
            if meta.get("smooth"):
                for p in proto.data.polygons:
                    p.use_smooth = True
            key, mat = _mat_for(spec, name, meta, matmap, tex_dir, hooks, zone)
            if mat:
                proto.data.materials.append(mat)
        if hasattr(mod, "dress"):
            mod.dress(dict(spec=spec, scene=scene, coll=coll, B=B, tex_dir=tex_dir, final=FINAL, zone=zone, origin=z.get("origin", [0, 0, 0])))
    for ob in bpy.data.objects:  # practical lights light the scene but never show as blobs in glass/stainless or to the camera
        if ob.type == "LIGHT" and ob.data.type != "SUN":
            ob.visible_glossy = False; ob.visible_camera = False
    for h in hooks:
        if hasattr(h, "post"):
            h.post(dict(spec=spec, scene=scene, colls=zone_colls, B=B, tex_dir=tex_dir, final=FINAL))
    # ---- ground
    ground = B.ensure_collection("00_Ground")
    if spec.get("ground_size", 200):
        bpy.ops.mesh.primitive_plane_add(size=spec.get("ground_size", 200))
        g = bpy.context.active_object; g.name = "Ground_Site_Plane"
        g.location.z = spec.get("ground_z", 0.0)
        for c in g.users_collection:
            c.objects.unlink(g)
        ground.objects.link(g); B.metric_uv(g)
        gk = spec.get("ground_material", "gravel")
        g.data.materials.append(B.pbr_material(gk, tex_dir, tuple(spec["materials"].get(gk, {}).get("fallback_rgb", (0.55, 0.53, 0.5))), spec["materials"].get(gk, {})))
        if spec.get("hills"):
            B.distant_hills(spec, ground, tex_dir)
    # ---- underlay reference planes + overlay cameras (hidden REF_Drawings collection)
    ref = B.ensure_collection(B.REF_COLL)
    rig = B.ensure_collection("90_Camera_Rig")
    ovs = spec.get("overlays")
    ovs = {(o["underlay"] if isinstance(o, dict) else o): (o if isinstance(o, dict) else {}) for o in ovs} if ovs else {n: {} for n in U.list_underlays(spec)}
    for nm in U.list_underlays(spec):
        info = U.load(spec, nm)
        B.underlay_plane(info, ref)
        if nm in ovs:
            B.overlay_camera(info, rig, cut_offset=float(ovs[nm].get("cut_offset_m", spec.get("overlay_cut_offset_m", 1.2))), depth=ovs[nm].get("depth_m"))
    ref.hide_render = True
    for vl in scene.view_layers:
        lc = next((c for c in vl.layer_collection.children if c.name == B.REF_COLL), None)
        if lc:
            lc.hide_viewport = True
    # ---- cameras, section cutaways, polish
    cams = B.build_camera_rig(spec, scene)
    poche = B.emission_free_material("MAT_Section_Poche", tuple(spec.get("poche_rgb", (0.55, 0.05, 0.03))), rough=0.9)
    for sh in spec["sheets"]:
        if sh["kind"] == "section" and sh.get("cutaway"):
            B.section_cutaway(sh["id"], dict(sh, _id=sh["id"]), zone_colls, poche, spec["datums"])
    B.apply_visual_strengths(spec, scene, sun, FINAL)
    B.horizon_disc(spec, B.ensure_collection("00_Ground"))
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=scene_path(spec))
    print(f"[Stage01c] compiled {n_obj} objects in {len(zone_colls)} zones in {time.time()-t:.1f}s -> {scene_path(spec)}")


main()
