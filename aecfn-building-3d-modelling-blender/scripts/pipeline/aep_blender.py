"""Blender 5.2 helpers for the aecfn-building-3d-modelling-blender pipeline. Import only inside Blender (or the bpy module)."""
import contextlib, glob, math, os, re
import bpy, bmesh
import numpy as np
from mathutils import Vector, Matrix

NAME_RE = re.compile(r"^(D\d_[A-Za-z0-9]+_[A-Za-z0-9_]+|Botanical_[A-Za-z0-9_]+|Prop_[A-Za-z0-9_]+|Light_[A-Za-z0-9_]+|"
                     r"Cam_\d\d_[A-Za-z0-9_]+|Focus_Target_Cam_\d\d_[A-Za-z0-9_]+|Global_Atmosphere_Volume_Box|Section_[A-Za-z0-9]+_.+|"
                     r"Sun_Key|Ground_.+|REF_[A-Za-z0-9_\-]+)$")
REF_COLL = "REF_Drawings"      # hidden collection of true-scale underlay planes (never rendered, never selected by QA)


# ---------------------------------------------------------------- scene / render
def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return bpy.context.scene


def ensure_collection(name, parent=None):
    c = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    parent = parent or bpy.context.scene.collection
    if c.name not in parent.children:
        parent.children.link(c)
    return c


def setup_render(scene, profile, final=False):
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    with contextlib.suppress(Exception):  # first GPU backend with a usable device, on any OS
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for backend in ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"):
            with contextlib.suppress(Exception):
                prefs.compute_device_type = backend
                prefs.get_devices()
                gpus = [d for d in prefs.devices if d.type == backend]
                if gpus:
                    for d in prefs.devices:
                        d.use = d.type == backend
                    scene.cycles.device = "GPU"
                    break
    res = profile["final_res"] if final else profile["review_res"]
    scene.render.resolution_x, scene.render.resolution_y = res
    scene.render.resolution_percentage = 100
    c = scene.cycles
    c.samples = profile["final_samples"] if final else profile["review_samples"]
    c.use_adaptive_sampling, c.adaptive_threshold = True, 0.01 if final else 0.05
    c.use_denoising = True
    with contextlib.suppress(Exception):
        c.denoiser = "OPENIMAGEDENOISE"
    c.max_bounces, c.volume_bounces = (12, 2) if final else (6, 1)
    c.sample_clamp_indirect = 10.0
    c.use_light_tree = True
    scene.render.use_persistent_data = True
    with contextlib.suppress(Exception):
        scene.view_settings.view_transform = "AgX"
        scene.view_settings.look = "AgX - Medium High Contrast"
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False


def setup_world_and_sun(scene, sun_spec):
    w = scene.world or bpy.data.worlds.new("World_Sky")
    scene.world = w
    nt = w.node_tree
    nt.nodes.clear()
    sky = nt.nodes.new("ShaderNodeTexSky")
    sky.sky_type = "MULTIPLE_SCATTERING"
    sky.sun_elevation = math.radians(sun_spec["elevation_deg"])
    sky.sun_rotation = math.radians(sun_spec["azimuth_deg"])
    sky.sun_disc = False  # the Sun lamp carries the key light and hard shadows
    with contextlib.suppress(AttributeError):  # optional haze controls (spec sun.air / sun.aerosol): paler, hazier sky
        sky.air_density = sun_spec.get("air", 1.0)
        sky.aerosol_density = sun_spec.get("aerosol", 1.0)
    bg = nt.nodes.new("ShaderNodeBackground")  # lighting sky: keep it dim vs the Sun lamp (physical sky is very bright)
    bg.inputs["Strength"].default_value = sun_spec.get("sky_strength", 0.04)  # 0.35 drowns the sun (overcast look)
    o = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(sky.outputs[0], bg.inputs[0])
    if "sky_camera_strength" in sun_spec:  # what the camera sees: paler, brighter backdrop (hazy high-key sky)
        hs = nt.nodes.new("ShaderNodeHueSaturation")
        hs.inputs["Saturation"].default_value = sun_spec.get("sky_camera_saturation", 0.6)
        nt.links.new(sky.outputs[0], hs.inputs["Color"])
        bgc = nt.nodes.new("ShaderNodeBackground"); bgc.inputs["Strength"].default_value = sun_spec["sky_camera_strength"]
        nt.links.new(hs.outputs[0], bgc.inputs[0])
        lp = nt.nodes.new("ShaderNodeLightPath"); mix = nt.nodes.new("ShaderNodeMixShader")
        mx = nt.nodes.new("ShaderNodeMath"); mx.operation = "MAXIMUM"  # glass reflections see the same sky the camera sees
        nt.links.new(lp.outputs["Is Camera Ray"], mx.inputs[0]); nt.links.new(lp.outputs["Is Glossy Ray"], mx.inputs[1])
        nt.links.new(mx.outputs[0], mix.inputs[0])
        nt.links.new(bg.outputs[0], mix.inputs[1]); nt.links.new(bgc.outputs[0], mix.inputs[2])
        nt.links.new(mix.outputs[0], o.inputs[0])
    else:
        nt.links.new(bg.outputs[0], o.inputs[0])
    ld = bpy.data.lights.new("Sun_Key", "SUN")
    ld.energy, ld.angle = sun_spec["strength"], math.radians(sun_spec.get("angle_deg", 0.6))
    ld.color = sun_spec.get("color", (1.0, 1.0, 1.0))
    ob = bpy.data.objects.new("Sun_Key", ld)
    el, az = math.radians(sun_spec["elevation_deg"]), math.radians(sun_spec["azimuth_deg"])
    d = Vector((math.cos(el) * math.sin(az), math.cos(el) * math.cos(az), math.sin(el)))  # direction TO the sun
    ob.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
    scene.collection.objects.link(ob)
    return ob


# ---------------------------------------------------------------- geometry
def npz_to_mesh(path, name, dissolve=True):
    """npz -> bpy mesh datablock. Solids (tris only) get coplanar triangles dissolved to clean n-gons; parametric meshes
    keep their authored polygons (face_idx/face_len)."""
    a = np.load(path)
    me = bpy.data.meshes.new(name)
    if "face_idx" in a:
        faces, p = [], 0
        for n in a["face_len"].tolist():
            faces.append([int(i) for i in a["face_idx"][p:p + n]]); p += n
        me.from_pydata(a["verts"].tolist(), [], faces)
        me.validate(); me.update()
    else:
        me.from_pydata(a["verts"].tolist(), [], a["tris"].tolist())
        me.validate(); me.update()
        if dissolve:
            bm = bmesh.new(); bm.from_mesh(me)
            bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
            bmesh.ops.dissolve_limit(bm, angle_limit=math.radians(1.0), verts=bm.verts, edges=bm.edges)  # tris -> clean ngons
            bmesh.ops.connect_verts_concave(bm, faces=bm.faces[:])  # split concave n-gons (huge concave plates tessellated wrongly)
            bm.to_mesh(me); bm.free()
    for p in me.polygons:
        p.use_smooth = False
    return me, a


