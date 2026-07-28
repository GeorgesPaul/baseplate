"""Live parameter viewer for the pillar + baseplate assembly.

Shows 4 corner pillars plus the top and bottom baseplate. The baseplate is
built from PCB/baseplate_geometry.py, the exact same hole/slot pattern used
by PCB/generate_baseplate.py for the production KiCad board -- not a
simplified stand-in -- so what you see here is what gets fabricated.
Rebuilding it is the expensive part (a few hundred ms to ~1-2s at large
board sizes because of the M3 grid + snap-slot count), so it's only rebuilt
when a board-affecting parameter (PCB length/width, hole size, snap-line
settings) actually changes; pillar-only tweaks just rebuild the pillars.

Run with:   uv run viewer.py
Smoke test (renders one frame to a PNG and exits):
            uv run viewer.py --screenshot out.png
"""

import glob
import os
import subprocess
import sys
import time

import numpy as np
import polyscope as ps
import polyscope.imgui as psim
from manifold3d import Manifold, OpType

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "pillars"))
sys.path.insert(0, os.path.join(ROOT, "PCB"))
import pillar as pl
import baseplate_mesh as bpm
from baseplate_geometry import CELL, WEB, EDGE_WEB, solid_fraction

EDGE_MARGIN = CELL / 2.0  # mm, corner M3 hole center to board edge (fixed by the grid)
HEIGHT_DEFAULT = pl.DEFAULTS["heights"][1]

# Board-only params, kept out of the dict handed to build_pillar().
BOARD_PARAM_KEYS = ("pcb_length", "pcb_width", "snap_lines", "web", "edge_web")

params = {k: pl.DEFAULTS[k] for k in pl.PILLAR_PARAM_KEYS if k in pl.DEFAULTS}
params["height"] = HEIGHT_DEFAULT
for k in pl.FIXTURE_PARAM_KEYS:
    params[k] = pl.DEFAULTS[k]
params["fixture"] = pl.DEFAULTS["fixture"]
params["pcb_length"] = 70.0
params["pcb_width"] = 70.0
params["snap_lines"] = True
params["web"] = WEB
params["edge_web"] = EDGE_WEB

view = dict(show_plate_bottom=True, show_plate_top=True, show_pillars=True)

FIXTURES = ("clip", "snap", "screw", "press")

last_build_ms = 0.0
mesh_names = []
status_line = ""
scene_lo = np.zeros(3)
scene_hi = np.ones(3)

# cached board build, keyed on the params that actually affect it
board_cache = {"key": None, "board": None, "geo": None}

# Polyscope's materials are matcaps that add a lot of ambient light, so a
# color set to the literal sRGB of FR4 comes out washed to mint. These are
# pre-compensated: they render as solder-mask green and light blue, they do
# not read as those colors on a swatch.
FR4_GREEN = (0.00, 0.24, 0.08)     # solder mask green, both plates
PILLAR_BLUE = (0.30, 0.62, 0.92)   # light blue, printed parts

MESH_COLORS = {
    "pillar": PILLAR_BLUE,
    "plate_bottom": FR4_GREEN,
    "plate_top": FR4_GREEN,
}

# "clay" is the most even of the built-in materials: matte, no blown-out
# specular on the large flat plate faces. Supersampling is what actually
# cleans up the hole and slot edges, which is most of what you look at here.
MATERIAL = "clay"
SSAA_INTERACTIVE = 2
SSAA_SCREENSHOT = 4


def setup_render(ssaa=SSAA_INTERACTIVE):
    ps.set_up_dir("z_up")
    ps.set_ground_plane_mode("shadow_only")
    ps.set_shadow_darkness(0.32)
    ps.set_shadow_blur_iters(4)
    ps.set_background_color((1.0, 1.0, 1.0))
    ps.set_SSAA_factor(ssaa)


def rot90(pt, k):
    x, y = pt
    for _ in range(k % 4):
        x, y = -y, x
    return x, y


def pillar_params():
    p = {k: v for k, v in params.items() if k not in BOARD_PARAM_KEYS}
    return p


