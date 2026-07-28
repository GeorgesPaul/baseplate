"""Hole/slot pattern for the snap-together M3 baseplate.

Pure geometry, no KiCad or mesh dependency, so it's the single source of
truth for both generate_baseplate.py (emits the production KiCad board) and
baseplate_mesh.py (builds the manifold3d preview used by the live viewer).
The two outputs can never drift apart because they read the same numbers.
"""

from dataclasses import dataclass, field

CELL = 10.0            # grid pitch, mm
M3_DRILL = 3.2          # default M3 clearance hole, mm
BITE_DRILL = 0.5        # mouse-bite hole, mm
BITE_PITCH = 0.75       # mouse-bite hole spacing, mm
SLOT_W = 1.0            # snap-slot width; >= common fab minimum for NPTH slots
WEB = 2.5               # solid web centered on each line crossing, mm
EDGE_WEB = 0.8          # solid margin between slot end and board edge, mm


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
    mouse_bites: list = field(default_factory=list)


def cells(dim_mm, label=None):
    n = max(1, round(dim_mm / CELL))
    if label and abs(n * CELL - dim_mm) > 1e-6:
        print(f"note: {label} {dim_mm:g} mm rounded to {n * CELL:g} mm ({n} cells)")
    return n


def _segments(n_cells, total):
    segs = []
    for g in range(n_cells):
        lo = g * CELL
        start = EDGE_WEB if g == 0 else lo + WEB / 2
        end = total - EDGE_WEB if g == n_cells - 1 else lo + CELL - WEB / 2
        segs.append((start, end))
    return segs


def compute_geometry(width, height=None, hole_d=M3_DRILL, label=False):
    nx = cells(width, "width" if label else None)
    ny = cells(height if height is not None else width, "height" if label else None)
    W, H = nx * CELL, ny * CELL

    m3_holes = [RoundHole(CELL / 2 + i * CELL, CELL / 2 + j * CELL, hole_d)
                for i in range(nx) for j in range(ny)]

    # Snap lines at every interior grid line, both axes. Each 10 mm segment
    # gets one milled NPTH slot that stops WEB/2 short of the crossings
    # (EDGE_WEB short of the board edge), so a solid web straddles each
    # crossing and keeps the plate rigid.
    lines_x = [CELL * k for k in range(1, nx)]
    lines_y = [CELL * k for k in range(1, ny)]

    snap_slots = []
    for x in lines_x:
        for start, end in _segments(ny, H):
            snap_slots.append(SlotHole(x, (start + end) / 2, end - start, SLOT_W, along_y=True))
    for y in lines_y:
        for start, end in _segments(nx, W):
            snap_slots.append(SlotHole((start + end) / 2, y, end - start, SLOT_W, along_y=False))

    # Perforate each crossing web with a plus-pattern of mouse bites so the
    # break stays clean through the webs.
    mouse_bites = []
    for x in lines_x:
        for y in lines_y:
            for dx, dy in ((0, 0), (-BITE_PITCH, 0), (BITE_PITCH, 0), (0, -BITE_PITCH), (0, BITE_PITCH)):
                mouse_bites.append(RoundHole(x + dx, y + dy, BITE_DRILL))

    return BoardGeometry(W, H, nx, ny, m3_holes, snap_slots, mouse_bites)