def npz_to_object(path, name, coll):
    me, _ = npz_to_mesh(path, name)
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    return ob


def npz_to_instances(path, name, coll):
    """One mesh datablock, k linked-duplicate objects <name>_i0001.. placed by the stored 4x4 transforms."""
    me, a = npz_to_mesh(path, name)
    obs = []
    for k, M in enumerate(a["transforms"], start=1):
        ob = bpy.data.objects.new(f"{name}_i{k:04d}", me)
        ob.matrix_world = Matrix([[float(x) for x in row] for row in M])
        coll.objects.link(ob); obs.append(ob)
    return obs


def imprint_z_datums(ob, zs, tol=1e-4):
    """bmesh.ops.bisect_plane at each world Z datum crossing the object: adds edge loops (no geometry removed)."""
    me = ob.data
    bm = bmesh.new(); bm.from_mesh(me)
    zmin = min(v.co.z for v in bm.verts); zmax = max(v.co.z for v in bm.verts)
    for z in zs:
        if zmin + tol < z < zmax - tol:
            bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], dist=tol,
                                   plane_co=Vector((0, 0, z)), plane_no=Vector((0, 0, 1)))
    bm.to_mesh(me); bm.free()


def metric_uv(ob, name="UVMap_Metric_1m", size=1.0, world=True, mode="box"):
    """1 UV unit == `size` metres on every face, so 1 m texture tiles are true-scale.
    mode "box": per-face box projection (world space, or object space for instanced prototypes: world=False).
    mode "axis": linear members — u runs along the mesh's longest bbox axis, v around it (grain follows the member)."""
    me = ob.data
    uv = me.uv_layers.get(name) or me.uv_layers.new(name=name)
    me.uv_layers.active = uv
    mw = ob.matrix_world if world else Matrix.Identity(4)
    if mode == "axis":
        co_all = np.array([(mw @ v.co)[:] for v in me.vertices])
        ext = co_all.max(0) - co_all.min(0); L = int(np.argmax(ext))
        for poly in me.polygons:
            n = (mw.to_3x3() @ poly.normal)
            others = [i for i in range(3) if i != L]
            ax2 = max(others, key=lambda i: abs(n[i]))          # the other axis the face mostly faces
            V = [i for i in others if i != ax2][0]
            for li in poly.loop_indices:
                co = mw @ me.vertices[me.loops[li].vertex_index].co
                uv.data[li].uv = (co[L] / size, co[V] / size)
        return
    for poly in me.polygons:
        n = (mw.to_3x3() @ poly.normal)
        ax = max(range(3), key=lambda i: abs(n[i]))
        for li in poly.loop_indices:
            co = mw @ me.vertices[me.loops[li].vertex_index].co
            u, v = [(co.y, co.z), (co.x, co.z), (co.x, co.y)][ax]
            uv.data[li].uv = (u / size, v / size)


# ---------------------------------------------------------------- underlay reference planes + overlay cameras
def underlay_plane(info, coll):
    """True-scale image plane for a calibrated underlay (pipeline.underlays.load): pixel (u0, v0) sits on the world origin.
    Plans lie flat at Z = info['z']; sections stand in the XZ plane at Y = info['y']. Unlit emission material."""
    W, H, mpp = info["W"], info["H"], info["mpp"]
    w, h = W * mpp, H * mpp
    me = bpy.data.meshes.new(f"REF_{info['name']}")
    # plane in its own (a, b) frame: a = +x of the image, b = up of the image
    a0, a1 = -info["u0"] * mpp, (W - info["u0"]) * mpp
    b0, b1 = (info["v0"] - H) * mpp, info["v0"] * mpp
    if info["kind"] == "plan":
        z = info["z"]; verts = [(a0, b0, z), (a1, b0, z), (a1, b1, z), (a0, b1, z)]
    else:
        y = info["y"]; verts = [(a0, y, b0), (a1, y, b0), (a1, y, b1), (a0, y, b1)]
    me.from_pydata(verts, [], [[0, 1, 2, 3]]); me.update()
    uv = me.uv_layers.new(name="UVMap")
    for li, (u, v) in zip(me.polygons[0].loop_indices, [(0, 0), (1, 0), (1, 1), (0, 1)]):
        uv.data[li].uv = (u, v)
    ob = bpy.data.objects.new(f"REF_{info['name']}", me)
    ob["underlay"] = info["name"]; ob["metres_per_px"] = mpp
    coll.objects.link(ob)
    mat = bpy.data.materials.new(f"MAT_REF_{info['name']}"); nt = mat.node_tree; N, L = nt.nodes, nt.links; N.clear()
    o = N.new("ShaderNodeOutputMaterial"); em = N.new("ShaderNodeEmission"); t = N.new("ShaderNodeTexImage")
    t.image = bpy.data.images.load(info["png"], check_existing=True)
    L.new(t.outputs[0], em.inputs["Color"]); L.new(em.outputs[0], o.inputs["Surface"])
    me.materials.append(mat)
    ob.hide_render = True; ob.visible_camera = False; ob.visible_shadow = False
    ob.display_type = "TEXTURED"
    return ob


def overlay_camera(info, coll, cut_offset=1.2, far=1000.0, depth=None):
    """Orthographic camera framing exactly the underlay's world window, with clip_start acting as the cut plane:
    plans look down from above (everything above z + cut_offset is clipped away = a plan CUT);
    sections look along +Y from y - far (everything nearer than the section plane is clipped away)."""
    a0, a1, b0, b1 = info["extent"]
    name = f"Cam_9{len([c for c in coll.objects if c.type == 'CAMERA']) % 10}_Overlay_{info['name']}"[:63]
    cd = bpy.data.cameras.new(name); cd.type = "ORTHO"
    cd.sensor_fit = "HORIZONTAL"; cd.sensor_width = 36.0
    cd.ortho_scale = a1 - a0
    ob = bpy.data.objects.new(name, cd); coll.objects.link(ob)
    cx, cy = (a0 + a1) / 2, (b0 + b1) / 2
    if info["kind"] == "plan":   # drawing depth: one storey below the cut by default
        zc = info["z"] + cut_offset
        ob.location = (cx, cy, zc + far); ob.rotation_euler = (0, 0, 0)
        cd.clip_start, cd.clip_end = far, far + (depth if depth is not None else 4.5)
    else:                        # section: only what sits close behind the cut plane
        y = info["y"]
        ob.location = (cx, y - far, cy); ob.rotation_euler = (math.radians(90), 0, 0)
        cd.clip_start, cd.clip_end = far, far + (depth if depth is not None else 1.5)
    ob["underlay"] = info["name"]; ob["res_x"] = info["W"]; ob["res_y"] = info["H"]; ob["aspect"] = (a1 - a0) / (b1 - b0)
    cd.dof.use_dof = False
    return ob


