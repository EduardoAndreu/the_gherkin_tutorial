"""lib_geom — parametric geometry kit for zone modules. numpy only at module top (safe inside Blender);
manifold3d is imported inside the functions that need it.

Two mesh currencies:
  Manifold (manifold3d.Manifold)      watertight CSG solids -> zone.solids()   (slabs, cores, voids, plinths, section cuts)
  Mesh = (verts: (n,3) float array, faces: list[list[int]])  -> zone.meshes() / zone.instances()
                                       (tubes, swept profiles, panels, lattices: detail that CSG does badly or slowly)
Everything is metres, +Z up, zone-local coordinates (the pipeline adds the zone origin).
"""
import math
import numpy as np

TAU = 2 * math.pi


# ------------------------------------------------------------------ small helpers
def polar(theta_deg, r, z=0.0):
    """(x, y, z) from an angle in degrees anticlockwise from +X."""
    t = math.radians(theta_deg)
    return np.array([r * math.cos(t), r * math.sin(t), z], dtype=float)


def interp_profile(samples, zkey="z", rkey="r"):
    """samples: list of dicts or (z, r) pairs -> r(z) by linear interpolation (clamped). Items with r=None are skipped.
    Use for envelope radius profiles: r = interp_profile([(z, r), ...]); r(z)."""
    pts = []
    for s in samples:
        if isinstance(s, dict):
            z, r = s.get(zkey), s.get(rkey)
        else:
            z, r = s
        try:
            z, r = float(z), float(r)
        except (TypeError, ValueError):
            continue
        pts.append((z, r))
    pts.sort()
    zs = np.array([p[0] for p in pts]); rs = np.array([p[1] for p in pts])
    if len(zs) < 2:
        raise ValueError("interp_profile needs >= 2 numeric samples")
    return lambda z: float(np.interp(z, zs, rs))


def circle2d(r, n=72, start_deg=0.0):
    a = np.radians(start_deg) + np.arange(n) * TAU / n
    return np.c_[r * np.cos(a), r * np.sin(a)]


def merge(*meshes):
    """Concatenate meshes (verts, faces) -> one mesh."""
    vs, fs, off = [], [], 0
    for v, f in meshes:
        v = np.asarray(v, dtype=float)
        vs.append(v); fs += [[i + off for i in face] for face in f]; off += len(v)
    return (np.vstack(vs) if vs else np.zeros((0, 3))), fs


def transform(mesh, M):
    """Apply a 4x4 matrix to a mesh."""
    v, f = mesh
    v4 = np.c_[v, np.ones(len(v))] @ np.asarray(M, dtype=float).T
    return v4[:, :3], f


def mat_rt(rot_z_deg=0.0, t=(0, 0, 0), rot_x_deg=0.0, rot_y_deg=0.0, scale=1.0):
    """4x4 matrix: scale, then rotate X, Y, Z (degrees), then translate."""
    def rx(a):
        c, s = math.cos(a), math.sin(a); return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])

    def ry(a):
        c, s = math.cos(a), math.sin(a); return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])

    def rz(a):
        c, s = math.cos(a), math.sin(a); return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    R = rz(math.radians(rot_z_deg)) @ ry(math.radians(rot_y_deg)) @ rx(math.radians(rot_x_deg)) * scale
    M = np.eye(4); M[:3, :3] = R; M[:3, 3] = t
    return M


def frame_matrix(origin, z_axis, x_hint=None):
    """4x4 placing local +Z along `z_axis` at `origin` (for instancing a prototype built along +Z)."""
    z = np.asarray(z_axis, float); z = z / np.linalg.norm(z)
    h = np.asarray(x_hint if x_hint is not None else ([1, 0, 0] if abs(z[0]) < 0.9 else [0, 1, 0]), float)
    x = h - z * (h @ z); x /= np.linalg.norm(x); y = np.cross(z, x)
    M = np.eye(4); M[:3, 0], M[:3, 1], M[:3, 2], M[:3, 3] = x, y, z, origin
    return M


