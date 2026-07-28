# Baseplate

![Baseplate in use](images/demo.png)

A parametric prototyping stack: a snappable M3 grid plate fabricated as a PCB, plus 3D-printed
L-shaped corner pillars that hold two plates apart.

The plate is an ordinary 1.6 mm PCB with no copper: M3 clearance holes on a 10 mm grid, and milled
snap lines along every grid line so you can break any rectangular sub-plate out of a larger sheet by
hand. The pillars clip into those same holes, so a bottom plate, four pillars and a top plate make a
rigid enclosure-less chassis for a board, a battery, a sensor stack, or whatever else.

Everything is generated from code. The hole and slot pattern lives in one module
(`PCB/baseplate_geometry.py`) that feeds both the production KiCad board and the live 3D preview, so
the thing you look at is the thing that gets fabricated.

![70x70 assembly with clip pillars](images/assembly_70x70_clip_h35.png)

## Configurations

Board size, pillar height and fixture type are all sliders in the viewer. A few points in that space:

### 40 x 40 mm, snap fixture, 20 mm pillars

The smallest useful stack. 16 M3 holes, 24 snap slots.

![40x40 snap assembly](images/assembly_40x40_snap_h20.png)

### 70 x 70 mm, clip fixture, 35 mm pillars

The default. 49 M3 holes, 84 snap slots. Corner stubs are cross-drilled for an RC-style wire clip.

![70x70 clip assembly](images/assembly_70x70_clip_h35.png)

### 150 x 80 mm, screw fixture, 50 mm pillars

Boards do not have to be square. 120 M3 holes, 217 snap slots. Each pillar is bored through for a
single bolt that clamps both plates.

![150x80 screw assembly](images/assembly_150x80_screw_h50.png)

### 250 x 250 mm, clip fixture, 50 mm pillars

The full panel, which is also the largest board most cheap fab houses take at the low price tier.
625 M3 holes, 1200 snap slots, 2880 mouse bites.

![250x250 clip assembly](images/assembly_250x250_clip_h50.png)

## Fixture types

All four pillars share the same L-shaped body and the same three-stub footprint (corner stub plus one
stub a grid pitch away along each arm). Only the corner stub changes, which is what retains the plate.

| | |
|---|---|
| **clip** <br> Cross-drilled stub for an RC-style wire clip. Preloaded so the installed clip clamps the plate down. <br> ![clip pillar](images/pillar_clip.png) | **snap** <br> Split cantilever boss with a barbed tip. No separate part to lose, and tolerant of stackup error. <br> ![snap pillar](images/pillar_snap.png) |
| **screw** <br> Plain slip-fit stubs plus a coaxial clearance bore through the whole body for one bolt. <br> ![screw pillar](images/pillar_screw.png) | **press** <br> Plain stubs sized for a light interference fit. Nothing else, for permanent stacks. <br> ![press pillar](images/pillar_press.png) |

Looking down at the corner stub, where the four types differ:

| clip | snap | screw | press |
|---|---|---|---|
| ![clip from above](images/pillar_clip_top.png) | ![snap from above](images/pillar_snap_top.png) | ![screw from above](images/pillar_screw_top.png) | ![press from above](images/pillar_press_top.png) |

## The board itself

Rendered from the generated KiCad file, 250 x 250 mm:

| Top | Perspective |
|---|---|
| ![board top render](images/board_250x250_top.png) | ![board perspective render](images/board_250x250_perspective.png) |

Each 10 mm cell carries one M3 clearance hole in its center. Every interior grid line is a run of
1.0 mm milled NPTH slots, one per cell, each stopping short of the line crossings so a 2.5 mm solid
web straddles every crossing and keeps the panel rigid in shipping. Those webs are perforated with a
plus-pattern of 0.5 mm mouse bites, so the panel still snaps cleanly along any grid line but needs
far fewer drill hits than perforating the whole line would.

## Running it

Dependencies are managed with [uv](https://docs.astral.sh/uv/).

```
uv run viewer.py
```

That opens the live parameter viewer: drag sliders, watch the assembly rebuild. The board mesh is
cached on the parameters that affect it, so pillar-only tweaks stay interactive even at 250 x 250 mm.

Two buttons in the viewer export:

- **Export STL** writes the current pillar plus both plates to `things/`.
- **Export PCB** shells out to `PCB/generate_baseplate.py` for the current board size.

A one-frame smoke test that renders to a PNG and exits:

```
uv run viewer.py --screenshot out.png
```

Pillar STLs at the three stock heights (20 / 35 / 50 mm), without opening the viewer:

```
uv run pillars/pillar.py
```

## Generating the PCB

`generate_baseplate.py` needs `pcbnew` and `kicad-cli`, which only exist inside KiCad's own bundled
Python, not the uv venv. Run it with that interpreter:

```
cd PCB
"%LOCALAPPDATA%/Programs/KiCad/10.0/bin/python.exe" generate_baseplate.py 250 250
```

(The viewer's **Export PCB** button finds that interpreter itself, searching `%LOCALAPPDATA%\Programs\KiCad`
and `C:\Program Files\KiCad`.)

Arguments are `WIDTH [HEIGHT] [--hole-d MM]`, in mm, rounded to the 10 mm grid; square if only width
is given. It writes `baseplate_<W>x<H>.kicad_pcb`, Gerbers and Excellon drills into
`production_<W>x<H>/`, and a ready-to-upload `baseplate_<W>x<H>_gerbers.zip`.

The board is first written in a conservative s-expression format, then reloaded and resaved through
`pcbnew` so the file ends up in the native format of the installed KiCad version.

## Layout

```
viewer.py                     live parameter viewer (polyscope + imgui)
pillars/pillar.py             parametric L-pillar generator (manifold3d), STL export
PCB/baseplate_geometry.py     hole/slot pattern, single source of truth
PCB/baseplate_mesh.py         manifold3d board model for the viewer
PCB/generate_baseplate.py     KiCad board + Gerber/drill export
PCB/production_<W>x<H>/       generated fab output (not committed)
things/                       exported STLs (not committed)
images/                       screenshots used by this README
```

Fab output, meshes and 3D renders are build products and stay out of the repo; run the commands above
to regenerate any of them.

## Key dimensions

| | |
|---|---|
| Grid pitch | 10 mm |
| M3 clearance hole | 3.2 mm NPTH |
| Plate thickness | 1.6 mm |
| Snap slot width | 1.0 mm |
| Web at line crossings | 2.5 mm |
| Mouse bite drill / pitch | 0.5 mm / 0.75 mm |
| Stock pillar heights | 20 / 35 / 50 mm |
| Pillar arm width / length | 4 mm / 15 mm |