def section_cutaway(sid, sec, source_colls, poche_mat, datums=None):
    """World-space BMesh Section cutaway: duplicate every mesh, bisect at the cut plane, delete the near side, cap the cut."""
    coll = ensure_collection(f"Section_{sid}")
    axis = {"x": 0, "y": 1}[sec["axis"]]
    no = Vector([1 if i == axis else 0 for i in range(3)])
    co = no * float(sec["value"])
    for src in source_colls:
        for ob in src.all_objects:
            if ob.type != "MESH" or ob.name.startswith(("Botanical_", "REF_")):  # a section is a drawing: no vegetation
                continue
            me = ob.data.copy()
            me.transform(ob.matrix_world)
            bm = bmesh.new(); bm.from_mesh(me)
            res = bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], dist=1e-5,
                                         plane_co=co, plane_no=no, clear_inner=True)  # keep the far side (+axis)
            edges = [e for e in res["geom_cut"] if isinstance(e, bmesh.types.BMEdge)]
            if not bm.faces:
                bm.free(); continue
            if edges:
                cap = bmesh.ops.holes_fill(bm, edges=edges, sides=0)
                names = [m.name if m else None for m in me.materials]  # empty slots are None
                if poche_mat.name not in names:
                    me.materials.append(poche_mat); names.append(poche_mat.name)
                idx = names.index(poche_mat.name)
                for f in cap["faces"]:
                    f.material_index = idx
            bm.to_mesh(me); bm.free()
            dup = bpy.data.objects.new(f"Section_{sid}_{ob.name}", me)
            coll.objects.link(dup)
    hatch_poche(poche_mat, pitch=0.45)
    if datums and sec.get("datum_geometry"):  # default: datums are drawn as a 2-D overlay after render (run.overlay_ortho)
        section_datums(coll, sec, datums)
    coll.hide_render = True  # only enabled for the section camera
    return coll


def hatch_poche(mat, pitch=0.35):
    """ANSI31-style 45 deg hatching on the cut faces: world-space diagonal bands mixed into the poche colour."""
    nt = mat.node_tree; N, L = nt.nodes, nt.links
    bsdf = N.get("Principled BSDF")
    if bsdf is None or any(n.bl_idname == "ShaderNodeTexWave" for n in N):
        return
    geo = N.new("ShaderNodeNewGeometry")
    wave = N.new("ShaderNodeTexWave"); wave.wave_type, wave.bands_direction = "BANDS", "DIAGONAL"
    wave.inputs["Scale"].default_value = 1.0 / pitch / 3.0; wave.inputs["Distortion"].default_value = 0.0
    L.new(geo.outputs["Position"], wave.inputs["Vector"])
    ramp = N.new("ShaderNodeValToRGB")
    e0, e1 = ramp.color_ramp.elements
    e0.position, e0.color = 0.88, (*bsdf.inputs["Base Color"].default_value[:3], 1)
    e1.position, e1.color = 0.905, (0.03, 0.03, 0.035, 1)
    L.new(wave.outputs["Factor"], ramp.inputs[0]); L.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])


def section_datums(coll, sec, datums, half_len=40.0, label_size=0.85):
    """Labelled datum lines floating just in front of the cut plane, seen only by the section camera."""
    mat = emission_free_material("MAT_Section_Datum", (0.02, 0.03, 0.06), rough=1.0)
    axis = sec["axis"]; v = float(sec["value"]) - 0.25  # in front of the cut faces (near side is deleted)
    last, col = -1e9, 0
    for name, z in sorted(datums.items(), key=lambda kv: kv[1]):
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        me = bpy.data.meshes.new(f"Section_Datum_{name}"); bm.to_mesh(me); bm.free()
        me.materials.append(mat)
        ln = bpy.data.objects.new(f"Section_{sec.get('_id', 'X-X')}_Datum_{name}", me)
        ln.scale = (2 * half_len, 0.02, 0.03) if axis == "y" else (0.02, 2 * half_len, 0.03)
        ln.location = (0, v, z) if axis == "y" else (v, 0, z)
        coll.objects.link(ln)
        col = col + 1 if z - last < label_size * 1.6 else 0
        if col == 0:
            last = z
        cu = bpy.data.curves.new(f"Section_Label_{name}", "FONT")
        cu.body, cu.size = f"{name}  {z:+.2f} m", label_size
        cu.materials.append(mat)
        tx = bpy.data.objects.new(f"Section_{sec.get('_id', 'X-X')}_Label_{name}", cu)
        off = -half_len + 3.5 + (col % 4) * 13.0
        if axis == "y":
            tx.location, tx.rotation_euler = (off, v - 0.01, z + 0.08), (math.radians(90), 0, 0)
        else:
            tx.location, tx.rotation_euler = (v - 0.01, -off, z + 0.08), (math.radians(90), 0, math.radians(-90))
        coll.objects.link(tx)


# ---------------------------------------------------------------- materials (5-channel PBR + §4.2 imperfection stacking)
CHANNELS = ("albedo", "roughness", "normal", "height", "ao")


