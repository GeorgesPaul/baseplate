"""Parametric L-shaped mounting pillar generator (manifold3d based).

Coordinate convention: origin at the center of the corner grid hole, on the
pillar's bottom shoulder plane (the plane that contacts the plate resting on
top of it). Z+ points from the bottom plate toward the lid. The two arms of
the L extend along X+ and Y+. The corner stub sits at the origin; the other
two stubs sit one grid pitch away along X and along Y.

Four fixture types select how the corner stub retains the plate:
  clip   - RC-style wire clip through a cross-drilled hole (preloaded so
           the installed clip clamps the plate).
  snap   - a split cantilever snap boss with a barbed tip (simpler, no
           separate clip part, but needs less stackup precision than clip).
  screw  - plain slip-fit stubs plus a coaxial screw clearance hole drilled
           through the whole pillar body for a bolt through both plates.
  press  - plain stubs sized for a light interference (press) fit, no
           additional retention feature.

Run as a script to export one STL per height in DEFAULTS["heights"]:
    uv run pillars/pillar.py
"""

import math
import os

import numpy as np
from manifold3d import Manifold, CrossSection, OpType

DEFAULTS = dict(
    grid_pitch=10.0,
    hole_d=3.2,             # M3 clearance hole, matches the baseplate's own hole size
    plate_t=1.6,
    heights=(20.0, 35.0, 50.0),
    arm_w=4.0,
    arm_l=15.0,
    stub_clearance=0.1,
    stub_extra=0.9,
    clip_wire_d=1.2,
    clip_hole_d=1.4,
    preload=0.3,
    clip_tip_wall=0.6,      # solid material left beyond the cross-hole, at the stub tip
    tip_chamfer=0.4,
    edge_fillet=1.0,
    fixture="clip",
    screw_clearance_d=3.2,
    press_interference=0.15,
    snap_barb_oversize=0.3,
    snap_barb_len=1.0,
    snap_slot_w=1.2,
)

# Parameter groups for the viewer's "reset to defaults" buttons.
PILLAR_PARAM_KEYS = (
    "height", "grid_pitch", "hole_d", "plate_t", "arm_w", "arm_l",
    "stub_clearance", "stub_extra", "tip_chamfer", "edge_fillet",
)
FIXTURE_PARAM_KEYS = (
    "clip_wire_d", "clip_hole_d", "preload", "clip_tip_wall",
    "screw_clearance_d", "press_interference",
    "snap_barb_oversize", "snap_barb_len", "snap_slot_w",
)

CIRC_SEGMENTS = 32


def fillet_polygon(points, radius, segments_per_circle=CIRC_SEGMENTS):
    """Round every vertex of a closed CCW polygon with a tangent-arc fillet.

    The fillet arc always bulges away from the vertex along the internal
    bisector of its two incident edges. At a convex vertex that bisector
    points into the material, so the corner is cut off (standard outer
    fillet). At a reflex (concave) vertex the bisector points into the
    notch on the other side, so the same construction adds a rounding
    blend there instead, which is what a fillet on an inner corner means.
    """
    pts = np.asarray(points, dtype=float)
    n = len(pts)
    out = []
    for i in range(n):
        p, v, q = pts[(i - 1) % n], pts[i], pts[(i + 1) % n]
        e1, e2 = v - p, q - v
        len1, len2 = np.linalg.norm(e1), np.linalg.norm(e2)
        rp, rn = -e1 / len1, e2 / len2
        dot = np.clip(np.dot(rp, rn), -1.0, 1.0)
        theta = math.acos(dot)
        if theta < 1e-6 or theta > math.pi - 1e-6 or radius <= 0.0:
            out.append(v)
            continue
        t = min(radius / math.tan(theta / 2.0), 0.999 * len1, 0.999 * len2)
        r_eff = t * math.tan(theta / 2.0)
        b_hat = (rp + rn) / np.linalg.norm(rp + rn)
        center = v + b_hat * (r_eff / math.sin(theta / 2.0))
        t1, t2 = v + rp * t, v + rn * t
        a1 = math.atan2(t1[1] - center[1], t1[0] - center[0])
        a2 = math.atan2(t2[1] - center[1], t2[0] - center[0])
        diff = (a2 - a1) % (2 * math.pi)
        if diff > math.pi:
            diff -= 2 * math.pi
        segs = max(1, math.ceil(abs(diff) / (2 * math.pi) * segments_per_circle))
        for k in range(segs + 1):
            a = a1 + diff * k / segs
            out.append(center + r_eff * np.array([math.cos(a), math.sin(a)]))
    return out


