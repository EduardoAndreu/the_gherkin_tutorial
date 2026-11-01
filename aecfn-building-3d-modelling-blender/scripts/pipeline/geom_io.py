"""Geometry interchange between Stage 01 (system Python) and Blender: npz files + a manifest. numpy only.

build/solids/<zone>/<name>.npz      verts (n,3) tris (m,3)                       manifold CSG solids (world coords)
build/meshes/<zone>/<name>.npz      verts, face_idx (flat), face_len, tris        parametric meshes (world coords)
build/instances/<zone>/<name>.npz   verts, face_idx, face_len, tris (LOCAL), transforms (k,4,4)   linked duplicates
build/geom/manifest.json            {name: {zone, kind, owner, bbox, tris, instances, material, uv, imprint}}
"""
import glob, json, os
import numpy as np
from pipeline.aep_spec import out


def faces_to_flat(faces):
    idx = np.array([i for f in faces for i in f], dtype=np.int64)
    ln = np.array([len(f) for f in faces], dtype=np.int64)
    return idx, ln


def flat_to_faces(idx, ln):
    faces, p = [], 0
    for n in ln:
        faces.append([int(i) for i in idx[p:p + n]]); p += n
    return faces


def triangulate(faces):
    t = []
    for f in faces:
        for i in range(1, len(f) - 1):
            t.append([f[0], f[i], f[i + 1]])
    return np.array(t, dtype=np.int64).reshape(-1, 3)


def validate_mesh(name, V, faces):
    V = np.asarray(V, dtype=np.float64)
    if V.ndim != 2 or V.shape[1] != 3 or len(V) == 0:
        raise SystemExit(f"[geom] {name}: verts must be (n,3), got {V.shape}")
    if not np.isfinite(V).all():
        raise SystemExit(f"[geom] {name}: non-finite vertex coordinates")
    if not faces:
        raise SystemExit(f"[geom] {name}: no faces")
    mx = max(max(f) for f in faces); mn = min(min(f) for f in faces)
    if mn < 0 or mx >= len(V):
        raise SystemExit(f"[geom] {name}: face index out of range ({mn}..{mx} of {len(V)})")
    if any(len(f) < 3 for f in faces):
        raise SystemExit(f"[geom] {name}: face with < 3 vertices")
    return V


def save_mesh(path, V, faces, transforms=None):
    idx, ln = faces_to_flat(faces)
    kw = dict(verts=np.asarray(V, np.float64), face_idx=idx, face_len=ln, tris=triangulate(faces))
    if transforms is not None:
        kw["transforms"] = np.asarray(transforms, np.float64).reshape(-1, 4, 4)
    np.savez(path, **kw)


def load_all(spec, zones=None, kinds=("solids", "meshes", "instances")):
    """-> list of dict(name, zone, kind, verts, tris, faces?, transforms?) in WORLD coords (instances keep local + transforms)."""
    items = []
    for kind in kinds:
        for zone in (zones or spec["zones"]):
            for f in sorted(glob.glob(os.path.join(spec["_root"], "build", kind, zone, "*.npz"))):
                a = np.load(f)
                it = dict(name=os.path.splitext(os.path.basename(f))[0], zone=zone, kind=kind, verts=a["verts"], tris=a["tris"], path=f)
                if "face_idx" in a:
                    it["faces"] = flat_to_faces(a["face_idx"], a["face_len"])
                if "transforms" in a:
                    it["transforms"] = a["transforms"]
                items.append(it)
    return items


def expand_instances(it):
    """World-space (verts, tris) of every instance as one big array pair (for drawings and measurements)."""
    V, T, M = it["verts"], it["tris"], it["transforms"]
    V4 = np.c_[V, np.ones(len(V))]
    W = np.einsum("kij,nj->kni", M, V4)[:, :, :3]          # (k, n, 3)
    k, n = W.shape[:2]
    allV = W.reshape(-1, 3)
    allT = (T[None, :, :] + (np.arange(k) * n)[:, None, None]).reshape(-1, 3)
    return allV, allT


def world_bbox(it):
    if "transforms" in it:
        V, _ = expand_instances(it)
    else:
        V = it["verts"]
    return V.min(0).tolist(), V.max(0).tolist()


def manifest_path(spec):
    return out(spec, "geom", "manifest.json")


def load_manifest(spec):
    p = manifest_path(spec)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {}
