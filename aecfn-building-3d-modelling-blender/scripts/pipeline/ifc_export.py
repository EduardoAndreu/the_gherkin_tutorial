"""IFC4 export: one file per discipline (spec["ifc_export"]["files"]), real IFC classes from the object name's part word,
storeys from the measured levels, one IfcMaterial per render material with an IfcSurfaceStyle (colour + transparency)
so viewers (Dalux, BIMcollab, Solibri) shade objects like the render, and IfcMappedItem for instances.

Colours come from build/geom/ifc_styles.json (pipeline/ifc_styles.py, run inside Blender on the compiled scene:
object -> material, material -> rendered swatch colour); spec ifc_export.styles overrides per material (glass).
Usage: python3 -m pipeline.ifc_export   (or `python3 -m pipeline.run ifc` which refreshes the styles first)
"""
import fnmatch, json, os, re, sys, time, uuid
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from pipeline.aep_spec import load_spec, out  # noqa: E402
from pipeline import geom_io as GIO  # noqa: E402

# (regex on the part words after D<n>_<ZONE>_, IFC class, PredefinedType); first match wins
CLASS_RULES = [
    (r"^Context", "IfcBuildingElementProxy", None),   # neighbouring buildings: massing shells + floors
    (r"Casing|Cladding|Soffit|Ceiling|Fascia|Nosing|Finish|Spandrel|Liner|Skirt|Collar", "IfcCovering", None),
    (r"Balustrade|Handrail|Rail|StairGlass|Guard", "IfcRailing", None),
    (r"Door", "IfcDoor", None),
    (r"Landing", "IfcSlab", "LANDING"),
    (r"Wall|Shaft|Core(?!Stair)", "IfcWall", None),
    (r"Stair|Tread|Stringer|Flight", "IfcStair", None),
    (r"Ramp", "IfcRamp", None),
    (r"Slab|Deck|Topping|Plinth", "IfcSlab", "FLOOR"),
    (r"Leg|Column|Mast|Post|Pier", "IfcColumn", None),
    (r"Diagonal|DomeMember|Brace|Node|Bracket|Clamp|Shoe|Stub|Strut", "IfcMember", None),
    (r"Beam|Tie|Ring|Joist|Purlin|Girder", "IfcBeam", None),
    (r"Mullion|Transom|Frame|Fin|Nose|Rib|Gantry", "IfcMember", "MULLION"),
    (r"Louvre|Louver|Blind|Shade", "IfcShadingDevice", None),
    (r"Glass|Glazing|Pane|Skylight|Lens|InnerScreen(?!Opening)", "IfcPlate", "CURTAIN_PANEL"),
    (r"Intake|Extract|Vent|Diffuser", "IfcAirTerminal", None),
    (r"Floor|Plenum|Grille|Pedestal|Carpet", "IfcCovering", "FLOORING"),
    (r"Duct", "IfcDuctSegment", None),
    (r"(?i)light|lamp", "IfcLightFixture", None),
    (r"Ground|Terrain|Tree|Shrub|Soil|Foliage|Leaf|Grass|Hedge|Clump", "IfcGeographicElement", None),
    (r"Table|Chair|Desk|Counter|Bench|Planter|Furniture|Seat|Bollard|Bin", "IfcFurniture", None),
    (r"Plaza|Paving|Road|Kerb|Pavement|Footway|Step", "IfcSlab", None),
]
PREDEF_OK = {"IfcSlab": {"FLOOR", "LANDING", "ROOF", "BASESLAB"}, "IfcCovering": {"FLOORING", "CEILING", "CLADDING"},
             "IfcMember": {"MULLION", "BRACE"}, "IfcPlate": {"CURTAIN_PANEL", "SHEET"}}
GLASS_HINT = re.compile(r"glass", re.I)


def ifc_class(name):
    part = name.split("_", 2)[2] if name.count("_") >= 2 else name
    for rx, cls, pdt in CLASS_RULES:
        if re.search(rx, part):
            if cls == "IfcCovering" and pdt is None:
                pdt = "CEILING" if re.search(r"Ceiling|Soffit", part) else "CLADDING"
            return cls, pdt
    return "IfcBuildingElementProxy", None