def glass_material(mat, tint, transmission, refl, rough, gloss=(1, 1, 1), ior=1.52):
    """Architectural thin glass (no refraction, flat panes): mix(Transparent(tint*transmission), Glossy) by Fresnel +
    base reflectance. Used by pbr_material for any spec material carrying "transmission"."""
    nt = mat.node_tree; N, Lk = nt.nodes, nt.links
    N.clear()
    out = N.new("ShaderNodeOutputMaterial")
    tr = N.new("ShaderNodeBsdfTransparent")
    mx = max(tint) or 1.0
    tr.inputs["Color"].default_value = (*[transmission * c / mx for c in tint], 1.0)
    gl = N.new("ShaderNodeBsdfGlossy")
    gl.inputs["Color"].default_value = (*gloss, 1)
    gl.inputs["Roughness"].default_value = rough
    fr = N.new("ShaderNodeFresnel"); fr.inputs["IOR"].default_value = ior
    # closed panes are seen/lit through their back faces too: Cycles' Fresnel inverts the IOR on backfacing hits, so rays
    # leaving a pane beyond ~41 deg are totally internally reflected (sun blocked, milky panes). Feed 1/ior on backfaces.
    geo = N.new("ShaderNodeNewGeometry"); mfa = N.new("ShaderNodeMath"); mfa.operation = "MULTIPLY_ADD"
    Lk.new(geo.outputs["Backfacing"], mfa.inputs[0]); mfa.inputs[1].default_value = 1.0 / ior - ior; mfa.inputs[2].default_value = ior
    Lk.new(mfa.outputs[0], fr.inputs["IOR"])
    add = N.new("ShaderNodeMath"); add.operation = "ADD"; add.use_clamp = True
    Lk.new(fr.outputs[0], add.inputs[0]); add.inputs[1].default_value = refl
    mix = N.new("ShaderNodeMixShader")
    Lk.new(add.outputs[0], mix.inputs[0]); Lk.new(tr.outputs[0], mix.inputs[1]); Lk.new(gl.outputs[0], mix.inputs[2])
    Lk.new(mix.outputs[0], out.inputs["Surface"])
    with contextlib.suppress(Exception):
        mat.blend_method = "HASHED"
    return mat


def pbr_material(key, tex_dir, fallback_rgb=(0.5, 0.5, 0.5), opts=None):
    mat = bpy.data.materials.get(f"MAT_{key}") or bpy.data.materials.new(f"MAT_{key}")
    if opts and opts.get("transmission") is not None:  # glass: never an opaque PBR tree
        r = opts.get("roughness", 0.03)
        r = sum(r) / len(r) if isinstance(r, (list, tuple)) else float(r)
        return glass_material(mat, tuple(fallback_rgb), float(opts["transmission"]), float(opts.get("glass_reflectance", 0.08)),
                              r, tuple(opts.get("glass_gloss", (1, 1, 1))), float(opts.get("ior", 1.52)))
    nt = mat.node_tree; N, L = nt.nodes, nt.links
    N.clear()
    out = N.new("ShaderNodeOutputMaterial"); bsdf = N.new("ShaderNodeBsdfPrincipled")
    L.new(bsdf.outputs[0], out.inputs["Surface"])
    uvn = N.new("ShaderNodeUVMap"); uvn.uv_map = "UVMap_Metric_1m"
    maps = {}
    for ch in CHANNELS:
        f = glob.glob(os.path.join(tex_dir, key, f"{ch}.*"))
        if f:
            t = N.new("ShaderNodeTexImage"); t.image = bpy.data.images.load(f[0], check_existing=True)
            if ch != "albedo":
                t.image.colorspace_settings.name = "Non-Color"
            L.new(uvn.outputs[0], t.inputs[0]); maps[ch] = t
    # Base colour = albedo x AO
    base = maps["albedo"].outputs[0] if "albedo" in maps else None
    if base is None:
        rgb = N.new("ShaderNodeRGB"); rgb.outputs[0].default_value = (*fallback_rgb, 1); base = rgb.outputs[0]
    if "ao" in maps:
        m = N.new("ShaderNodeMix"); m.data_type, m.blend_type = "RGBA", "MULTIPLY"
        m.inputs["Factor"].default_value = 1.0
        L.new(base, m.inputs["A"]); L.new(maps["ao"].outputs[0], m.inputs["B"]); base = m.outputs["Result"]
    # §4.2 Pointiness + AO edge-wear / crevice dirt -> Base Color & Roughness
    geo = N.new("ShaderNodeNewGeometry"); aon = N.new("ShaderNodeAmbientOcclusion"); aon.inputs["Distance"].default_value = 0.25
    wear = N.new("ShaderNodeValToRGB")
    wear.color_ramp.elements[0].position, wear.color_ramp.elements[1].position = 0.48, 0.56
    L.new(geo.outputs["Pointiness"], wear.inputs[0])
    dirt = N.new("ShaderNodeValToRGB")
    dirt.color_ramp.elements[0].position, dirt.color_ramp.elements[1].position = 0.35, 0.9
    L.new(aon.outputs["AO"], dirt.inputs[0])
    m_w = N.new("ShaderNodeMix"); m_w.data_type, m_w.blend_type = "RGBA", "SCREEN"; m_w.inputs["Factor"].default_value = (opts or {}).get("edge_wear", 0.12)  # spec material "edge_wear": 0 for glossy painted metal
    L.new(base, m_w.inputs["A"]); L.new(wear.outputs["Color"], m_w.inputs["B"])
    m_d = N.new("ShaderNodeMix"); m_d.data_type, m_d.blend_type = "RGBA", "MULTIPLY"; m_d.inputs["Factor"].default_value = (opts or {}).get("crevice_dirt", 0.5)
    L.new(m_w.outputs["Result"], m_d.inputs["A"]); L.new(dirt.outputs["Color"], m_d.inputs["B"])
    base_out = m_d.outputs["Result"]
    opts = opts or {}
    # macro variation: world-space grime at ~2-5 m scale breaks 1 m tile repetition (multiply, subtle)
    pos = N.new("ShaderNodeNewGeometry")
    macro = N.new("ShaderNodeTexNoise"); macro.inputs["Scale"].default_value = opts.get("macro_scale", 0.35)
    macro.inputs["Detail"].default_value = 6.0; L.new(pos.outputs["Position"], macro.inputs["Vector"])
    mr = N.new("ShaderNodeValToRGB")
    mr.color_ramp.elements[0].position, mr.color_ramp.elements[0].color = 0.3, (0.72, 0.68, 0.64, 1)
    mr.color_ramp.elements[1].position, mr.color_ramp.elements[1].color = 0.7, (1, 1, 1, 1)
    L.new(macro.outputs["Factor"], mr.inputs[0])
    mm = N.new("ShaderNodeMix"); mm.data_type, mm.blend_type = "RGBA", "MULTIPLY"
    mm.inputs["Factor"].default_value = opts.get("macro_strength", 0.6)
    L.new(base_out, mm.inputs["A"]); L.new(mr.outputs["Color"], mm.inputs["B"]); base_out = mm.outputs["Result"]
    if opts.get("streaks"):  # vertical rain/dust streaks: noise stretched along world Z
        mp = N.new("ShaderNodeMapping"); mp.inputs["Scale"].default_value = (7.0, 7.0, 0.45)
        L.new(pos.outputs["Position"], mp.inputs["Vector"])
        st = N.new("ShaderNodeTexNoise"); st.inputs["Scale"].default_value = 3.0; st.inputs["Detail"].default_value = 4.0
        L.new(mp.outputs["Vector"], st.inputs["Vector"])
        sr = N.new("ShaderNodeValToRGB")
        sr.color_ramp.elements[0].position, sr.color_ramp.elements[0].color = 0.55, (1, 1, 1, 1)
        sr.color_ramp.elements[1].position, sr.color_ramp.elements[1].color = 0.75, (0.62, 0.58, 0.52, 1)
        L.new(st.outputs["Factor"], sr.inputs[0])
        sm = N.new("ShaderNodeMix"); sm.data_type, sm.blend_type = "RGBA", "MULTIPLY"
        sm.inputs["Factor"].default_value = opts.get("streaks", 0.5)
        L.new(base_out, sm.inputs["A"]); L.new(sr.outputs["Color"], sm.inputs["B"]); base_out = sm.outputs["Result"]
    if "brightness" in opts:  # per-material value trim (e.g. paler gravel)
        bc = N.new("ShaderNodeBrightContrast"); bc.inputs["Bright"].default_value = opts["brightness"]
        L.new(base_out, bc.inputs["Color"]); base_out = bc.outputs["Color"]
    if "specular" in opts:  # tame grazing glints (e.g. clay tiles)
        bsdf.inputs["Specular IOR Level"].default_value = opts["specular"]
    if "metallic" in opts:
        bsdf.inputs["Metallic"].default_value = opts["metallic"]
    if "saturation" in opts:  # per-material saturation trim (e.g. keep vermilion saturated under AgX)
        hs = N.new("ShaderNodeHueSaturation"); hs.inputs["Saturation"].default_value = opts["saturation"]
        L.new(base_out, hs.inputs["Color"]); base_out = hs.outputs["Color"]
    L.new(base_out, bsdf.inputs["Base Color"])
    # §4.2 Roughness channel priority: base roughness LINEAR_LIGHT micro-grunge noise (Factor 0.28)
    grunge = N.new("ShaderNodeTexNoise"); grunge.inputs["Scale"].default_value = 85.0; grunge.inputs["Detail"].default_value = 12.0
    L.new(uvn.outputs[0], grunge.inputs["Vector"])
    rmix = N.new("ShaderNodeMix"); rmix.data_type, rmix.blend_type = "RGBA", "LINEAR_LIGHT"; rmix.inputs["Factor"].default_value = 0.28
    if "roughness" in maps:
        L.new(maps["roughness"].outputs[0], rmix.inputs["A"])
    else:  # no roughness map: the spec's roughness (mean of [lo, hi]) instead of a flat 0.6
        r = opts.get("roughness", 0.6)
        r = sum(r) / len(r) if isinstance(r, (list, tuple)) else float(r)
        rmix.inputs["A"].default_value = (r, r, r, 1)
    L.new(grunge.outputs["Factor"], rmix.inputs["B"])
    rw = N.new("ShaderNodeMix"); rw.data_type, rw.blend_type = "RGBA", "SUBTRACT"; rw.inputs["Factor"].default_value = 0.15
    L.new(rmix.outputs["Result"], rw.inputs["A"]); L.new(wear.outputs["Color"], rw.inputs["B"])  # worn edges are polished
    L.new(rw.outputs["Result"], bsdf.inputs["Roughness"])
    # Normal + height (bump; true displacement only for final if mesh is dense)
    nrm_in = None
    if "normal" in maps:
        nm = N.new("ShaderNodeNormalMap"); nm.uv_map = "UVMap_Metric_1m"
        nm.inputs["Strength"].default_value = min(1.0, float(opts.get("bump", 0.35)) / 0.35)  # bump scales the derived normal map too
        L.new(maps["normal"].outputs[0], nm.inputs["Color"]); nrm_in = nm.outputs[0]
    if "height" in maps:
        bump = N.new("ShaderNodeBump"); bump.inputs["Strength"].default_value = float(opts.get("bump", 0.35)); bump.inputs["Distance"].default_value = 0.01
        L.new(maps["height"].outputs[0], bump.inputs["Height"])
        if nrm_in:
            L.new(nrm_in, bump.inputs["Normal"])
        nrm_in = bump.outputs[0]
    if nrm_in:
        L.new(nrm_in, bsdf.inputs["Normal"])
    return mat


