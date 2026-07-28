"""Offline path-traced render of whatever the viewer is currently showing.

The live viewer is a rasterizer: fast, but flat. The board is mostly holes
and slots, and those only read as depth once something actually traces
shadow rays into them, so the "Render" button hands the same meshes and the
same camera to mitsuba 3 and paints the result back over the viewport.

Kept deliberately small: meshes go out as temporary PLY files, the scene is
one dict, and lighting is a constant fill plus a single large area light, so
there are no light rigs or material graphs to maintain. Everything that
costs time is in SPP and resolution, both capped in render_view().

mitsuba is imported lazily: it takes a second or two to load and pulls in a
JIT backend, and none of that should happen for a viewer session that never
presses the button.
"""

import os
import tempfile

import numpy as np

# Albedos here are physical reflectances, NOT the pre-compensated constants
# the viewer feeds polyscope's matcaps. Same intent, different rendering
# model, so they are deliberately not shared.
FR4_GREEN = (0.020, 0.150, 0.055)
PILLAR_BLUE = (0.250, 0.500, 0.780)
GROUND_GREY = (0.780, 0.780, 0.800)

ROUGHNESS = {"plate": 0.18, "pillar": 0.42}  # plates have solder-mask sheen, prints are matte

MAX_WIDTH = 1600      # render resolution cap; the overlay is stretched to the viewport
DEFAULT_SPP = 192     # ~35 s at the resolution cap on 16 cores; noise-free enough
MIN_SPP, MAX_SPP = 16, 512


def _write_ply(path, verts, tris):
    """Binary little-endian PLY, positions and triangles only. Normals are
    left out on purpose: the ply loader is told face_normals, which is what
    keeps machined faces crisp instead of smoothing them into blobs."""
    verts = np.ascontiguousarray(verts, dtype="<f4")
    tris = np.asarray(tris, dtype="<i4")
    header = ("ply\n"
              "format binary_little_endian 1.0\n"
              f"element vertex {len(verts)}\n"
              "property float x\nproperty float y\nproperty float z\n"
              f"element face {len(tris)}\n"
              "property list uchar int vertex_indices\n"
              "end_header\n")
    faces = np.empty(len(tris), dtype=[("n", "u1"), ("v", "<i4", 3)])
    faces["n"] = 3
    faces["v"] = tris
    with open(path, "wb") as f:
        f.write(header.encode("ascii"))
        f.write(verts.tobytes())
        f.write(faces.tobytes())


def _bsdf(color, roughness, transparency):
    surface = {
        "type": "roughplastic",
        "distribution": "ggx",
        "alpha": roughness,
        "int_ior": 1.5,
        "diffuse_reflectance": {"type": "rgb", "value": list(color)},
    }
    if transparency >= 0.999:
        return surface
    # A null BSDF passes light straight through, so blending against it is
    # exactly the alpha the viewer's transparency slider means.
    return {
        "type": "blendbsdf",
        "weight": float(1.0 - transparency),
        "surface": surface,
        "clear": {"type": "null"},
    }


def _scene_dict(mi, parts, cam, width, height, spp, lo, hi):
    T = mi.ScalarTransform4f

    center = (lo + hi) / 2.0
    extent = float(np.max(hi - lo))
    eye, look, up, fov_vertical_deg = cam

    scene = {
        "type": "scene",
        "integrator": {"type": "path", "max_depth": 8},
        "sensor": {
            "type": "perspective",
            "fov": float(fov_vertical_deg),
            "fov_axis": "y",
            "to_world": T().look_at(origin=list(eye), target=list(eye + look), up=list(up)),
            "sampler": {"type": "independent", "sample_count": int(spp)},
            "film": {
                "type": "hdrfilm",
                "width": int(width),
                "height": int(height),
                "pixel_format": "rgb",
                "rfilter": {"type": "gaussian"},
            },
        },
        # Constant fill is what produces the soft ambient occlusion down the
        # holes and slots; the area light above only adds shape on top of it.
        "fill": {"type": "constant", "radiance": {"type": "rgb", "value": [0.55, 0.56, 0.60]}},
        "key": {
            "type": "rectangle",
            "to_world": (T().translate([center[0] - 0.5 * extent,
                                        center[1] - 0.7 * extent,
                                        hi[2] + 1.4 * extent])
                         .rotate([1, 0, 0], 180)
                         .scale([0.8 * extent, 0.8 * extent, 1.0])),
            "emitter": {"type": "area", "radiance": {"type": "rgb", "value": [7.0, 7.0, 7.0]}},
        },
        # Deliberately far larger than the frame: a ground plane whose edge
        # is visible reads as a wall, an oversized one reads as an infinite
        # studio floor.
        "ground": {
            "type": "rectangle",
            "to_world": (T().translate([center[0], center[1], lo[2] - 0.004 * extent])
                         .scale([40.0 * extent, 40.0 * extent, 1.0])),
            "bsdf": {"type": "diffuse", "reflectance": {"type": "rgb", "value": list(GROUND_GREY)}},
        },
    }

    for name, ply_path, color, roughness, transparency in parts:
        scene[name] = {
            "type": "ply",
            "filename": ply_path,
            "face_normals": True,
            "bsdf": _bsdf(color, roughness, transparency),
        }
    return scene


def render_view(meshes, cam, aspect, window_width, spp=DEFAULT_SPP):
    """Path-trace `meshes` from `cam` and return an (H, W, 3) float array in
    [0, 1], sRGB encoded and ready to hand to polyscope as an image.

    meshes: sequence of (name, verts, tris, color, roughness, transparency).
    cam:    (eye, look_dir, up, fov_vertical_deg), all numpy arrays but fov.
    """
    import mitsuba as mi

    # Only the scalar variant is guaranteed present; the LLVM ones need an
    # LLVM shared library that many machines do not have. Scalar still
    # renders image tiles across every core, so it is not a fallback to
    # single-threaded.
    if mi.variant() is None:
        for variant in ("llvm_ad_rgb", "scalar_rgb"):
            try:
                mi.set_variant(variant)
                break
            except Exception:
                continue

    width = int(min(MAX_WIDTH, max(320, window_width)))
    height = max(240, int(round(width / aspect)))

    lo = np.min([v.min(axis=0) for _, v, _, _, _, _ in meshes], axis=0)
    hi = np.max([v.max(axis=0) for _, v, _, _, _, _ in meshes], axis=0)

    with tempfile.TemporaryDirectory(prefix="baseplate_render_") as tmp:
        parts = []
        for name, verts, tris, color, roughness, transparency in meshes:
            ply_path = os.path.join(tmp, name + ".ply")
            _write_ply(ply_path, verts, tris)
            parts.append((name, ply_path, color, roughness, transparency))

        scene = mi.load_dict(_scene_dict(mi, parts, cam, width, height, spp, lo, hi))
        image = mi.render(scene, spp=int(spp))

    bitmap = mi.Bitmap(image).convert(mi.Bitmap.PixelFormat.RGB,
                                      mi.Struct.Type.Float32, srgb_gamma=True)
    return np.clip(np.array(bitmap), 0.0, 1.0)