def _guid(*key):
    import ifcopenshell.guid
    return ifcopenshell.guid.compress(uuid.uuid5(uuid.NAMESPACE_URL, "aep:" + "/".join(map(str, key))).hex)


def _match(name, pats):
    return any(fnmatch.fnmatchcase(name, p) for p in pats)


def discipline_of(spec, it, cls):
    cfg = spec.get("ifc_export", {})
    for fname, rule in cfg.get("files", {}).items():
        if rule.get("rest"):
            continue
        if cls in rule.get("exclude_classes", []) or _match(it["name"], rule.get("exclude_names", [])):
            continue
        if it["zone"] in rule.get("zones", []) or _match(it["name"], rule.get("include_names", [])):
            return fname
    return next((f for f, r in cfg.get("files", {}).items() if r.get("rest")), "site.ifc")


def load_styles(spec):
    p = out(spec, "geom", "ifc_styles.json")
    st = json.load(open(p)) if os.path.exists(p) else {"objects": {}, "materials": {}}
    objs = st.get("objects", {})
    stripped = {}
    for k, v in objs.items():
        stripped.setdefault(re.sub(r"_(i\d+|\d+)$", "", k), v)
    mats = dict(st.get("materials", {}))
    for k, v in spec.get("ifc_export", {}).get("styles", {}).items():
        mats[k] = {**mats.get(k, {}), **v}
    return objs, stripped, mats


def material_of(it, manifest, objs, stripped):
    n = it["name"]
    m = objs.get(n) or objs.get(n + "_i0000") or objs.get(n + "_0") or stripped.get(n) or manifest.get(n, {}).get("material")
    return re.sub(r"^MAT_", "", m) if m else ("glass_clear" if GLASS_HINT.search(n) else "default")