def l_profile(arm_w, arm_l, edge_fillet):
    raw = [
        (-arm_w / 2, -arm_w / 2), (arm_l, -arm_w / 2), (arm_l, arm_w / 2),
        (arm_w / 2, arm_w / 2), (arm_w / 2, arm_l), (-arm_w / 2, arm_l),
    ]
    return fillet_polygon(raw, edge_fillet)


def chamfered_stub(radius, length, tip_chamfer, hollow_radius=None):
    """A +Z cylinder from z=0 to z=length with a conical chamfer at the tip."""
    chamfer = min(tip_chamfer, length * 0.5, radius * 0.9) if tip_chamfer > 0 else 0.0
    body_h = length - chamfer
    parts = [Manifold.cylinder(body_h, radius, radius, CIRC_SEGMENTS)]
    if chamfer > 0:
        cone = Manifold.cylinder(chamfer, radius, radius - chamfer, CIRC_SEGMENTS)
        parts.append(cone.translate((0, 0, body_h)))
    stub = Manifold.batch_boolean(parts, OpType.Add)
    if hollow_radius:
        bore = Manifold.cylinder(length + 1.0, hollow_radius, hollow_radius, CIRC_SEGMENTS).translate((0, 0, -0.5))
        stub = stub - bore
    return stub


def clip_axis_z(p):
    """Height of the clip wire's axis above the shoulder plane (local +Z stub frame)."""
    return p["plate_t"] + p["clip_wire_d"] / 2.0 - p["preload"]


def clip_hole(clip_hole_d, axis_z, through_len):
    """Cross-drilled hole through the corner stub for the RC wire clip, in
    the same local +Z frame as chamfered_stub (shoulder at z=0). Its near
    edge reaches down into the plate's thickness span at typical preload
    settings, since the wire has to flex against the plate to clamp it."""
    r = clip_hole_d / 2.0
    hole = Manifold.cylinder(through_len, r, r, CIRC_SEGMENTS, center=True)
    return hole.rotate((0, 90, 0)).translate((0, 0, axis_z))


def snap_boss(radius, length, tip_chamfer, barb_oversize, barb_len, slot_w):
    body_h = max(length - barb_len - tip_chamfer, 0.1)
    parts = [Manifold.cylinder(body_h, radius, radius, CIRC_SEGMENTS)]
    barb_r = radius + barb_oversize
    parts.append(Manifold.cylinder(barb_len, radius, barb_r, CIRC_SEGMENTS).translate((0, 0, body_h)))
    tip_h = length - body_h - barb_len
    if tip_h > 0:
        parts.append(Manifold.cylinder(tip_h, barb_r, max(radius - tip_chamfer, 0.05), CIRC_SEGMENTS)
                     .translate((0, 0, body_h + barb_len)))
    boss = Manifold.batch_boolean(parts, OpType.Add)
    slot = Manifold.cube((slot_w, 2 * (radius + barb_oversize) + 1.0, length + 1.0), center=False)
    slot = slot.translate((-slot_w / 2.0, -(radius + barb_oversize) - 0.5, -0.5))
    return boss - slot