def emission_free_material(name, rgb, rough=0.6, metal=0.0):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    b = mat.node_tree.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (*rgb, 1); b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    return mat


# ---------------------------------------------------------------- §4 Five Blender-native visual strengths
def gobo_canopy(sun_ob, size=120.0, height=30.0, center=(0, 0, 0), open_to=0.62, leaf_from=0.70):
    """§4.1 Gobo: alpha-cutout foliage/slat plane between the key light and the set."""
    bpy.ops.mesh.primitive_plane_add(size=size)
    p = bpy.context.active_object; p.name = "Light_Gobo_Canopy"
    d = (sun_ob.matrix_world.to_3x3() @ Vector((0, 0, 1))).normalized()  # towards the sun
    # horizontal canopy at `height`, slid up-sun so its shadow lands on `center`; never cuts the ground or the set
    t = height / max(d.z, 0.05)
    p.location = Vector(center) + d * t
    p.rotation_euler = (0, 0, 0)
    mat = bpy.data.materials.new("MAT_Light_Gobo_Canopy"); nt = mat.node_tree; N, L = nt.nodes, nt.links; N.clear()
    o = N.new("ShaderNodeOutputMaterial"); mix = N.new("ShaderNodeMixShader")
    tr = N.new("ShaderNodeBsdfTransparent"); df = N.new("ShaderNodeBsdfDiffuse")
    tc = N.new("ShaderNodeTexCoord")  # Object coords = metres, so leaf clusters have a real-world size
    vo = N.new("ShaderNodeTexVoronoi"); vo.inputs["Scale"].default_value = 0.9
    no = N.new("ShaderNodeTexNoise"); no.inputs["Scale"].default_value = 0.22; no.inputs["Detail"].default_value = 10.0
    L.new(tc.outputs["Object"], vo.inputs["Vector"]); L.new(tc.outputs["Object"], no.inputs["Vector"])
    add = N.new("ShaderNodeMath"); add.operation = "MULTIPLY_ADD"; add.inputs[1].default_value = 0.25  # noise + 0.25*voronoi
    L.new(vo.outputs["Distance"], add.inputs[0]); L.new(no.outputs["Factor"], add.inputs[2])
    ramp = N.new("ShaderNodeValToRGB")  # coverage: ~30-40% leaf; raise 'leaf_from' to open the canopy
    e0, e1 = ramp.color_ramp.elements
    e0.position, e0.color = open_to, (1, 1, 1, 1)   # open sky -> Transparent
    e1.position, e1.color = leaf_from, (0, 0, 0, 1)   # leaf -> Diffuse occluder
    L.new(add.outputs[0], ramp.inputs[0])
    L.new(ramp.outputs["Color"], mix.inputs["Factor"])  # Factor 1 = Transparent (2nd shader)
    df.inputs["Color"].default_value = (0.02, 0.03, 0.02, 1)
    L.new(df.outputs[0], mix.inputs[1]); L.new(tr.outputs[0], mix.inputs[2]); L.new(mix.outputs[0], o.inputs["Surface"])
    p.data.materials.append(mat)
    p.visible_camera = False  # casts shadows, never seen
    p.visible_glossy = False
    p.display_type, p.hide_select = "WIRE", True  # don't hide the set in the viewport (live inspection)
    return p


