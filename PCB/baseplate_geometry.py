"""Hole/slot pattern for the snap-together M3 baseplate.

Pure geometry, no KiCad or mesh dependency, so it's the single source of
truth for both generate_baseplate.py (emits the production KiCad board) and
baseplate_mesh.py (builds the manifold3d preview used by the live viewer).
The two outputs can never drift apart because they read the same numbers.

Break-off design: each interior grid line is a run of milled NPTH slots, one
per 10 mm cell, each stopping WEB/2 short of the line crossings so a solid
web straddles every crossing. The webs are what hold the panel together, so
WEB and EDGE_WEB are the two strength dials. Both are parameters, not just
constants, because the right values depend on board thickness and on how the
boards get handled in shipping.
"""

from dataclasses import dataclass, field

CELL = 10.0            # grid pitch, mm
M3_DRILL = 3.2          # default M3 clearance hole, mm
SLOT_W = 1.0            # snap-slot width; >= common fab minimum for NPTH slots
WEB = 3.0               # solid web centered on each line crossing, mm
EDGE_WEB = 2.5          # solid material between slot end and board edge, mm


@dataclass
class RoundHole:
    x: float
    y: float
    d: float


@dataclass
class SlotHole:
    cx: float
    cy: float
    length: float
    width: float
    along_y: bool  # True: long axis is Y (width measured in X); False: long axis X


@dataclass
class BoardGeometry:
    width: float
    height: float
    nx: int
    ny: int
    m3_holes: list = field(default_factory=list)
    snap_slots: list = field(default_factory=list)


def cells(dim_mm, label=None):
    n = max(1, round(dim_mm / CELL))
    if label and abs(n * CELL - dim_mm) > 1e-6:
        print(f"note: {label} {dim_mm:g} mm rounded to {n * CELL:g} mm ({n} cells)")
    return n


def _segments(n_cells, total, web, edge_web):
    """Slot spans along one grid line, one per cell. A span shorter than the
    slot width can't be milled as an oval, so an over-wide web just drops
    those slots instead of emitting degenerate geometry."""
    segs = []
    for g in range(n_cells):
        lo = g * CELL
        start = edge_web if g == 0 else lo + web / 2
        end = total - edge_web if g == n_cells - 1 else lo + CELL - web / 2
        if end - start >= SLOT_W:
            segs.append((start, end))
    return segs


def solid_fraction(n_cells, total, web, edge_web):
    """Fraction of a break line that is still solid material. The handle for
    'how hard is this to snap': 1.0 is an unbroken line, and the lower it
    goes the less force the break takes."""
    slotted = sum(end - start for start, end in _segments(n_cells, total, web, edge_web))
    return max(0.0, 1.0 - slotted / total)


def compute_geometry(width, height=None, hole_d=M3_DRILL, snap_lines=True,
                     web=WEB, edge_web=EDGE_WEB, label=False):
    """Hole and slot layout for a board of the given size.

    snap_lines=False emits a plain drilled grid plate with no break-off
    geometry at all, for when the board is used at its full size and the
    slots are just a liability.
    """
    nx = cells(width, "width" if label else None)
    ny = cells(height if height is not None else width, "height" if label else None)
    W, H = nx * CELL, ny * CELL

    m3_holes = [RoundHole(CELL / 2 + i * CELL, CELL / 2 + j * CELL, hole_d)
                for i in range(nx) for j in range(ny)]

    if not snap_lines:
        return BoardGeometry(W, H, nx, ny, m3_holes, [])

    snap_slots = []
    for k in range(1, nx):
        for start, end in _segments(ny, H, web, edge_web):
            snap_slots.append(SlotHole(CELL * k, (start + end) / 2, end - start, SLOT_W, along_y=True))
    for k in range(1, ny):
        for start, end in _segments(nx, W, web, edge_web):
            snap_slots.append(SlotHole((start + end) / 2, CELL * k, end - start, SLOT_W, along_y=False))

    return BoardGeometry(W, H, nx, ny, m3_holes, snap_slots)
