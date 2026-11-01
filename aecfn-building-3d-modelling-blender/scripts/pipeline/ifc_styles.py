"""Inside Blender (compiled scene): object -> material map and one display colour per material for the IFC export.
Each material is rendered on a flat 4 x 4 m swatch under a uniform white sky (Cycles, Standard view transform), so the
average pixel is the colour the material shows in neutral light with every texture / mix / gain in its node tree applied.
Glass (no Principled BSDF) is flagged; its colour/transparency come from spec ifc_export.styles.
Writes build/geom/ifc_styles.json. Run: python3 -m pipeline.run ifc  (or blender -b scene.blend -P ifc_styles.py)
"""
import json, os, sys, tempfile
import bpy
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SKIP = ("Section_", "REF_", "OV_", "Ground_Horizon")


def swatch_scene():
    sc = bpy.data.scenes.new("IFC_SWATCH")
    sc.render.engine = "CYCLES"; sc.cycles.samples = 16; sc.cycles.device = "CPU"; sc.cycles.use_denoising = False
    sc.render.resolution_x = sc.render.resolution_y = 24; sc.render.resolution_percentage = 100
    sc.view_settings.view_transform = "Standard"; sc.view_settings.look = "None"; sc.view_settings.exposure = 0; sc.view_settings.gamma = 1
    sc.render.image_settings.file_format = "PNG"; sc.render.image_settings.color_depth = "16"
    w = bpy.data.worlds.new("IFC_SWATCH_W"); w.use_nodes = True
    bg = w.node_tree.nodes["Background"]; bg.inputs[0].default_value = (1, 1, 1, 1); bg.inputs[1].default_value = 1.0
    sc.world = w
    me = bpy.data.meshes.new("IFC_SWATCH_M")
    me.from_pydata([(-2, -2, 0), (2, -2, 0), (2, 2, 0), (-2, 2, 0)], [], [(0, 1, 2, 3)])
    for uvn in ("UVMap", "UVMap_Metric_1m"):
        uv = me.uv_layers.new(name=uvn)
        for li, vi in enumerate(me.polygons[0].vertices):
            x, y, _ = me.vertices[vi].co
            uv.data[li].uv = ((x + 2) / (4 if uvn == "UVMap" else 1), (y + 2) / (4 if uvn == "UVMap" else 1))
    ob = bpy.data.objects.new("IFC_SWATCH_O", me); sc.collection.objects.link(ob)
    cam = bpy.data.cameras.new("IFC_SWATCH_C"); cam.type = "ORTHO"; cam.ortho_scale = 3.0
    co = bpy.data.objects.new("IFC_SWATCH_CO", cam); co.location = (0, 0, 5); sc.collection.objects.link(co); sc.camera = co
    return sc, ob


def main():
    objects, used = {}, set()
    for o in bpy.data.objects:
        if o.type != "MESH" or o.name.startswith(SKIP) or not o.material_slots or not o.material_slots[0].material:
            continue
        m = o.material_slots[0].material.name
        objects[o.name] = m; used.add(m)
    sc, ob = swatch_scene()
    tmp = tempfile.mkdtemp()
    mats = {}
    for m in sorted(used):
        mat = bpy.data.materials[m]
        key = m[4:] if m.startswith("MAT_") else m
        nodes = mat.node_tree.nodes if mat.use_nodes else []
        pr = [n for n in nodes if n.type == "BSDF_PRINCIPLED"]
        rec = {"blender": m, "glass": not pr and any(n.type in ("BSDF_TRANSPARENT", "BSDF_GLASS", "BSDF_REFRACTION") for n in nodes)}
        if pr:
            rec["metallic"] = round(float(pr[0].inputs["Metallic"].default_value), 3) if not pr[0].inputs["Metallic"].is_linked else 0.5
        ob.data.materials.clear(); ob.data.materials.append(mat)
        p = os.path.join(tmp, f"{key}.png")
        sc.render.filepath = p
        bpy.ops.render.render(write_still=True, scene=sc.name)
        img = bpy.data.images.load(p)
        px = np.empty(len(img.pixels), np.float32); img.pixels.foreach_get(px)
        rec["rgb"] = [round(float(c), 4) for c in px.reshape(-1, 4)[:, :3].mean(0)]
        bpy.data.images.remove(img)
        mats[key] = rec
        print(f"[ifc_styles] {key:28s} rgb {rec['rgb']} glass={rec['glass']}")
    dst = os.path.join(ROOT, "build", "geom", "ifc_styles.json")
    json.dump({"objects": objects, "materials": mats, "scene": bpy.data.filepath}, open(dst, "w"), indent=0)
    print(f"[ifc_styles] {len(objects)} objects, {len(mats)} materials -> {dst}")


main()