def textured_lights():
    """§4.1 Procedural light textures on Area/Spot lights: Noise(Scale 6.0) -> ColorRamp(0.82–1.0) -> Emission strength."""
    for ob in [o for o in bpy.data.objects if o.type == "LIGHT" and o.data.type in {"AREA", "SPOT"}]:
        light = ob.data
        light.use_nodes = True  # deprecated in 5.x but still the switch for Cycles light node trees
        nt = light.node_tree; N, L = nt.nodes, nt.links
        em = next((n for n in N if n.bl_idname == "ShaderNodeEmission"), None)
        if em is None:  # custom light tree from a zone's dress(): leave it alone
            continue
        noise = N.new("ShaderNodeTexNoise"); noise.inputs["Scale"].default_value = 6.0
        ramp = N.new("ShaderNodeValToRGB")
        ramp.color_ramp.elements[0].color = (0.82, 0.82, 0.82, 1); ramp.color_ramp.elements[1].color = (1, 1, 1, 1)
        L.new(noise.outputs["Factor"], ramp.inputs[0]); L.new(ramp.outputs["Color"], em.inputs["Color"])


def build_camera_rig(spec, scene):
    """§4.3 + §5: full-frame 36 mm sensor on every camera, Focus_Target_<CamName> empties, aperture by shot class."""
    if not spec.get("cameras"):
        raise SystemExit('[Stage01c] spec["cameras"] is empty: define at least one camera (§5).')
    coll = ensure_collection("90_Camera_Rig")
    cams = []
    for c in spec["cameras"]:
        cd = bpy.data.cameras.new(c["name"])
        cd.sensor_fit, cd.sensor_width = "HORIZONTAL", 36.0
        cd.clip_start, cd.clip_end = c.get("clip_start", 0.05), c.get("clip_end", 4000)
        ob = bpy.data.objects.new(c["name"], cd); coll.objects.link(ob)
        ob.location = Vector(c["loc"])
        tgt = Vector(c["target"])
        ob.rotation_euler = (tgt - ob.location).to_track_quat("-Z", "Y").to_euler()
        cd.shift_x, cd.shift_y = c.get("shift_x", 0.0), c.get("shift_y", 0.0)  # architectural rise: keep verticals straight
        if c["class"] == "ortho":
            cd.type, cd.ortho_scale = "ORTHO", c.get("ortho_scale", 60)
            cd.dof.use_dof = False
        else:
            cd.lens = c.get("lens", 35)
            ft = bpy.data.objects.new(f"Focus_Target_{c['name']}", None); ft.location = tgt; coll.objects.link(ft)
            cd.dof.use_dof, cd.dof.focus_object = True, ft
            cd.dof.aperture_fstop = c.get("fstop", 2.2 if c["class"] in {"close", "medium"} else 4.8)
            cd.dof.aperture_blades = 7
        cams.append(ob)
    scene.camera = cams[0]
    return cams


def compositor_polish(scene):
    """§4.4 Fog Glow (Threshold 1.15, Strength 0.25) -> Lens Distortion (Dispersion 0.008) -> S-curve. Blender 5.x node-group compositor."""
    nt = bpy.data.node_groups.new("AEP_Compositor", "CompositorNodeTree")
    nt.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    N, L = nt.nodes, nt.links
    rl = N.new("CompositorNodeRLayers"); rl.scene = scene
    gl = N.new("CompositorNodeGlare")
    gl.inputs["Type"].default_value = "Fog Glow"; gl.inputs["Quality"].default_value = "High"
    gl.inputs["Threshold"].default_value = 1.15; gl.inputs["Strength"].default_value = 0.25
    ld = N.new("CompositorNodeLensdist"); ld.inputs["Dispersion"].default_value = 0.008
    cv = N.new("CompositorNodeCurveRGB")
    curve = cv.mapping.curves[3]  # combined curve
    curve.points[0].location = (0.0, 0.0); curve.points[1].location = (1.0, 1.0)
    curve.points.new(0.25, 0.21); curve.points.new(0.75, 0.80)
    cv.mapping.update()
    go = N.new("NodeGroupOutput")
    L.new(rl.outputs["Image"], gl.inputs["Image"]); L.new(gl.outputs["Image"], ld.inputs["Image"])
    L.new(ld.outputs["Image"], cv.inputs["Image"]); L.new(cv.outputs["Image"], go.inputs[0])
    scene.compositing_node_group = nt
    return nt


def atmosphere_box(spec, scene, margin=40.0):
    """§4.5 Global_Atmosphere_Volume_Box: Principled Volume, ultra-low density, forward-scattering anisotropy 0.70."""
    mins = Vector((1e9, 1e9, 1e9)); maxs = -mins
    for ob in scene.objects:
        if ob.type == "MESH" and not ob.name.startswith("REF_"):
            for c in ob.bound_box:
                w = ob.matrix_world @ Vector(c)
                mins = Vector(map(min, mins, w)); maxs = Vector(map(max, maxs, w))
    bpy.ops.mesh.primitive_cube_add(size=1)
    box = bpy.context.active_object; box.name = "Global_Atmosphere_Volume_Box"
    box.location = (mins + maxs) / 2 + Vector((0, 0, margin / 2))
    box.scale = (maxs - mins) + Vector((margin * 2, margin * 2, margin))
    if spec.get("atmosphere_radius_m"):  # clamp the haze volume (a city-wide box turns long camera rays into a white band)
        R = float(spec["atmosphere_radius_m"])
        box.location.x, box.location.y = 0.0, 0.0
        box.scale.x, box.scale.y = min(box.scale.x, 2 * R), min(box.scale.y, 2 * R)
    mat = bpy.data.materials.new("MAT_Global_Atmosphere"); nt = mat.node_tree; N, L = nt.nodes, nt.links; N.clear()
    o = N.new("ShaderNodeOutputMaterial"); v = N.new("ShaderNodeVolumePrincipled")
    v.inputs["Density"].default_value = spec.get("atmosphere_density", 0.0035)
    v.inputs["Anisotropy"].default_value = 0.70
    v.inputs["Color"].default_value = (*spec.get("atmosphere_color", (0.92, 0.95, 1.0)), 1)
    L.new(v.outputs["Volume"], o.inputs["Volume"])
    box.data.materials.append(mat)
    box.visible_shadow = False
    box.display_type, box.hide_select = "WIRE", True  # render-only: keep the viewport see-through
    return box


