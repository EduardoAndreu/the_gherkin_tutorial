"""Zone module template. Copy to zones/<zone>.py. Owner: <specialist role>. Only the owner edits this file.

Every number comes from the measured spec (src/srcnum) or a named datum. If the spec does not have a value the model
needs (a thickness, a member size, a cavity depth) it is an ASSUMPTION: declare it in ASSUMPTIONS below, use it only
through A("..."), hang it DOWN from the governing datum (e.g. slab depth below SSL) and never move a measured level.
`python3 -m pipeline.run decisions` fails until every assumption and every open issue has a line in decisions_log.md.

Contract (enforced by Stage 01 / 01c / measure / QA):
  solids(spec)     -> {name: manifold3d.Manifold}             CSG only for slabs, cores, voids, bases, plinths, section cuts
  meshes(spec)     -> {name: (verts, faces) | dict(mesh=(verts, faces), material=key, uv="box"|"axis", imprint=False)}
                      parametric detail: members, mullions, panels, rails, fins (lib_geom.sweep/tube/panel ...)
  instances(spec)  -> {name: dict(mesh=(verts, faces), transforms=[4x4, ...], material=key)}
                      repeated detail compiled as linked duplicates (one mesh datablock, thousands of objects)
  materials(spec)  -> {substring_of_object_name: material_key}  (meshes/instances may also name their material directly)
  dress(ctx)       -> optional bpy-side detail that is not geometry from the generators (lights, props, people-free)
All coordinates zone-local, metres, +Z up; the pipeline adds spec["zones"][zone]["origin"].
Names: D3_<ZONE>_<Part>[_<Variant>] (QA-enforced). Instances get _i0001.. appended automatically.
Never import manifold3d at module top level (Blender's Python does not have it).
"""
import numpy as np
from pipeline.aep_spec import datum, src, srcnum
from zones import lib_geom as G

ZONE = "<ZONE>"           # e.g. "STR" — used in object names: D3_STR_...

# id -> value, key, hung_from (datum or spec key the value is measured from), reason. Logged in decisions_log.md.
ASSUMPTIONS = {
    # "A-STR-01": {"key": "assumed_slab_depth_m", "value": 0.15, "hung_from": "SSL (down)", "reason": "not on any sheet; typical composite deck"},
}


def A(aid):
    return ASSUMPTIONS[aid]["value"]


def solids(spec):
    import manifold3d as m3  # noqa: F401  (import here, never at module top)
    s = {}
    # Example: a slab whose TOP is the measured SSL and whose depth is an assumption hung down from it
    # ssl = srcnum(spec, "levels[level=1].ssl_m")           # None if "unknown" -> handle explicitly
    # s[f"D3_{ZONE}_Slab_L1"] = G.disc(r=12.0, z0=ssl - A("A-STR-01"), z1=ssl)
    return s


def meshes(spec):
    m = {}
    # m[f"D3_{ZONE}_Handrail"] = dict(mesh=G.tube(path, 0.025), material="stainless", uv="axis")
    return m


def instances(spec):
    i = {}
    # proto = G.box_beam(G.segment([0,0,0],[0,0,4.0]), 0.08, 0.15)
    # i[f"D3_{ZONE}_Mullion"] = dict(mesh=proto, transforms=G.ring_transforms(72, r=25.0, z=0.0), material="aluminium")
    return i


def materials(spec):
    return {"Slab": "concrete"}


def dress(ctx):
    # ctx = dict(spec, scene, coll, B (pipeline.aep_blender), tex_dir, final, zone, origin)
    return