def build_stub(name, x, y, top, p):
    """Builds one stub (mirrored/placed for the bottom or top end) already
    positioned at its final XY and oriented so it points away from the body."""
    r = (p["hole_d"] - p["stub_clearance"]) / 2.0
    if p["fixture"] == "press":
        r = (p["hole_d"] + p["press_interference"] - p["stub_clearance"]) / 2.0
    length = p["plate_t"] + p["stub_extra"]

    is_corner = name == "corner"
    if is_corner and p["fixture"] == "snap":
        solid = snap_boss(r, length, p["tip_chamfer"], p["snap_barb_oversize"],
                           p["snap_barb_len"], p["snap_slot_w"])
    elif is_corner and p["fixture"] == "clip":
        axis_z = clip_axis_z(p)
        length = axis_z + p["clip_hole_d"] / 2.0 + p["clip_tip_wall"]
        solid = chamfered_stub(r, length, p["tip_chamfer"])
        hole = clip_hole(p["clip_hole_d"], axis_z, through_len=4 * r + 4)
        solid = solid - hole
    else:
        solid = chamfered_stub(r, length, p["tip_chamfer"])

    if top:
        solid = solid.translate((0, 0, p["height"]))
    else:
        solid = solid.mirror((0, 0, 1))
    return solid.translate((x, y, 0))


def build_pillar(params=None, **overrides):
    p = dict(DEFAULTS)
    p.pop("heights", None)
    p.update(params or {})
    p.update(overrides)
    if "height" not in p:
        raise ValueError("build_pillar requires a 'height' parameter")

    profile = CrossSection([l_profile(p["arm_w"], p["arm_l"], p["edge_fillet"])])
    body = Manifold.extrude(profile, p["height"])

    stub_positions = [("corner", 0.0, 0.0), ("x_arm", p["grid_pitch"], 0.0), ("y_arm", 0.0, p["grid_pitch"])]
    solids = [body]
    for name, x, y in stub_positions:
        solids.append(build_stub(name, x, y, top=False, p=p))
        solids.append(build_stub(name, x, y, top=True, p=p))
    pillar = Manifold.batch_boolean(solids, OpType.Add)

    if p["fixture"] == "screw":
        stub_len = p["plate_t"] + p["stub_extra"]
        bore_len = p["height"] + 2 * stub_len + 2.0
        bore = Manifold.cylinder(bore_len, p["screw_clearance_d"] / 2.0, p["screw_clearance_d"] / 2.0, CIRC_SEGMENTS)
        bore = bore.translate((0, 0, -stub_len - 1.0))
        pillar = pillar - bore

    return pillar


def mesh_arrays(manifold):
    m = manifold.to_mesh()
    return np.asarray(m.vert_properties)[:, :3], np.asarray(m.tri_verts)


def write_stl(filename, verts, tris):
    tri_pts = np.asarray(verts, dtype=np.float64)[np.asarray(tris)]
    normals = np.cross(tri_pts[:, 1] - tri_pts[:, 0], tri_pts[:, 2] - tri_pts[:, 0])
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    lengths[lengths == 0] = 1.0
    normals = normals / lengths

    n = len(tri_pts)
    record = np.zeros(n, dtype=[("normal", "<f4", 3), ("verts", "<f4", (3, 3)), ("attr", "<u2")])
    record["normal"] = normals
    record["verts"] = tri_pts
    with open(filename, "wb") as f:
        f.write(b"pillar mesh export".ljust(80, b" "))
        f.write(np.uint32(n).tobytes())
        f.write(record.tobytes())


def main():
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "things")
    os.makedirs(out_dir, exist_ok=True)
    for height in DEFAULTS["heights"]:
        pillar = build_pillar(height=height)
        verts, tris = mesh_arrays(pillar)
        filename = os.path.join(out_dir, "pillar_%s_h%g.stl" % (DEFAULTS["fixture"], height))
        write_stl(filename, verts, tris)
        print("wrote %s (%d verts, %d tris, volume %.1f mm3)"
              % (filename, len(verts), len(tris), pillar.volume()))


if __name__ == "__main__":
    main()