def apply_visual_strengths(spec, scene, sun_ob, final):
    """Run all five layers. Order matters: rig before compositor; atmosphere last (needs final scene bounds)."""
    g = spec.get("gobo")  # v2: opt-in (a 30 m canopy cuts through a tower; dappled light suits precincts, not high-rises)
    if g:
        gobo_canopy(sun_ob, g.get("size", 120.0), g.get("height", 30.0), g.get("center", (0, 0, 0)),
                    g.get("open_to", 0.62), g.get("leaf_from", 0.70))
    textured_lights()
    compositor_polish(scene)
    box = atmosphere_box(spec, scene)
    box.hide_render = not (final or spec["_profile"]["volume_in_review"])
    return box


# ---------------------------------------------------------------- rendering
@contextlib.contextmanager
def render_lock(root):
    """One Blender render at a time across all agents (prevents GPU/RAM contention). POSIX flock, or msvcrt on Windows."""
    path = os.path.join(root, "build", ".render.lock"); os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a+") as f:
        try:
            import fcntl
            lock, unlock = (lambda: fcntl.flock(f, fcntl.LOCK_EX)), (lambda: fcntl.flock(f, fcntl.LOCK_UN))
        except ImportError:  # Windows: LK_LOCK gives up after ~10 s, so keep retrying while another render holds it
            import msvcrt, time

            def lock():
                while True:
                    f.seek(0)  # msvcrt locks from the current position: always lock/unlock byte 0
                    try:
                        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1); return
                    except OSError:
                        time.sleep(1)

            def unlock():
                f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        lock()
        try:
            yield
        finally:
            unlock()


@contextlib.contextmanager
def workbench_drawing(scene, res=None):
    """Temporarily switch to a flat white Workbench render with black object outlines on a transparent film:
    the output is a line drawing of the model at exactly the camera's world window (used for underlay overlays)."""
    keep = dict(engine=scene.render.engine, rx=scene.render.resolution_x, ry=scene.render.resolution_y, ft=scene.render.film_transparent,
                vt=scene.view_settings.view_transform, look=scene.view_settings.look, cng=scene.compositing_node_group, exp=scene.view_settings.exposure)
    scene.render.engine = "BLENDER_WORKBENCH"
    sh = scene.display.shading
    sh.light, sh.color_type = "FLAT", "RANDOM"           # a colour per object: every object boundary is an edge
    sh.show_object_outline = True; sh.object_outline_color = (0, 0, 0)
    sh.show_shadows = sh.show_cavity = sh.show_xray = False; sh.show_backface_culling = False
    scene.render.film_transparent = True
    scene.view_settings.view_transform = "Standard"; scene.view_settings.look = "None"; scene.view_settings.exposure = 0.0
    scene.compositing_node_group = None
    if res:
        scene.render.resolution_x, scene.render.resolution_y = res
    try:
        yield
    finally:
        scene.render.engine = keep["engine"]; scene.render.resolution_x, scene.render.resolution_y = keep["rx"], keep["ry"]
        scene.render.film_transparent = keep["ft"]; scene.view_settings.view_transform = keep["vt"]
        with contextlib.suppress(Exception):
            scene.view_settings.look = keep["look"]
        scene.view_settings.exposure = keep["exp"]; scene.compositing_node_group = keep["cng"]


def render_overlay_cameras(scene, cams, out_dir, max_px=3000):
    """Workbench line drawings for the Cam_9x_Overlay_* cameras -> <out_dir>/<cam>.png (+ _overlay_meta.json)."""
    import json
    os.makedirs(out_dir, exist_ok=True)
    meta, paths = {}, []
    hide = [o for o in scene.objects if o.name.startswith(("Light_Gobo", "Ground_Hills", "Global_Atmosphere", "Botanical_", "REF_")) and not o.hide_render]
    sec = [c for c in bpy.data.collections if c.name.startswith("Section_")]
    sec_state = {c.name: c.hide_render for c in sec}
    for o in hide:
        o.hide_render = True
    for c in sec:
        c.hide_render = True
    try:
        for cam in cams:
            W, H = int(cam["res_x"]), int(cam["res_y"])
            s = min(1.0, max_px / max(W, H)); res = (max(16, int(W * s)), max(16, int(H * s)))
            with workbench_drawing(scene, res):
                scene.camera = cam
                scene.render.filepath = os.path.join(out_dir, f"{cam.name}.png")
                bpy.ops.render.render(write_still=True)
            paths.append(scene.render.filepath)
            meta[cam.name] = dict(underlay=cam["underlay"], res=res, scale=s, ortho_scale=cam.data.ortho_scale, loc=list(cam.location))
    finally:
        for o in hide:
            o.hide_render = False
        for c in sec:
            c.hide_render = sec_state[c.name]
    with open(os.path.join(out_dir, "_overlay_meta.json"), "w") as f:
        json.dump(meta, f, indent=1)
    return paths