class Writer:
    def __init__(self, spec, label):
        import ifcopenshell
        self.f = f = ifcopenshell.file(schema="IFC4")
        self.spec, self.label = spec, label
        units = [f.createIfcSIUnit(None, "LENGTHUNIT", None, "METRE"), f.createIfcSIUnit(None, "AREAUNIT", None, "SQUARE_METRE"),
                 f.createIfcSIUnit(None, "VOLUMEUNIT", None, "CUBIC_METRE"), f.createIfcSIUnit(None, "PLANEANGLEUNIT", None, "RADIAN")]
        self.o = f.createIfcCartesianPoint((0.0, 0.0, 0.0))
        self.ax = f.createIfcAxis2Placement3D(self.o, None, None)
        ctx = f.createIfcGeometricRepresentationContext(None, "Model", 3, 1e-5, self.ax, f.createIfcDirection((0.0, 1.0)))
        self.body = f.createIfcGeometricRepresentationSubContext("Body", "Model", None, None, None, None, ctx, None, "MODEL_VIEW", None)
        self.place = f.createIfcLocalPlacement(None, self.ax)
        self.proj = f.createIfcProject(_guid("project"), None, spec["project"], None, None, spec.get("title"), None, [ctx], f.createIfcUnitAssignment(units))
        self.site = f.createIfcSite(_guid("site"), None, "Site", None, None, self.place, None, None, "ELEMENT", None, None, None, None, None)
        self.bldg = f.createIfcBuilding(_guid("building"), None, spec.get("title", spec["project"]), None, None, self.place, None, None, "ELEMENT", None, None, None)
        f.createIfcRelAggregates(_guid("agg", "proj"), None, None, None, self.proj, [self.site])
        f.createIfcRelAggregates(_guid("agg", "site"), None, None, None, self.site, [self.bldg])
        self.storeys = []
        for el, nm in storeys_of(spec):
            s = f.createIfcBuildingStorey(_guid("storey", nm), None, str(nm), None, None, self.place, None, None, "ELEMENT", float(el))
            self.storeys.append((el, s))
        f.createIfcRelAggregates(_guid("agg", "bldg"), None, None, None, self.bldg, [s for _, s in self.storeys])
        self.contained = {}
        self.mat_rel, self.mats, self.styles, self.psets, self.maps = {}, {}, {}, {}, {}
        self.n = 0

    def style(self, key, mats):
        if key in self.styles:
            return self.styles[key]
        f = self.f
        s = mats.get(key, {})
        rgb = [float(np.clip(c, 0, 1)) for c in s.get("rgb", (0.7, 0.7, 0.7))]
        tr = float(s.get("transparency", 0.0))
        col = f.createIfcColourRgb(None, *rgb)
        meth = "GLASS" if tr > 0 else ("METAL" if s.get("metallic", 0) > 0.5 else "NOTDEFINED")
        rend = f.createIfcSurfaceStyleRendering(col, tr, None, None, None, None, None, None, meth)
        st = f.createIfcSurfaceStyle(key, "BOTH", [rend])
        mat = f.createIfcMaterial(key, s.get("describe"), s.get("category"))
        f.createIfcMaterialDefinitionRepresentation(None, None, [f.createIfcStyledRepresentation(self.body, None, None, [f.createIfcStyledItem(None, [st], None)])], mat)
        self.styles[key], self.mats[key] = st, mat
        return st

    def faceset(self, V, it, key, mats):
        f = self.f
        pts = f.createIfcCartesianPointList3D(np.round(V, 4).tolist())
        faces = it.get("faces")
        if faces is not None and all(len(p) >= 3 for p in faces):
            fs = f.createIfcPolygonalFaceSet(pts, None, [f.createIfcIndexedPolygonalFace([int(i) + 1 for i in p]) for p in faces], None)
        else:
            fs = f.createIfcTriangulatedFaceSet(pts, None, None, (np.asarray(it["tris"], int) + 1).tolist(), None)
        f.createIfcStyledItem(fs, [self.style(key, mats)], None)
        return fs

    def storey(self, zmin):
        return max([s for s in self.storeys if s[0] <= zmin + 1e-6] or self.storeys[:1], key=lambda s: s[0])[1]

    def product(self, name, cls, pdt, rep_items, rep_type, zmin, key, props, gid):
        f = self.f
        shape = f.createIfcProductDefinitionShape(None, None, [f.createIfcShapeRepresentation(self.body, "Body", rep_type, rep_items)])
        el = f.create_entity(cls, GlobalId=gid, Name=name, ObjectPlacement=self.place, Representation=shape)
        if pdt and pdt in PREDEF_OK.get(cls, ()):
            el.PredefinedType = pdt
        self.contained.setdefault(self.storey(zmin), []).append(el)
        self.mat_rel.setdefault(key, []).append(el)
        self.psets.setdefault(props, []).append(el)
        self.n += 1
        return el

    def add(self, it, cls, pdt, key, mats, zone, owner):
        f = self.f
        props = (zone, owner, key, self.label, cls)
        if it["kind"] != "instances":
            V = it["verts"]
            self.product(it["name"], cls, pdt, [self.faceset(V, it, key, mats)], "Tessellation", float(V[:, 2].min()), key, props, _guid(it["name"]))
            return
        M = np.asarray(it["transforms"])
        A = M[:, :3, :3]; s = np.linalg.norm(A, axis=1); R = A / s[:, None, :]
        ok = (np.abs(np.einsum("kij,kil->kjl", R, R) - np.eye(3)).max(axis=(1, 2)) < 1e-6) & (np.linalg.det(A) > 0)
        rmap = None
        for k in range(len(M)):
            nm = f"{it['name']}_i{k:04d}"
            if ok[k]:
                if rmap is None:
                    fs = self.faceset(it["verts"], it, key, mats)
                    rmap = f.createIfcRepresentationMap(self.ax, f.createIfcShapeRepresentation(self.body, "Body", "Tessellation", [fs]))
                d = lambda v: f.createIfcDirection([float(x) for x in v])
                t = M[k, :3, 3]
                if np.allclose(s[k], s[k][0], rtol=1e-9):
                    op = f.createIfcCartesianTransformationOperator3D(d(R[k][:, 0]), d(R[k][:, 1]), f.createIfcCartesianPoint([float(x) for x in t]), float(s[k][0]), d(R[k][:, 2]))
                else:
                    op = f.createIfcCartesianTransformationOperator3DnonUniform(d(R[k][:, 0]), d(R[k][:, 1]), f.createIfcCartesianPoint([float(x) for x in t]),
                                                                                float(s[k][0]), d(R[k][:, 2]), float(s[k][1]), float(s[k][2]))
                zmin = float((it["verts"] @ A[k].T + t)[:, 2].min())
                self.product(nm, cls, pdt, [f.createIfcMappedItem(rmap, op)], "MappedRepresentation", zmin, key, props, _guid(nm))
            else:  # shear / mirror: explicit geometry for this instance
                W = it["verts"] @ A[k].T + M[k, :3, 3]
                self.product(nm, cls, pdt, [self.faceset(W, it, key, mats)], "Tessellation", float(W[:, 2].min()), key, props, _guid(nm))

    def finish(self, path):
        f = self.f
        for st, els in self.contained.items():
            f.createIfcRelContainedInSpatialStructure(_guid("cont", self.label, st.Name), None, None, None, els, st)
        for key, els in self.mat_rel.items():
            f.createIfcRelAssociatesMaterial(_guid("mat", self.label, key), None, None, None, els, self.mats[key])
        for (zone, owner, key, label, cls), els in self.psets.items():
            vals = {"Zone": zone, "Owner": owner, "Material": key, "Discipline": label, "IfcClass": cls}
            ps = f.createIfcPropertySet(_guid("pset", label, zone, owner, key, cls), None, "AEP_Common", None,
                                        [f.createIfcPropertySingleValue(k, None, f.createIfcLabel(str(v)), None) for k, v in vals.items()])
            f.createIfcRelDefinesByProperties(_guid("rdp", label, zone, owner, key, cls), None, None, None, els, ps)
        f.write(path)


