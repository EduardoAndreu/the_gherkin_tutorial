"""2-D linework from triangle meshes for the blueprint sheets (numpy only): plane cuts and feature edges."""
import math
import numpy as np


def cut_segments(V, T, axis, value):
    """Segments where triangles cross the plane {axis = value}. Returns (s, 2, 3) world-space endpoints."""
    if len(T) == 0:
        return np.zeros((0, 2, 3))
    P = V[T]                                   # (m, 3, 3)
    d = P[:, :, axis] - value                  # signed distance per vertex
    sgn = d > 0
    cross = (sgn.any(1)) & (~sgn).any(1)
    P, d = P[cross], d[cross]
    if len(P) == 0:
        return np.zeros((0, 2, 3))
    segs = []
    for (i, j) in ((0, 1), (1, 2), (2, 0)):
        di, dj = d[:, i], d[:, j]
        hit = (di > 0) != (dj > 0)
        t = np.where(hit, di / np.where(di - dj == 0, 1e-12, di - dj), 0)
        pts = P[:, i] + (P[:, j] - P[:, i]) * t[:, None]
        segs.append(np.where(hit[:, None], pts, np.nan))
    S = np.stack(segs, 1)                      # (m, 3, 3) with NaN where no hit
    ok = ~np.isnan(S[:, :, 0])
    out = np.zeros((len(S), 2, 3))
    for r in range(len(S)):
        pts = S[r][ok[r]]
        out[r] = pts[:2] if len(pts) >= 2 else np.nan
    return out[~np.isnan(out[:, 0, 0])]


def feature_edges(V, T, angle_deg=25.0):
    """Edges that are boundary edges or whose dihedral angle exceeds angle_deg. Returns (e, 2) vertex index pairs."""
    if len(T) == 0:
        return np.zeros((0, 2), dtype=np.int64)
    E = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    Es = np.sort(E, 1)
    key = Es[:, 0] * (len(V) + 1) + Es[:, 1]
    order = np.argsort(key, kind="stable")
    key_s, E_s = key[order], Es[order]
    tri_of = np.tile(np.arange(len(T)), 3)[order]
    uniq, start, counts = np.unique(key_s, return_index=True, return_counts=True)
    n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    cos_lim = math.cos(math.radians(angle_deg))
    keep = []
    for s, c in zip(start, counts):
        if c == 1:
            keep.append(E_s[s]); continue
        if c == 2:
            a, b = tri_of[s], tri_of[s + 1]
            if (n[a] @ n[b]) < cos_lim:
                keep.append(E_s[s])
        else:
            keep.append(E_s[s])
    return np.array(keep, dtype=np.int64).reshape(-1, 2)


def project(points3, kind, **kw):
    """World points -> drawing (u, w). kind: 'plan' (u=X, w=Y) | 'section_y' (u=X, w=Z) | 'section_x' (u=Y, w=Z)
    | 'elevation' with right=(rx, ry) unit vector (u = p . right, w = Z)."""
    p = np.asarray(points3, float)
    if kind == "plan":
        return p[..., :2]
    if kind == "section_y":
        return p[..., [0, 2]]
    if kind == "section_x":
        return p[..., [1, 2]]
    if kind == "elevation":
        rx, ry = kw["right"]
        return np.stack([p[..., 0] * rx + p[..., 1] * ry, p[..., 2]], -1)
    raise ValueError(kind)


def elevation_right(azimuth_deg=None, psi_deg=None):
    """Drawing 'right' direction for an elevation. azimuth_deg: compass of the VIEWER (0 = north/+Y, 90 = east/+X),
    looking at the origin. psi_deg: the markup convention u = r sin(theta - psi), theta anticlockwise from +X."""
    if psi_deg is not None:
        p = math.radians(psi_deg); return (-math.sin(p), math.cos(p))
    a = math.radians(azimuth_deg or 0.0)
    return (-math.cos(a), math.sin(a))


def edges_to_segments(V, E):
    return V[E]  # (e, 2, 3)