def render_cameras(scene, cams, out_dir, pass_no, cam_specs=None):
    """cam_specs: {name: spec camera dict}; optional per-camera keys: "show_zones" [zone names] (e.g. a bare-structure
    construction-stage view answering a construction photo) and "exposure" (interiors)."""
    os.makedirs(out_dir, exist_ok=True)
    for o in scene.objects:  # underlay planes are reference only
        if o.name.startswith("REF_"):
            o.hide_render = True
    zone_colls = [c for c in bpy.data.collections if c.name[:2].isdigit() and not c.name.startswith("90_")]
    base = {c.name: c.hide_render for c in zone_colls}  # respects --zone isolation set by the caller
    sec = [c for c in bpy.data.collections if c.name.startswith("Section_")]
    box = bpy.data.objects.get("Global_Atmosphere_Volume_Box")
    box_base = box.hide_render if box else None
    paths = []
    exp0 = scene.view_settings.exposure
    import fnmatch
    spec_all = next(iter((cam_specs or {}).values()), {}).get("_construction_only", [])
    constr = [o for o in scene.objects if any(fnmatch.fnmatch(o.name, p) for p in spec_all)]
    constr_base = {o.name: o.hide_render for o in constr}
    for cam in cams:
        scene.camera = cam
        cs = (cam_specs or {}).get(cam.name, {})
        scene.view_settings.exposure = cs.get("exposure", exp0)
        poche = bpy.data.materials.get("MAT_Section_Poche")  # ANSI31-like hatch pitch per section camera (plot-scale legibility)
        if poche and poche.node_tree:
            for n in poche.node_tree.nodes:
                if n.bl_idname == "ShaderNodeTexWave":
                    n.inputs["Scale"].default_value = 1.0 / float(cs.get("hatch_pitch", 0.45)) / 3.0
        is_sec = "Section" in cam.name  # section camera sees only the cut copies
        for c in sec:
            c.hide_render = not is_sec
        show = {z.title().replace("_", "") for z in cs.get("show_zones", [])}
        for o in constr:  # construction-only objects (e.g. slab-edge handrails) appear only in show_zones cameras
            o.hide_render = constr_base[o.name] or not show
        for c in zone_colls:
            c.hide_render = True if is_sec else (base[c.name] or (bool(show) and c.name.split("_", 1)[1] not in show and not c.name.startswith("00_")))
        if box:  # sections and orthographic views are technical drawings: no haze
            box.hide_render = True if (is_sec or cam.data.type == "ORTHO") else box_base
        elev = cam.data.type == "ORTHO" and "Elevation" in cam.name  # elevations omit vegetation (drawing convention)
        veg = [o for o in scene.objects if o.name.startswith("Botanical_") and not o.hide_render] if elev else []
        if cam.data.type == "ORTHO":  # no gobo dapple or distant hills on technical views
            veg += [o for o in scene.objects if o.name in ("Light_Gobo_Canopy", "Ground_Hills") and not o.hide_render]
        for o in veg:
            o.hide_render = True
        scene.render.filepath = os.path.join(out_dir, f"{cam.name}.png")
        bpy.ops.render.render(write_still=True)
        paths.append(scene.render.filepath)
        for o in veg:
            o.hide_render = False
    for c in zone_colls:
        c.hide_render = base[c.name]
    scene.view_settings.exposure = exp0
    for o in constr:
        o.hide_render = constr_base[o.name]
    if box:
        box.hide_render = box_base
    return paths


def distant_hills(spec, coll, tex_dir, r0=260.0, r1=520.0, h=38.0, seed=3):
    """Low rolling hills ring beyond the site plate (aerial horizon), moss/forest-toned, hazed by the atmosphere box."""
    from mathutils import noise
    bm = bmesh.new()
    nr, na = 24, 160
    rows = []
    for i in range(nr + 1):
        r = r0 + (r1 - r0) * i / nr
        row = []
        for j in range(na):
            a = 2 * math.pi * j / na
            x, y = r * math.cos(a), r * math.sin(a)
            fall = math.sin(math.pi * i / nr) ** 0.8
            z = max(0.0, h * fall * (0.55 + 0.45 * noise.noise(Vector((x / 140 + seed, y / 140, 0.0)))) - 2.0)
            row.append(bm.verts.new((x, y, z)))
        rows.append(row)
    for i in range(nr):
        for j in range(na):
            bm.faces.new([rows[i][j], rows[i][(j + 1) % na], rows[i + 1][(j + 1) % na], rows[i + 1][j]])
    me = bpy.data.meshes.new("Ground_Hills"); bm.to_mesh(me); bm.free()
    for poly in me.polygons:
        poly.use_smooth = True
    ob = bpy.data.objects.new("Ground_Hills", me); coll.objects.link(ob)
    ob.data.materials.append(emission_free_material("MAT_Ground_Hills", tuple(spec.get("hills_rgb", (0.07, 0.1, 0.05))), rough=0.95))
    return ob


def horizon_disc(spec, coll):
    """Far ground to the horizon (spec["horizon"] = {"radius_m", "z", "near_rgb", "far_rgb", "fade_m": [d0, d1]}): one big disc
    whose colour fades from the ground colour to the haze colour with distance, so no world edge or bright band ever shows
    between the context and the sky. Built after the atmosphere box (it must not inflate the volume bounds)."""
    h = spec.get("horizon")
    if not h:
        return None
    R = float(h.get("radius_m", 60000.0)); n = 128
    bm = bmesh.new()
    c = bm.verts.new((0, 0, float(h.get("z", -0.7))))
    ring = [bm.verts.new((R * math.cos(2 * math.pi * k / n), R * math.sin(2 * math.pi * k / n), float(h.get("z", -0.7)))) for k in range(n)]
    for k in range(n):
        bm.faces.new((c, ring[k], ring[(k + 1) % n]))
    me = bpy.data.meshes.new("Ground_Horizon_Disc"); bm.to_mesh(me); bm.free()
    ob = bpy.data.objects.new("Ground_Horizon_Disc", me); coll.objects.link(ob)
    mat = bpy.data.materials.new("MAT_Ground_Horizon"); nt = mat.node_tree; N, L = nt.nodes, nt.links; N.clear()
    o = N.new("ShaderNodeOutputMaterial"); b = N.new("ShaderNodeBsdfPrincipled"); b.inputs["Roughness"].default_value = 0.95
    g = N.new("ShaderNodeNewGeometry"); vl = N.new("ShaderNodeVectorMath"); vl.operation = "LENGTH"
    mr = N.new("ShaderNodeMapRange"); d0, d1 = h.get("fade_m", [3000.0, 25000.0])
    mr.inputs["From Min"].default_value, mr.inputs["From Max"].default_value = d0, d1
    ramp = N.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*h.get("near_rgb", (0.32, 0.31, 0.30)), 1)
    ramp.color_ramp.elements[1].color = (*h.get("far_rgb", (0.70, 0.74, 0.80)), 1)
    L.new(g.outputs["Position"], vl.inputs[0]); L.new(vl.outputs["Value"], mr.inputs["Value"])
    L.new(mr.outputs["Result"], ramp.inputs[0]); L.new(ramp.outputs["Color"], b.inputs["Base Color"])
    L.new(b.outputs[0], o.inputs["Surface"]); me.materials.append(mat)
    ob.visible_shadow = False
    return ob