def storeys_of(spec):
    from pipeline.specq import q, num, fmt
    from pipeline.aep_spec import datums_matching
    st, res = spec.get("storeys"), []
    if st:
        for it in q(spec["_src"], st["each"]):
            if isinstance(it, dict):
                el = num(q(it, st["elevation"], None))
                if el is not None:
                    res.append((el, fmt(st["name"], it)))
    else:
        res = [(z, n) for n, z in datums_matching(spec, spec.get("sheet_datums", "*"))]
    return sorted(res)


def write_ifcs(spec, items=None):
    t = time.time()
    items = items if items is not None else GIO.load_all(spec)
    manifest = GIO.load_manifest(spec)
    objs, stripped, mats = load_styles(spec)
    cfg = spec.get("ifc_export", {})
    excl = cfg.get("exclude_names", []) + list(spec.get("construction_only", []))
    writers, summary = {}, {}
    for it in items:
        if _match(it["name"], excl):
            continue
        cls, pdt = ifc_class(it["name"])
        fname = discipline_of(spec, it, cls)
        w = writers.get(fname) or writers.setdefault(fname, Writer(spec, cfg.get("files", {}).get(fname, {}).get("label", fname)))
        key = material_of(it, manifest, objs, stripped)
        w.add(it, cls, pdt, key, mats, it["zone"], manifest.get(it["name"], {}).get("owner", ""))
        summary.setdefault(fname, {}).setdefault(cls, 0)
        summary[fname][cls] += 1 if it["kind"] != "instances" else len(it["transforms"])
    for fname, w in writers.items():
        p = out(spec, "blueprint", fname)
        w.finish(p)
        print(f"[ifc] {fname}: {w.n} products, {len(w.storeys)} storeys, {len(w.mats)} materials, {os.path.getsize(p)/1e6:.1f} MB")
    json.dump(summary, open(out(spec, "blueprint", "ifc_summary.json"), "w"), indent=1)
    print(f"[ifc] done in {time.time()-t:.1f}s")
    return summary


if __name__ == "__main__":
    write_ifcs(load_spec())