# ------------------------------------------------------------------ sweeps and tubes
def _frames(path, up=None):
    """Parallel-transport frames along a polyline: returns tangents, normals, binormals (n,3)."""
    P = np.asarray(path, float)
    n = len(P)
    T = np.zeros_like(P)
    T[1:-1] = P[2:] - P[:-2]; T[0] = P[1] - P[0]; T[-1] = P[-1] - P[-2]
    T /= np.maximum(np.linalg.norm(T, axis=1, keepdims=True), 1e-12)
    N = np.zeros_like(P)
    u0 = np.asarray(up if up is not None else ([0, 0, 1] if abs(T[0][2]) < 0.9 else [1, 0, 0]), float)
    N[0] = u0 - T[0] * (u0 @ T[0]); N[0] /= np.linalg.norm(N[0])
    for i in range(1, n):
        b = np.cross(T[i - 1], T[i])
        if np.linalg.norm(b) < 1e-9:
            N[i] = N[i - 1]
        else:
            b /= np.linalg.norm(b)
            ang = math.atan2(np.linalg.norm(np.cross(T[i - 1], T[i])), T[i - 1] @ T[i])
            c, s = math.cos(ang), math.sin(ang)
            v = N[i - 1]
            N[i] = v * c + np.cross(b, v) * s + b * (b @ v) * (1 - c)
        N[i] -= T[i] * (N[i] @ T[i]); N[i] /= np.linalg.norm(N[i])
    B = np.cross(T, N)
    return T, N, B


def sweep(profile2d, path, up=None, closed_profile=True, cap=True, scale=1.0):
    """Sweep a 2-D profile (n,2) (metres; use scale=0.001 for the brief's profiles in mm) along a 3-D polyline.
    Profile x -> normal, y -> binormal. Returns a Mesh with quads (and end caps when the profile is closed)."""
    prof = np.asarray(profile2d, float) * scale
    if closed_profile and np.allclose(prof[0], prof[-1]):
        prof = prof[:-1]
    P = np.asarray(path, float)
    T, N, B = _frames(P, up)
    k = len(prof); rings = []
    for i in range(len(P)):
        rings.append(P[i] + np.outer(prof[:, 0], N[i]) + np.outer(prof[:, 1], B[i]))
    V = np.vstack(rings); F = []
    m = len(P)
    for i in range(m - 1):
        for j in range(k if closed_profile else k - 1):
            a, b = i * k + j, i * k + (j + 1) % k
            F.append([a, b, b + k, a + k])
    if cap and closed_profile:
        F.append(list(range(k - 1, -1, -1)))
        F.append([(m - 1) * k + j for j in range(k)])
    return V, F


def tube(path, radius, segs=12, cap=True, up=None):
    """Round tube along a polyline (CHS members, handrails, pipes)."""
    return sweep(circle2d(radius, segs), path, up=up, closed_profile=True, cap=cap)


def box_beam(path, width, depth, up=None):
    """Rectangular section along a polyline: width across the normal, depth across the binormal (up by default)."""
    w, d = width / 2, depth / 2
    return sweep(np.array([[-w, -d], [w, -d], [w, d], [-w, d]]), path, up=up)


def segment(p0, p1, n=2):
    return np.linspace(np.asarray(p0, float), np.asarray(p1, float), n)


def helix_path(r_of_z, z0, z1, theta0_deg, dtheta_deg_per_m, n=24, r_offset=0.0):
    """Polyline climbing from z0 to z1 while turning dtheta per metre; radius from a profile function r(z)."""
    zs = np.linspace(z0, z1, n)
    return np.array([polar(theta0_deg + dtheta_deg_per_m * (z - z0), r_of_z(z) + r_offset, z) for z in zs])


