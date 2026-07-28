"""Generate a universal M3 mounting plate as a KiCad board.

Usage:
    python generate_baseplate.py [WIDTH [HEIGHT]] [--hole-d MM]
                                 [--no-snap-lines] [--web MM] [--edge-web MM]

WIDTH and HEIGHT are the board dimensions in mm (default 250 x 250; square
if only WIDTH is given). Dimensions are rounded to the nearest multiple of
the 10 mm grid. Outputs: baseplate_<W>x<H>.kicad_pcb, production files
(Gerbers + Excellon drills) in production_<W>x<H>/, and a ready-to-upload
baseplate_<W>x<H>_gerbers.zip.

Board contents:
- M3 clearance holes (3.2 mm NPTH by default) on a 10 mm grid, centered in
  each cell.
- Snap lines every 10 mm in both axes: one milled NPTH slot per 10 mm
  segment, with a solid web straddling each line crossing. --web and
  --edge-web set how much material those webs leave, i.e. how hard the
  panel is to snap; --no-snap-lines omits the break-off geometry entirely
  and leaves a plain drilled grid plate.

The hole/slot layout itself lives in baseplate_geometry.py, shared with the
live viewer's manifold3d preview so the two never drift apart.

All holes live in a single locked footprint at the board origin.

Run with KiCad's bundled Python so the final file is written in the native
format of the installed KiCad version (via pcbnew load + save):
    "%LOCALAPPDATA%/Programs/KiCad/10.0/bin/python.exe" generate_baseplate.py 250 250
"""

import argparse
import os
import uuid

from baseplate_geometry import compute_geometry, solid_fraction, M3_DRILL, WEB, EDGE_WEB

parser = argparse.ArgumentParser(description="Generate a snappable M3 mounting plate as a KiCad board.")
parser.add_argument("width", type=float, nargs="?", default=250.0, help="board width in mm (default 250)")
parser.add_argument("height", type=float, nargs="?", default=None, help="board height in mm (default: same as width)")
parser.add_argument("--hole-d", type=float, default=M3_DRILL, help=f"grid hole diameter in mm (default {M3_DRILL:g})")
parser.add_argument("--no-snap-lines", dest="snap_lines", action="store_false",
                    help="omit the break-off slots entirely (plain drilled grid plate)")
parser.add_argument("--web", type=float, default=WEB,
                    help=f"solid web at each line crossing in mm; higher is harder to snap (default {WEB:g})")
parser.add_argument("--edge-web", type=float, default=EDGE_WEB,
                    help=f"solid material between slot end and board edge in mm (default {EDGE_WEB:g})")
args = parser.parse_args()

geo = compute_geometry(args.width, args.height, args.hole_d, args.snap_lines,
                       args.web, args.edge_web, label=True)
W, H = geo.width, geo.height


def dim(v: float) -> str:
    return f"{v:g}"


base_name = f"baseplate_{dim(W)}x{dim(H)}"


def uid() -> str:
    return str(uuid.uuid4())