def get_board():
    key = (params["pcb_length"], params["pcb_width"], params["hole_d"], params["plate_t"],
           params["snap_lines"], params["web"], params["edge_web"])
    if board_cache["key"] != key:
        board, geo = bpm.build_board_at_origin(
            params["pcb_length"], params["pcb_width"], params["hole_d"], params["plate_t"],
            params["snap_lines"], params["web"], params["edge_web"])
        board_cache.update(key=key, board=board, geo=geo)
    return board_cache["board"], board_cache["geo"]


def corner_positions(geo):
    L, W = geo.width, geo.height
    return [
        (0, EDGE_MARGIN, EDGE_MARGIN),
        (1, L - EDGE_MARGIN, EDGE_MARGIN),
        (2, L - EDGE_MARGIN, W - EDGE_MARGIN),
        (3, EDGE_MARGIN, W - EDGE_MARGIN),
    ]


def build_pillars(p, geo):
    base = pl.build_pillar(p)
    solids = [base.rotate((0, 0, 90.0 * k)).translate((cx, cy, 0)) for k, cx, cy in corner_positions(geo)]
    return Manifold.batch_boolean(solids, OpType.Add)


def mesh_arrays(manifold):
    m = manifold.to_mesh()
    return np.asarray(m.vert_properties)[:, :3], np.asarray(m.tri_verts)


def rebuild():
    """Rebuilds whatever is out of date. get_board() is itself cached on the
    params that affect it, so calling it here is cheap when only a
    pillar-only parameter changed."""
    global last_build_ms, mesh_names, scene_lo, scene_hi
    t0 = time.perf_counter()
    p = pillar_params()
    board, geo = get_board()

    meshes = {}
    if view["show_pillars"]:
        meshes["pillar"] = mesh_arrays(build_pillars(p, geo))
    if view["show_plate_bottom"]:
        meshes["plate_bottom"] = mesh_arrays(board.translate((0, 0, -p["plate_t"])))
    if view["show_plate_top"]:
        meshes["plate_top"] = mesh_arrays(board.translate((0, 0, p["height"])))

    new_names = []
    for name, (verts, tris) in meshes.items():
        ps.register_surface_mesh(name, verts, tris, smooth_shade=False,
                                 color=MESH_COLORS.get(name), material=MATERIAL)
        new_names.append(name)

    for name in mesh_names:
        if name not in new_names and ps.has_surface_mesh(name):
            ps.remove_surface_mesh(name)
    mesh_names = new_names

    if meshes:
        lo = np.min([v.min(axis=0) for v, t in meshes.values()], axis=0)
        hi = np.max([v.max(axis=0) for v, t in meshes.values()], axis=0)
        scene_lo, scene_hi = lo, hi

    last_build_ms = (time.perf_counter() - t0) * 1000


def export_stl():
    global status_line
    p = pillar_params()
    board, geo = get_board()
    out_dir = os.path.join(ROOT, "things")
    os.makedirs(out_dir, exist_ok=True)
    written = []

    verts, tris = mesh_arrays(pl.build_pillar(p))
    fn = os.path.join(out_dir, "pillar_%s_h%g.stl" % (p["fixture"], p["height"]))
    pl.write_stl(fn, verts, tris)
    written.append(os.path.basename(fn))

    for label, z0 in (("bottom", -p["plate_t"]), ("top", p["height"])):
        verts, tris = mesh_arrays(board.translate((0, 0, z0)))
        fn = os.path.join(out_dir, "plate_%s_%gx%g.stl" % (label, geo.width, geo.height))
        pl.write_stl(fn, verts, tris)
        written.append(os.path.basename(fn))

    status_line = "Wrote " + ", ".join(written) + " to things/"
    print(status_line)


def find_kicad_python():
    """generate_baseplate.py needs pcbnew (native-format save) and kicad-cli
    (gerbers), both only available through KiCad's own bundled Python, not
    the uv venv this viewer runs in."""
    candidates = []
    for base in (
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "KiCad"),
        r"C:\Program Files\KiCad",
    ):
        candidates += glob.glob(os.path.join(base, "*", "bin", "python.exe"))
    candidates.sort(reverse=True)
    return candidates[0] if candidates else None


