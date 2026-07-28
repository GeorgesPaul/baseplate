"""manifold3d 3D model of the snap-together baseplate, built from the same
hole/slot geometry as generate_baseplate.py (baseplate_geometry.py), so the
live preview always matches the production board. No KiCad Python
dependency, so this runs in the regular uv-managed venv.
"""

from manifold3d import Manifold, CrossSection, OpType

from baseplate_geometry import compute_geometry

CIRC_SEGMENTS = 32


def _slot_cross_section(slot):
    r = slot.width / 2.0
    half = max((slot.length - slot.width) / 2.0, 0.0)
    if slot.along_y:
        p1, p2 = (slot.cx, slot.cy - half), (slot.cx, slot.cy + half)
    else:
        p1, p2 = (slot.cx - half, slot.cy), (slot.cx + half, slot.cy)
    c1 = CrossSection.circle(r, CIRC_SEGMENTS).translate(p1)
    c2 = CrossSection.circle(r, CIRC_SEGMENTS).translate(p2)
    return CrossSection.batch_boolean([c1, c2], OpType.Add).hull()


def build_board_at_origin(width, height=None, hole_d=3.2, thickness=1.6, label=False):
    """Returns (Manifold, BoardGeometry). The manifold spans z in
    [0, thickness] and x/y in [0, geo.width] / [0, geo.height]. Split out
    from build_board() so a caller needing both plates (identical apart from
    Z placement) can extrude once and cheaply translate twice instead of
    paying the triangulation cost, by far the most expensive step, twice."""
    geo = compute_geometry(width, height, hole_d, label=label)

    cutters = [CrossSection.circle(h.d / 2.0, CIRC_SEGMENTS).translate((h.x, h.y))
               for h in geo.m3_holes + geo.mouse_bites]
    cutters += [_slot_cross_section(s) for s in geo.snap_slots]
    cutout = CrossSection.batch_boolean(cutters, OpType.Add)

    board_cs = CrossSection.square((geo.width, geo.height)) - cutout
    board = Manifold.extrude(board_cs, thickness)
    return board, geo


def build_board(width, height=None, hole_d=3.2, thickness=1.6, z0=0.0, label=False):
    """Returns (Manifold, BoardGeometry) with the manifold placed at z0."""
    board, geo = build_board_at_origin(width, height, hole_d, thickness, label)
    return board.translate((0, 0, z0)), geo


def mesh_arrays(manifold):
    import numpy as np
    m = manifold.to_mesh()
    return np.asarray(m.vert_properties)[:, :3], np.asarray(m.tri_verts)


if __name__ == "__main__":
    import time
    t0 = time.perf_counter()
    board, geo = build_board(250.0, 250.0)
    dt = (time.perf_counter() - t0) * 1000
    print(f"{geo.width:g}x{geo.height:g} board: {len(geo.m3_holes)} holes, "
          f"{len(geo.snap_slots)} slots, {len(geo.mouse_bites)} mouse bites, "
          f"status {board.status()}, built in {dt:.0f} ms")