def num(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s if s else "0"


def pad(x: float, y: float, drill: float) -> str:
    d = num(drill)
    return (f'    (pad "" np_thru_hole circle (at {num(x)} {num(y)}) '
            f'(size {d} {d}) (drill {d}) (layers "*.Cu" "*.Mask") (uuid "{uid()}"))')


def slot(cx: float, cy: float, length: float, width: float, along_y: bool) -> str:
    w, l = (width, length) if along_y else (length, width)
    return (f'    (pad "" np_thru_hole oval (at {num(cx)} {num(cy)}) '
            f'(size {num(w)} {num(l)}) (drill oval {num(w)} {num(l)}) '
            f'(layers "*.Cu" "*.Mask") (uuid "{uid()}"))')


pads = [pad(h.x, h.y, h.d) for h in geo.m3_holes]
m3_count = len(pads)

pads += [slot(s.cx, s.cy, s.length, s.width, s.along_y) for s in geo.snap_slots]
slot_count = len(pads) - m3_count

descr = (f"{dim(W)}x{dim(H)} mm universal M3 mounting plate, "
         + (f"snappable on a 10 mm grid ({args.web:g} mm webs)" if args.snap_lines
            else "plain grid, no break-off slots"))

font = '(effects (font (size 1 1) (thickness 0.15)))'
pcb = f'''(kicad_pcb
  (version 20240108)
  (generator "generate_baseplate.py")
  (generator_version "8.0")
  (general
    (thickness 1.6)
    (legacy_teardrops no)
  )
  (paper "A4")
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (32 "B.Adhes" user "B.Adhesive")
    (33 "F.Adhes" user "F.Adhesive")
    (34 "B.Paste" user)
    (35 "F.Paste" user)
    (36 "B.SilkS" user "B.Silkscreen")
    (37 "F.SilkS" user "F.Silkscreen")
    (38 "B.Mask" user)
    (39 "F.Mask" user)
    (40 "Dwgs.User" user "User.Drawings")
    (41 "Cmts.User" user "User.Comments")
    (42 "Eco1.User" user "User.Eco1")
    (43 "Eco2.User" user "User.Eco2")
    (44 "Edge.Cuts" user)
    (45 "Margin" user)
    (46 "B.CrtYd" user "B.Courtyard")
    (47 "F.CrtYd" user "F.Courtyard")
    (48 "B.Fab" user)
    (49 "F.Fab" user)
  )
  (setup
    (pad_to_mask_clearance 0)
    (allow_soldermask_bridges_in_footprints no)
  )
  (net 0 "")
  (footprint "Baseplate:Grid_{dim(W)}x{dim(H)}_M3"
    (layer "F.Cu")
    (uuid "{uid()}")
    (at 0 0)
    (descr "{descr}")
    (attr through_hole board_only exclude_from_pos_files exclude_from_bom allow_missing_courtyard)
    (property "Reference" "H1" (at {num(W / 2)} -2 0) (layer "F.SilkS") (hide yes) (uuid "{uid()}") {font})
    (property "Value" "Baseplate_{dim(W)}x{dim(H)}" (at {num(W / 2)} {num(H + 2)} 0) (layer "F.Fab") (uuid "{uid()}") {font})
    (property "Footprint" "" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid "{uid()}") {font})
    (property "Datasheet" "" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid "{uid()}") {font})
    (property "Description" "" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid "{uid()}") {font})
{chr(10).join(pads)}
  )
  (gr_rect (start 0 0) (end {num(W)} {num(H)})
    (stroke (width 0.1) (type solid)) (fill none)
    (layer "Edge.Cuts") (uuid "{uid()}")
  )
)
'''

out = os.path.abspath(f"{base_name}.kicad_pcb")
with open(out, "w", newline="\n") as f:
    f.write(pcb)

print(f"board: {dim(W)} x {dim(H)} mm -> {os.path.basename(out)}")
print(f"M3 holes: {m3_count}")
if args.snap_lines:
    print(f"snap slots: {slot_count} (web {args.web:g} mm, edge web {args.edge_web:g} mm)")
    for axis, n_cells, total in (("X", geo.nx, W), ("Y", geo.ny, H)):
        frac = solid_fraction(n_cells, total, args.web, args.edge_web)
        print(f"  break line along {axis}: {frac * 100:.0f}% solid ({frac * total:.1f} mm of {total:g} mm)")
else:
    print("snap slots: none (--no-snap-lines)")
print(f"total pads: {len(pads)}")

# Resave through pcbnew so the file ends up in the native format of the
# installed KiCad version instead of the compatibility format above.
import pcbnew
board = pcbnew.LoadBoard(out)
pcbnew.SaveBoard(out, board)
print(f"resaved in native format of KiCad {pcbnew.Version()}")

# Export production files (Gerbers + Excellon drills) and zip them for upload.
import shutil
import subprocess
import sys

kicad_cli = os.path.join(os.path.dirname(sys.executable), "kicad-cli.exe")
if not os.path.exists(kicad_cli):
    kicad_cli = "kicad-cli"  # not running under KiCad's Python; use PATH

prod_dir = os.path.abspath(f"production_{dim(W)}x{dim(H)}")
subprocess.run([kicad_cli, "pcb", "export", "gerbers", out, "-o", prod_dir,
                "--layers", "F.Cu,B.Cu,F.Mask,B.Mask,F.SilkS,Edge.Cuts"], check=True)
subprocess.run([kicad_cli, "pcb", "export", "drill", out, "-o", prod_dir,
                "--excellon-separate-th", "--generate-map", "--map-format", "pdf"], check=True)
zip_path = shutil.make_archive(f"{base_name}_gerbers", "zip", prod_dir)
print(f"production files: {os.path.basename(prod_dir)}{os.sep} and {os.path.basename(zip_path)}")