def export_pcb():
    global status_line
    kicad_python = find_kicad_python()
    if not kicad_python:
        status_line = "Could not find KiCad's bundled Python (needed for pcbnew + kicad-cli)"
        print(status_line)
        return

    pcb_dir = os.path.join(ROOT, "PCB")
    args = [kicad_python, "generate_baseplate.py", str(params["pcb_length"]), str(params["pcb_width"]),
            "--hole-d", str(params["hole_d"])]
    if params["snap_lines"]:
        args += ["--web", str(params["web"]), "--edge-web", str(params["edge_web"])]
    else:
        args.append("--no-snap-lines")
    status_line = "Running KiCad export (this can take a few seconds)..."
    print(status_line)
    result = subprocess.run(args, cwd=pcb_dir, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        last_err = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "unknown error"
        status_line = "PCB export failed: " + last_err
    else:
        last_line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "done"
        status_line = "PCB export: " + last_line


def slider_row(label, key, mn, mx):
    changed, params[key] = psim.SliderFloat(label, params[key], mn, mx)
    return changed


def ui():
    global status_line
    board_dirty = False
    pillars_dirty = False

    psim.TextUnformatted("Rebuild time: %.0f ms" % last_build_ms)
    _, geo = get_board()
    if abs(geo.width - params["pcb_length"]) > 1e-6 or abs(geo.height - params["pcb_width"]) > 1e-6:
        psim.TextUnformatted("Board rounded to %g x %g mm (10 mm grid)" % (geo.width, geo.height))
    psim.Separator()

    if slider_row("PCB length (mm)", "pcb_length", 30.0, 300.0):
        board_dirty = True
    if slider_row("PCB width (mm)", "pcb_width", 30.0, 300.0):
        board_dirty = True
    if slider_row("Hole size (mm)", "hole_d", 2.0, 6.0):
        board_dirty = True
        pillars_dirty = True
    if slider_row("Pillar height (mm)", "height", 10.0, 80.0):
        pillars_dirty = True

    psim.Separator()
    changed, params["snap_lines"] = psim.Checkbox("Break-off snap lines", params["snap_lines"])
    board_dirty = board_dirty or changed
    if params["snap_lines"]:
        if slider_row("Crossing web (mm)", "web", 1.0, 8.0):
            board_dirty = True
        if slider_row("Edge web (mm)", "edge_web", 0.5, 8.0):
            board_dirty = True
        # The webs are the only thing holding the panel together along a
        # break line, so show how much of that line survives. Higher is
        # harder to snap.
        for axis, n_cells, total in (("X", geo.nx, geo.width), ("Y", geo.ny, geo.height)):
            frac = solid_fraction(n_cells, total, params["web"], params["edge_web"])
            psim.TextUnformatted("break along %s: %.0f%% solid (%.1f of %g mm)"
                                 % (axis, frac * 100, frac * total, total))

    psim.Separator()
    psim.TextUnformatted("Fixture type")
    for i, name in enumerate(FIXTURES):
        if i > 0:
            psim.SameLine()
        if psim.RadioButton(name, params["fixture"] == name):
            params["fixture"] = name
            pillars_dirty = True

    psim.Separator()
    psim.TextUnformatted("Show")
    changed, view["show_pillars"] = psim.Checkbox("Pillars", view["show_pillars"])
    pillars_dirty = pillars_dirty or changed
    psim.SameLine()
    changed, view["show_plate_bottom"] = psim.Checkbox("Bottom plate", view["show_plate_bottom"])
    board_dirty = board_dirty or changed
    psim.SameLine()
    changed, view["show_plate_top"] = psim.Checkbox("Top plate", view["show_plate_top"])
    board_dirty = board_dirty or changed

    if psim.TreeNodeEx("Pillar parameters", psim.ImGuiTreeNodeFlags_DefaultOpen):
        for label, key, mn, mx in (
            ("Grid pitch (mm)", "grid_pitch", 6.0, 20.0),
            ("Plate thickness (mm)", "plate_t", 0.8, 3.2),
            ("Arm width (mm)", "arm_w", 4.0, 16.0),
            ("Arm length (mm)", "arm_l", 10.0, 30.0),
            ("Stub clearance (mm)", "stub_clearance", 0.0, 0.5),
            ("Stub extra protrusion (mm)", "stub_extra", 0.0, 3.0),
            ("Tip chamfer (mm)", "tip_chamfer", 0.0, 1.5),
            ("Edge fillet (mm)", "edge_fillet", 0.0, 3.0),
        ):
            if slider_row(label, key, mn, mx):
                pillars_dirty = True
                if key == "plate_t":
                    board_dirty = True
        psim.TextUnformatted("(grid pitch should match the baseplate's fixed 10 mm hole grid to align)")
        if psim.Button("Reset pillar parameters"):
            for key in pl.PILLAR_PARAM_KEYS:
                params[key] = HEIGHT_DEFAULT if key == "height" else pl.DEFAULTS[key]
            pillars_dirty = True
            board_dirty = True
        psim.TreePop()

    if psim.TreeNodeEx("Fixture parameters (all types)", psim.ImGuiTreeNodeFlags_DefaultOpen):
        psim.TextUnformatted("Clip")
        for label, key, mn, mx in (
            ("Clip wire diameter (mm)", "clip_wire_d", 0.5, 3.0),
            ("Cross-hole diameter (mm)", "clip_hole_d", 0.5, 3.0),
            ("Preload (mm)", "preload", 0.0, 1.0),
            ("Tip wall beyond cross-hole (mm)", "clip_tip_wall", 0.0, 2.0),
        ):
            if slider_row(label, key, mn, mx):
                pillars_dirty = True

        psim.TextUnformatted("Snap")
        for label, key, mn, mx in (
            ("Barb oversize (mm)", "snap_barb_oversize", 0.0, 1.0),
            ("Barb length (mm)", "snap_barb_len", 0.2, 3.0),
            ("Slot width (mm)", "snap_slot_w", 0.4, 3.0),
        ):
            if slider_row(label, key, mn, mx):
                pillars_dirty = True

        psim.TextUnformatted("Screw")
        if slider_row("Screw clearance diameter (mm)", "screw_clearance_d", 1.5, 6.0):
            pillars_dirty = True

        psim.TextUnformatted("Press fit")
        if slider_row("Interference (mm)", "press_interference", 0.0, 0.5):
            pillars_dirty = True

        if psim.Button("Reset fixture parameters"):
            for key in pl.FIXTURE_PARAM_KEYS:
                params[key] = pl.DEFAULTS[key]
            pillars_dirty = True
        psim.TreePop()

    psim.Separator()
    if psim.Button("Export STL"):
        export_stl()
    psim.SameLine()
    if psim.Button("Export PCB (Gerbers + KiCad file)"):
        export_pcb()
    if status_line:
        psim.TextUnformatted(status_line)

    if board_dirty or pillars_dirty:
        rebuild()


def frame_scene():
    center = (scene_lo + scene_hi) / 2.0
    extent = float(np.max(scene_hi - scene_lo))
    dist = 1.2 * extent
    ps.look_at((center[0] - 0.8 * dist, center[1] - 0.8 * dist, center[2] + 0.7 * dist), tuple(center))


def screen_size():
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080


def main():
    shot = "--screenshot" in sys.argv
    ps.set_program_name("Pillar + baseplate generator")
    width, height = screen_size()
    ps.set_window_size(width, height)
    ps.init()
    setup_render(SSAA_SCREENSHOT if shot else SSAA_INTERACTIVE)
    rebuild()
    frame_scene()
    ps.set_user_callback(ui)

    if shot:
        out = sys.argv[sys.argv.index("--screenshot") + 1]
        ps.screenshot(out)
        print("Wrote " + out)
        return

    ps.show()


if __name__ == "__main__":
    main()
