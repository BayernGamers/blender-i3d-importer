"""Parser for inline geometry embedded directly in the .i3d XML
(<Shapes><IndexedTriangleSet>...</IndexedTriangleSet></Shapes>), as opposed
to the usual binary payload in a sibling .i3d.shapes file.

Seen on FS15 base data and on any Giants-Editor-saved / mod .i3d that was
never re-exported through the binary-shapes pipeline. Format confirmed from
Farming Simulator 15/data/maps/models/objects/triggers/tyreTrackSystem.i3d:

    <IndexedTriangleSet name="..." shapeId="1" bvCenter="0 1 0" bvRadius="1.73">
      <Vertices count="24" normal="true" uv0="true" tangent="true">
        <v p="-1 0 1" n="0 -1 0" t0="1 0"/>      <!-- t0..t3 = UV1..UV4, c = color -->
      </Vertices>
      <Triangles count="12"><t vi="0 1 2"/></Triangles>   <!-- 0-based -->
      <Subsets count="1"><Subset firstVertex="0" numVertices="24" firstIndex="0" numIndices="36"/></Subsets>
    </IndexedTriangleSet>

We deliberately emit i3d_shapes_models.Shape / Spline objects (not MeshData
directly) so every downstream pass (shape_to_mesh_data, merge groups,
subsets -> material slots, skin) works unchanged, whether the geometry came
from the binary file or inline XML.

Kept dependency-free (no bpy) like i3d_xml_parser, so it can be unit tested
standalone.
"""

import xml.etree.ElementTree as ET
from typing import Optional

try:
    from .i3d_shapes_models import Shape, ShapeOptions, Subset, Triangle, UV, Vector3, Vector4, Spline
except ImportError:  # pragma: no cover  (standalone test convenience)
    from i3d_shapes_models import Shape, ShapeOptions, Subset, Triangle, UV, Vector3, Vector4, Spline


def _to_int(s, default=0):
    if s is None:
        return default
    try:
        return int(str(s).strip())
    except (ValueError, TypeError):
        return default


def _to_float(s, default=0.0):
    if s is None:
        return default
    try:
        return float(s)
    except (ValueError, TypeError):
        return default


def _to_bool(s) -> bool:
    return str(s).strip().lower() in ("1", "true")


def _floats(s, n):
    """Parse n whitespace-separated floats from s. Returns None if malformed."""
    if not s:
        return None
    parts = s.split()
    if len(parts) < n:
        return None
    try:
        return [float(p) for p in parts[:n]]
    except ValueError:
        return None


def parse_inline_shape(elem: ET.Element) -> Shape:
    """Decode an inline <IndexedTriangleSet> element into a Shape.

    Mirrors the field layout parse_shape_entity produces from the binary
    format, so shape_to_mesh_data() and every merge-group/skin pass work
    unchanged regardless of geometry source.
    """
    sh = Shape()
    sh.name = elem.get("name", "")
    sh.id = _to_int(elem.get("shapeId"), default=0)

    bv_center = _floats(elem.get("bvCenter"), 3)
    bv_radius = _to_float(elem.get("bvRadius"), None)
    if bv_center is not None and bv_radius is not None:
        sh.bounding_volume = Vector4(bv_center[0], bv_center[1], bv_center[2], bv_radius)

    vertices_elem = elem.find("Vertices")
    if vertices_elem is None:
        return sh

    has_normal = _to_bool(vertices_elem.get("normal"))
    uv_flags = [_to_bool(vertices_elem.get(f"uv{i}")) for i in range(4)]
    has_color = _to_bool(vertices_elem.get("color"))

    options = ShapeOptions.NONE
    if has_normal:
        options |= ShapeOptions.HAS_NORMALS
    for i, present in enumerate(uv_flags):
        if present:
            options |= ShapeOptions(int(ShapeOptions.HAS_UV1) << i)
    if has_color:
        options |= ShapeOptions.HAS_VERTEX_COLOR
    sh.options = options

    positions = []
    normals = [] if has_normal else None
    uv_sets = [[] if uv_flags[i] else None for i in range(4)]
    vertex_colors = [] if has_color else None

    for v in vertices_elem.findall("v"):
        p = _floats(v.get("p"), 3) or [0.0, 0.0, 0.0]
        positions.append(Vector3(p[0], p[1], p[2]))
        if has_normal:
            n = _floats(v.get("n"), 3) or [0.0, 0.0, 0.0]
            normals.append(Vector3(n[0], n[1], n[2]))
        for i in range(4):
            if uv_flags[i]:
                t = _floats(v.get(f"t{i}"), 2) or [0.0, 0.0]
                uv_sets[i].append(UV(t[0], t[1]))
        if has_color:
            c = _floats(v.get("c"), 4) or _floats(v.get("c"), 3)
            if c is None:
                c = [1.0, 1.0, 1.0, 1.0]
            elif len(c) == 3:
                c = c + [1.0]
            vertex_colors.append(Vector4(c[0], c[1], c[2], c[3]))

    sh.positions = positions
    sh.normals = normals
    sh.uv_sets = uv_sets
    sh.vertex_colors = vertex_colors

    triangles_elem = elem.find("Triangles")
    if triangles_elem is not None:
        tris = []
        for t in triangles_elem.findall("t"):
            vi = _floats(t.get("vi"), 3)
            if vi is None:
                continue
            # Inline XML indices are 0-based; Shape.triangles uses the same
            # 1-based-on-disk convention as the binary format (I3DTri.cs) -
            # shape_to_mesh_data() subtracts 1 again downstream.
            tris.append(Triangle(int(vi[0]) + 1, int(vi[1]) + 1, int(vi[2]) + 1))
        sh.triangles = tris

    subsets_elem = elem.find("Subsets")
    if subsets_elem is not None:
        subsets = []
        for s in subsets_elem.findall("Subset"):
            subsets.append(Subset(
                _to_int(s.get("firstVertex")),
                _to_int(s.get("numVertices")),
                _to_int(s.get("firstIndex")),
                _to_int(s.get("numIndices")),
            ))
        sh.subsets = subsets

    sh.unread_bytes = 0
    return sh


def parse_inline_spline(elem: ET.Element) -> Spline:
    """Decode an inline <NurbsCurve> element (Editor-saved splines) into a Spline.

    Points are read from child <cp p="x y z"/> (control point) elements.
    """
    sp = Spline()
    sp.name = elem.get("name", "")
    sp.id = _to_int(elem.get("shapeId"), default=0)
    sp.kind = "SPLINE"
    sp.form_closed = _to_bool(elem.get("closed"))

    points = []
    for cp in elem.findall("cp"):
        p = _floats(cp.get("p"), 3)
        if p is not None:
            points.append(Vector3(p[0], p[1], p[2]))
    sp.points = points
    sp.unread_bytes = 0
    return sp


# Element tags directly under <Shapes> that carry actual geometry we can
# decode. Anything else (<Precipitation>, ...) stays "inline but
# unsupported" - the importer keeps warning for those.
GEOMETRY_TAGS = {"IndexedTriangleSet": parse_inline_shape,
                  "NurbsCurve": parse_inline_spline}