# ------------------------------------------------------------------ panels and plates
def panel(poly3d, thickness, normal=None):
    """Flat plate: a planar polygon (n,3) extruded by `thickness` along its normal (centred). Glass, decks, plates."""
    P = np.asarray(poly3d, float)
    if normal is None:
        nrm = np.cross(P[1] - P[0], P[2] - P[0])
    else:
        nrm = np.asarray(normal, float)
    nrm = nrm / max(np.linalg.norm(nrm), 1e-12)
    n = len(P)
    A = P - nrm * thickness / 2; Bp = P + nrm * thickness / 2
    V = np.vstack([A, Bp])
    F = [list(range(n - 1, -1, -1)), [n + i for i in range(n)]]
    for i in range(n):
        j = (i + 1) % n
        F.append([i, j, n + j, n + i])
    return V, F


def grid_on_surface(f_xyz, u_vals, v_vals):
    """Quad grid over a parametric surface f(u, v) -> (x,y,z)."""
    V = np.array([f_xyz(u, v) for v in v_vals for u in u_vals]); nu = len(u_vals)
    F = [[j * nu + i, j * nu + i + 1, (j + 1) * nu + i + 1, (j + 1) * nu + i]
         for j in range(len(v_vals) - 1) for i in range(nu - 1)]
    return V, F


# ------------------------------------------------------------------ CSG helpers (manifold3d inside)
def prism(poly2d, z0, z1, holes=()):
    """Manifold: polygon (with holes) extruded from z0 to z1. Slabs with light-well cut-outs, cores, plinths."""
    import manifold3d as m3
    polys = [np.asarray(poly2d, float).tolist()] + [np.asarray(h, float).tolist() for h in holes]
    cs = m3.CrossSection(polys, m3.FillRule.EvenOdd)
    return m3.Manifold.extrude(cs, z1 - z0).translate([0, 0, z0])


def disc(r, z0, z1, n=144, holes=()):
    return prism(circle2d(r, n), z0, z1, holes)


def annulus(r_out, r_in, z0, z1, n=144, holes=()):
    return prism(circle2d(r_out, n), z0, z1, [circle2d(r_in, n)] + list(holes))


def revolve(rz_profile, segs=144):
    """Manifold of revolution about Z from a closed (r, z) profile (r >= 0). Domes, lenses, bowls, finials."""
    import manifold3d as m3
    pts = [[float(r), float(z)] for r, z in rz_profile]
    cs = m3.CrossSection([pts], m3.FillRule.EvenOdd)
    return m3.Manifold.revolve(cs, segs)


def mesh_from_manifold(man):
    mesh = man.to_mesh()
    V = np.asarray(mesh.vert_properties)[:, :3].astype(float)
    F = np.asarray(mesh.tri_verts).astype(int).tolist()
    return V, F


def manifold_from_mesh(mesh):
    """Mesh -> Manifold (must be watertight; triangulates faces as fans)."""
    import manifold3d as m3
    V, F = mesh
    tris = []
    for f in F:
        for i in range(1, len(f) - 1):
            tris.append([f[0], f[i], f[i + 1]])
    return m3.Manifold(m3.Mesh(vert_properties=np.asarray(V, np.float32), tri_verts=np.asarray(tris, np.uint32)))


# ------------------------------------------------------------------ arrays
def ring_transforms(n, r, z, theta0_deg=0.0, tilt_outward_deg=0.0, scale=1.0):
    """n matrices around a ring: prototype +X points outward (radial), +Z up; tilt rotates about the tangent."""
    out = []
    for k in range(n):
        th = theta0_deg + 360.0 * k / n
        M = mat_rt(rot_z_deg=th, t=polar(th, r, z), rot_y_deg=tilt_outward_deg, scale=scale)
        out.append(M)
    return out


def triangulate_fan(face):
    return [[face[0], face[i], face[i + 1]] for i in range(1, len(face) - 1)]


def tri_count(mesh):
    return sum(max(len(f) - 2, 0) for f in mesh[1])
