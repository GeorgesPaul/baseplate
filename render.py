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
DEFAULT_SPP = 64      # with the denoiser on, this is already clean; ~10 s at the cap
MIN_SPP, MAX_SPP = 8, 512


def _hint_llvm():
    """Point Dr.Jit at an LLVM runtime if one is installed.

    The LLVM variants are the fast CPU path, but Dr.Jit dlopen's LLVM at
    runtime rather than bundling it, and on Windows it only finds the DLL
    via DRJIT_LIBLLVM_PATH or the normal search path. A stock machine has
    neither, which is why mitsuba silently drops to the scalar variant. If
    LLVM is installed in the usual place, hand Dr.Jit the path so the fast
    variant becomes available with no action from the user.

    Must run before mitsuba is imported, hence the call at module import.
    """
    if os.environ.get("DRJIT_LIBLLVM_PATH"):
        return
    candidates = []
    for env in ("ProgramFiles", "ProgramW6432", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            candidates.append(os.path.join(base, "LLVM", "bin", "LLVM-C.dll"))
    candidates += ["/usr/lib/libLLVM.so", "/usr/local/lib/libLLVM.dylib"]
    for path in candidates:
        if os.path.exists(path):
            os.environ["DRJIT_LIBLLVM_PATH"] = path
            return


_hint_llvm()


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


def _scene_dict(mi, parts, cam, width, height, lo, hi):
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
            "sampler": {"type": "independent", "sample_count": 16},  # overridden per pass
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


def select_variant():
    """Pick the fastest mitsuba variant this machine can actually run.

    The LLVM variants vectorise across SIMD lanes and are the fast CPU path,
    but Dr.Jit loads LLVM at runtime from a shared library that is not part
    of the wheel. If it is missing we fall back to scalar, which is slower
    per sample but still renders image tiles across every core, so this is
    not a fall back to single-threaded.

    cuda_ad_rgb is deliberately not tried: it is NVIDIA-only, and Dr.Jit has
    no AMD or Intel GPU backend, so on anything else the probe just costs
    startup time.
    """
    import mitsuba as mi

    if mi.variant() is not None:
        return mi.variant()
    for variant in ("llvm_ad_rgb", "scalar_rgb"):
        try:
            mi.set_variant(variant)
            return variant
        except Exception:
            continue
    raise RuntimeError("no usable mitsuba variant")


def make_scene(meshes, cam, aspect, window_width):
    """Build the mitsuba scene once. Returns (scene, (width, height)).

    Split from rendering so a progressive render can fire many passes at the
    same scene instead of re-parsing geometry and rebuilding the BVH for
    every one of them.

    meshes: sequence of (name, verts, tris, color, roughness, transparency).
    cam:    (eye, look_dir, up, fov_vertical_deg), arrays apart from fov.
    """
    import mitsuba as mi
    select_variant()

    width = int(min(MAX_WIDTH, max(320, window_width)))
    height = max(240, int(round(width / aspect)))

    lo = np.min([v.min(axis=0) for _, v, _, _, _, _ in meshes], axis=0)
    hi = np.max([v.max(axis=0) for _, v, _, _, _, _ in meshes], axis=0)

    # The PLY files only need to outlive load_dict; the scene holds its own
    # copy of the geometry once it is parsed.
    with tempfile.TemporaryDirectory(prefix="baseplate_render_") as tmp:
        parts = []
        for name, verts, tris, color, roughness, transparency in meshes:
            ply_path = os.path.join(tmp, name + ".ply")
            _write_ply(ply_path, verts, tris)
            parts.append((name, ply_path, color, roughness, transparency))
        scene = mi.load_dict(_scene_dict(mi, parts, cam, width, height, lo, hi))

    return scene, (width, height)


def render_pass(scene, spp, seed=0):
    """One batch of samples, as a linear (H, W, 3) float array. Linear so
    that passes can be averaged: averaging sRGB-encoded images is wrong."""
    import mitsuba as mi
    return np.array(mi.render(scene, spp=int(spp), seed=int(seed)), dtype=np.float32)


def denoise(linear):
    """Open Image Denoise on the linear image. Returns the input untouched
    if the denoiser is unavailable, since it is an optional accelerator and
    not worth failing a render over."""
    try:
        import pyoidn
    except ImportError:
        return linear

    image = np.ascontiguousarray(linear, dtype=np.float32)
    out = np.zeros_like(image)
    height, width = image.shape[:2]
    with pyoidn.Device(pyoidn.OIDN_DEVICE_TYPE_CPU) as device:
        device.commit()
        with pyoidn.Filter(device, pyoidn.OIDN_FILTER_TYPE_RT) as flt:
            flt.set_image("color", image, pyoidn.OIDN_FORMAT_FLOAT3, width, height)
            flt.set_image("output", out, pyoidn.OIDN_FORMAT_FLOAT3, width, height)
            flt.set_bool("hdr", True)
            flt.commit()
            flt.execute()
        if device.get_error() is not None:
            return linear
    return out


def to_srgb(linear):
    """Linear -> sRGB-encoded floats in [0, 1], ready for polyscope."""
    import mitsuba as mi
    bitmap = mi.Bitmap(np.ascontiguousarray(linear, dtype=np.float32))
    bitmap = bitmap.convert(mi.Bitmap.PixelFormat.RGB, mi.Struct.Type.Float32, srgb_gamma=True)
    return np.clip(np.array(bitmap), 0.0, 1.0)


def render_view(meshes, cam, aspect, window_width, spp=DEFAULT_SPP, denoised=True):
    """One-shot convenience wrapper: scene, one pass, denoise, encode."""
    scene, _ = make_scene(meshes, cam, aspect, window_width)
    linear = render_pass(scene, spp)
    if denoised:
        linear = denoise(linear)
    return to_srgb(linear)
