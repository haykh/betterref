"""Geometry and pixel helpers for reference-image cropping.

The crop is non-destructive: the untouched source image is kept on the object
and the empty displays a *derived* image baked from a normalized rectangle.
Re-cropping always slices the source again, so widening the rectangle brings
previously discarded pixels back.
"""

from __future__ import annotations

import os
import tempfile

import bpy
from mathutils import Matrix, Vector
import numpy as np

# Rect is always (min_x, min_y, max_x, max_y), normalized to the source image
# in UV orientation (origin at the bottom-left corner, matching Image.pixels).
Rect = tuple[float, float, float, float]
Bounds = tuple[int, int, int, int]

MIN_CROP_PIXELS = 1

# Source pixels cached for the duration of a gizmo drag, keyed by image name,
# so an interactive drag pays for one foreach_get instead of one per step.
_pixel_cache: dict[str, np.ndarray] = {}

# Guards property update callbacks while we write props programmatically.
_suspend_updates = False


class suspended:
    """Context manager that disables the rect update callbacks."""

    def __enter__(self):
        global _suspend_updates
        self._previous = _suspend_updates
        _suspend_updates = True
        return self

    def __exit__(self, *_):
        global _suspend_updates
        _suspend_updates = self._previous
        return False


def updates_suspended() -> bool:
    return _suspend_updates


def is_image_empty(obj: bpy.types.Object | None) -> bool:
    return (
        obj is not None
        and obj.type == "EMPTY"
        and obj.empty_display_type == "IMAGE"
        and obj.data is not None
    )


def image_extents(width: int, height: int, display_size: float) -> tuple[float, float]:
    """Local-space width/height of an empty-image quad.

    Blender fits the image so that its *long* axis equals ``empty_display_size``
    and the short axis is scaled by the image aspect.
    """
    if width <= 0 or height <= 0:
        return (display_size, display_size)
    if width >= height:
        return (display_size, display_size * height / width)
    return (display_size * width / height, display_size)


def quad_corners(
    size: tuple[int, int], display_size: float, offset
) -> tuple[float, float, float, float]:
    """Local-space (x_min, y_min, x_max, y_max) of an empty-image quad.

    ``empty_image_offset`` is expressed in units of the quad itself, so the
    default of (-0.5, -0.5) centers the image on the object origin.
    """
    extent_x, extent_y = image_extents(size[0], size[1], display_size)
    x_min = offset[0] * extent_x
    y_min = offset[1] * extent_y
    return (x_min, y_min, x_min + extent_x, y_min + extent_y)


def pixel_bounds(width: int, height: int, rect: Rect) -> Bounds:
    """Snap a normalized rect to whole pixels, clamped to the image."""
    min_x, min_y, max_x, max_y = rect
    x0 = round(min(min_x, max_x) * width)
    x1 = round(max(min_x, max_x) * width)
    y0 = round(min(min_y, max_y) * height)
    y1 = round(max(min_y, max_y) * height)
    x0 = max(0, min(x0, width - MIN_CROP_PIXELS))
    y0 = max(0, min(y0, height - MIN_CROP_PIXELS))
    x1 = max(x0 + MIN_CROP_PIXELS, min(x1, width))
    y1 = max(y0 + MIN_CROP_PIXELS, min(y1, height))
    return (x0, y0, x1, y1)


def exact_rect(size: tuple[int, int], bounds: Bounds) -> Rect:
    """The rect that the snapped pixel bounds actually represent.

    Deriving the display transform from this instead of from the requested rect
    keeps the quad exactly aligned with the pixels that were baked.
    """
    width, height = size
    x0, y0, x1, y1 = bounds
    return (x0 / width, y0 / height, x1 / width, y1 / height)


def crop_transform(
    size: tuple[int, int], display_size: float, offset, rect: Rect
) -> tuple[float, tuple[float, float]]:
    """Display size and image offset that leave the kept region exactly in place.

    Without this the cropped image would jump to a new position the moment the
    crop is applied, which reads as a bug rather than a crop.
    """
    extent_x, extent_y = image_extents(size[0], size[1], display_size)
    min_x, min_y, max_x, max_y = rect
    span_x = max_x - min_x
    span_y = max_y - min_y
    kept_x = span_x * extent_x
    kept_y = span_y * extent_y
    new_display_size = max(kept_x, kept_y)
    new_offset = (
        (offset[0] + min_x) / span_x,
        (offset[1] + min_y) / span_y,
    )
    return new_display_size, new_offset


def read_pixels(image: bpy.types.Image, use_cache: bool = False) -> np.ndarray:
    """Flat RGBA float buffer for an image, optionally served from the drag cache."""
    if use_cache:
        cached = _pixel_cache.get(image.name)
        if cached is not None and cached.size == image.size[0] * image.size[1] * 4:
            return cached
    buffer = np.empty(image.size[0] * image.size[1] * 4, dtype=np.float32)
    image.pixels.foreach_get(buffer)
    if use_cache:
        _pixel_cache[image.name] = buffer
    return buffer


def cache_source(image: bpy.types.Image) -> None:
    read_pixels(image, use_cache=True)


def clear_cache() -> None:
    _pixel_cache.clear()


def derived_is_shared(obj: bpy.types.Object, derived: bpy.types.Image | None) -> bool:
    """True when some other object also references this derived image.

    Duplicating a reference empty (Shift+D) copies the property group verbatim,
    so the copy points at the very datablock the original is displaying. Baking
    into it would silently re-crop both. Detecting the sharing here makes the
    fork lazy: copies stay cheap until one of them is actually re-cropped.
    """
    if derived is None:
        return False
    for other in bpy.data.objects:
        if other == obj:
            continue
        if other.data == derived:
            return True
        other_props = getattr(other, "betterref_crop", None)
        if other_props is not None and other_props.derived_image == derived:
            return True
    return False


def _derived_image(
    existing: bpy.types.Image | None,
    name: str,
    width: int,
    height: int,
    source: bpy.types.Image,
) -> bpy.types.Image:
    """Reuse the derived datablock when its format still matches, else remake it.

    An already-packed datablock is never reused: packing works by reading the
    image's filepath, and a packed crop's path points at the temp file of the
    crop before it, which no longer exists. Starting fresh keeps every bake on
    the one path that packs cleanly.
    """
    if existing is not None and (
        existing.size[0] != width
        or existing.size[1] != height
        or existing.is_float != source.is_float
        or existing.packed_file
    ):
        bpy.data.images.remove(existing)
        existing = None
    if existing is None:
        existing = bpy.data.images.new(
            name, width, height, alpha=True, float_buffer=source.is_float
        )
    # Image.pixels always exchanges scene-linear floats, so any colorspace that
    # maps to itself displays identically. Float crops are tagged raw because
    # save() colour-manages on the way out, which would sRGB-encode the linear
    # values when they are packed; byte crops keep the source tag, where the
    # 8-bit quantisation follows the same curve the source used. The tag has to
    # be chosen before any pixels are written -- changing it later makes
    # Blender regenerate a generated image, blanking it.
    target = "Non-Color" if source.is_float else source.colorspace_settings.name
    try:
        existing.colorspace_settings.name = target
    except TypeError:
        existing.colorspace_settings.name = source.colorspace_settings.name
    existing.alpha_mode = source.alpha_mode
    return existing


def bake_crop(
    source: bpy.types.Image,
    bounds: Bounds,
    existing: bpy.types.Image | None = None,
    name: str = "BR_Crop",
    use_cache: bool = False,
) -> bpy.types.Image:
    """Slice ``bounds`` out of ``source`` into a derived image datablock."""
    width, height = source.size
    x0, y0, x1, y1 = bounds
    buffer = read_pixels(source, use_cache=use_cache)
    region = buffer.reshape(height, width, 4)[y0:y1, x0:x1, :]
    derived = _derived_image(existing, name, x1 - x0, y1 - y0, source)
    derived.pixels.foreach_set(np.ascontiguousarray(region).ravel())
    derived.update()
    return derived


def needs_persisting(derived: bpy.types.Image | None) -> bool:
    return derived is not None and not derived.packed_file


def _crop_filename(name: str, suffix: str) -> str:
    """A tidy filename for the packed crop, in case it is ever unpacked.

    Derived names are built from the source's, which usually already carries an
    extension, so strip it rather than ending up with "ref.png.png".
    """
    stem = name
    for extension in (".png", ".jpg", ".jpeg", ".exr", ".tif", ".tiff", ".tga"):
        if stem.lower().endswith(extension):
            stem = stem[: -len(extension)]
            break
    return f"{stem}{suffix}"


def _max_difference(image: bpy.types.Image, expected: np.ndarray) -> float:
    actual = np.empty(expected.size, dtype=np.float32)
    image.pixels.foreach_get(actual)
    return float(np.abs(actual - expected).max())


def _match_colorspace(
    derived: bpy.types.Image, expected: np.ndarray, preferred: str
) -> bool:
    """Pick the colorspace that reads the saved file back unchanged.

    An EXR stores the scene-linear values directly, so whether reading them
    back needs the image's original colorspace or a raw one depends on the
    OCIO config in use -- forcing either one blindly corrupts the other case.
    Trying and verifying is config-independent.
    """
    fallback_name, fallback_error = None, None
    for name in (preferred, "Non-Color", "Linear Rec.709"):
        try:
            derived.colorspace_settings.name = name
        except TypeError:
            continue  # not present in this OCIO config
        error = _max_difference(derived, expected)
        if error <= 1e-6:
            return True
        if fallback_error is None or error < fallback_error:
            fallback_name, fallback_error = name, error
    if fallback_name is not None:
        derived.colorspace_settings.name = fallback_name
    return False


def persist_derived(
    derived: bpy.types.Image, expected: np.ndarray | None = None
) -> bool:
    """Turn a freshly baked image into an ordinary packed image.

    A GENERATED image with pixels written into it counts as modified for the
    rest of the session, which is what makes Blender offer to save images on
    quit -- a plain reference never does that because it is a FILE image with
    nothing to write back. Blender refuses to pack generated images (auto-pack
    skips them too), so the only way out is to give the crop real file bytes
    and pack those. It then behaves exactly like a packed reference: clean, and
    carried inside the .blend with no rebuild needed on load.
    """
    is_float = derived.is_float
    suffix = ".exr" if is_float else ".png"
    handle, temp_path = tempfile.mkstemp(suffix=suffix, prefix="betterref_")
    os.close(handle)
    colorspace = derived.colorspace_settings.name
    try:
        # The colorspace is already the one that survives save(); see
        # _derived_image. It must not be touched before the write, because on a
        # still-generated image that would blank the buffer.
        # save(filepath=...) rather than assigning filepath first: pointing an
        # already-packed image at a path that is not a valid image yet makes
        # Blender log "not available, keeping packed image" on every re-crop.
        # pack() reads the image's own filepath, so it has to be pointed at the
        # freshly written file. _derived_image guarantees this only ever runs on
        # a not-yet-packed datablock, which is what keeps that path valid.
        derived.filepath_raw = temp_path
        derived.file_format = "OPEN_EXR" if is_float else "PNG"
        derived.save()
        derived.reload()
        derived.pack()
        # An Unpack should land beside the .blend, not in the temp directory.
        derived.filepath_raw = f"//{_crop_filename(derived.name, suffix)}"
        # Safety net for unusual OCIO configs: once the image is file-backed,
        # re-tagging is cheap and non-destructive, so confirm the pixels really
        # did survive and correct the tag if not.
        if expected is not None:
            _match_colorspace(derived, expected, colorspace)
    except (RuntimeError, OSError) as error:
        print(f"BetterRef: could not pack {derived.name} ({error})")
        return False
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass
    return True


def persist_all() -> int:
    """Pack every crop that is still unpacked. Used just before a file save."""
    packed = 0
    for obj in bpy.data.objects:
        props = getattr(obj, "betterref_crop", None)
        if props is None or not props.is_cropped:
            continue
        derived = props.derived_image
        if needs_persisting(derived) and persist_derived(
            derived, expected=read_pixels(derived).copy()
        ):
            packed += 1
    return packed


def begin_crop(obj: bpy.types.Object) -> bool:
    """Record the object's pristine state so cropping stays reversible."""
    props = obj.betterref_crop
    if props.source_image is not None:
        return True
    source = obj.data
    if not isinstance(source, bpy.types.Image):
        return False
    with suspended():
        props.source_image = source
        # The source stops being referenced by the object once a crop is applied.
        source.use_fake_user = True
        props.source_display_size = obj.empty_display_size
        props.source_image_offset = obj.empty_image_offset
        props.min_x, props.min_y = 0.0, 0.0
        props.max_x, props.max_y = 1.0, 1.0
    return True


def apply_crop(
    obj: bpy.types.Object,
    use_cache: bool = False,
    allow_fork: bool = True,
    persist: bool = True,
) -> bool:
    """Bake the current rect and re-anchor the empty so nothing shifts."""
    props = obj.betterref_crop
    source = props.source_image
    if source is None or source.size[0] == 0 or source.size[1] == 0:
        return False

    size = (source.size[0], source.size[1])
    bounds = pixel_bounds(size[0], size[1], props.rect)
    rect = exact_rect(size, bounds)

    # Copy-on-write: a datablock another object is still showing is never
    # baked into, so duplicated references crop independently. Forking is for
    # user edits only -- regenerating after a file load must reuse what is
    # there, or every reload would split shared crops into new datablocks.
    existing = props.derived_image
    if allow_fork and derived_is_shared(obj, existing):
        existing = None

    derived = bake_crop(
        source,
        bounds,
        existing=existing,
        name=f"BR_Crop_{source.name}",
        use_cache=use_cache,
    )
    display_size, offset = crop_transform(
        size, props.source_display_size, props.source_image_offset, rect
    )

    if persist:
        # Skipped while a slider or handle is being dragged: writing a temp
        # file every tick would stall the interaction. A save_pre handler packs
        # anything still outstanding before the file is written.
        persist_derived(derived, expected=read_pixels(derived).copy())

    with suspended():
        props.derived_image = derived
        props.is_cropped = bounds != (0, 0, size[0], size[1])
    obj.data = derived
    obj.empty_display_size = display_size
    obj.empty_image_offset = offset
    return True


def reset_crop(obj: bpy.types.Object) -> bool:
    """Put the untouched source image back and forget the derived datablock."""
    props = obj.betterref_crop
    source = props.source_image
    if source is None:
        return False
    derived = props.derived_image
    with suspended():
        props.min_x, props.min_y = 0.0, 0.0
        props.max_x, props.max_y = 1.0, 1.0
        props.is_cropped = False
        props.derived_image = None
    obj.data = source
    obj.empty_display_size = props.source_display_size
    obj.empty_image_offset = props.source_image_offset
    if derived is not None and derived.users == 0:
        bpy.data.images.remove(derived)
    return True


def source_state(obj: bpy.types.Object):
    """Image, display size and offset that the crop rect is measured against.

    Before the first crop these come straight off the object; afterwards they
    come from the recorded pristine state, because by then the object is showing
    the derived image at a different size and offset.

    Reading this instead of requiring initialized props lets the gizmos draw
    correctly without writing to the object, which a draw-time callback must not
    do.
    """
    props = obj.betterref_crop
    if props.source_image is not None:
        return (
            props.source_image,
            props.source_display_size,
            tuple(props.source_image_offset),
        )
    image = obj.data if isinstance(obj.data, bpy.types.Image) else None
    return (image, obj.empty_display_size, tuple(obj.empty_image_offset))


def source_size(obj: bpy.types.Object) -> tuple[int, int]:
    image = source_state(obj)[0]
    return (image.size[0], image.size[1]) if image else (1, 1)


def source_extents(obj: bpy.types.Object) -> tuple[float, float]:
    _, display_size, _ = source_state(obj)
    size = source_size(obj)
    return image_extents(size[0], size[1], display_size)


def source_quad(obj: bpy.types.Object) -> tuple[float, float, float, float]:
    _, display_size, offset = source_state(obj)
    return quad_corners(source_size(obj), display_size, offset)


def cage_matrix(obj: bpy.types.Object) -> Matrix:
    """World matrix placing a cage gizmo at the center of the source quad."""
    x_min, y_min, x_max, y_max = source_quad(obj)
    center = Vector(((x_min + x_max) * 0.5, (y_min + y_max) * 0.5, 0.0))
    return obj.matrix_world @ Matrix.Translation(center)


def rect_to_offset_matrix(rect: Rect, extents: tuple[float, float]) -> Matrix:
    """Cage offset matrix: translate to the rect center, scale by its span."""
    min_x, min_y, max_x, max_y = rect
    span_x = max(max_x - min_x, 1e-6)
    span_y = max(max_y - min_y, 1e-6)
    center_x = ((min_x + max_x) * 0.5 - 0.5) * extents[0]
    center_y = ((min_y + max_y) * 0.5 - 0.5) * extents[1]
    matrix = Matrix.Translation((center_x, center_y, 0.0))
    matrix[0][0] = span_x
    matrix[1][1] = span_y
    return matrix


def offset_matrix_to_rect(matrix, extents: tuple[float, float]) -> Rect:
    """Inverse of :func:`rect_to_offset_matrix`, clamped to the source image."""
    span_x = abs(matrix[0][0])
    span_y = abs(matrix[1][1])
    center_x = matrix[0][3] / extents[0] + 0.5 if extents[0] else 0.5
    center_y = matrix[1][3] / extents[1] + 0.5 if extents[1] else 0.5
    min_x = center_x - span_x * 0.5
    max_x = center_x + span_x * 0.5
    min_y = center_y - span_y * 0.5
    max_y = center_y + span_y * 0.5
    return clamp_rect((min_x, min_y, max_x, max_y))


CORNER_HANDLES = ("BL", "BR", "TR", "TL")
EDGE_HANDLES = ("B", "R", "T", "L")
HANDLES = CORNER_HANDLES + EDGE_HANDLES

MIN_SPAN = 1e-3


def handle_uvs(rect: Rect) -> dict[str, tuple[float, float]]:
    """Normalized positions of the eight drag handles around a rect."""
    min_x, min_y, max_x, max_y = rect
    mid_x = (min_x + max_x) * 0.5
    mid_y = (min_y + max_y) * 0.5
    return {
        "BL": (min_x, min_y),
        "BR": (max_x, min_y),
        "TR": (max_x, max_y),
        "TL": (min_x, max_y),
        "B": (mid_x, min_y),
        "R": (max_x, mid_y),
        "T": (mid_x, max_y),
        "L": (min_x, mid_y),
    }


def move_rect(rect: Rect, delta_u: float, delta_v: float) -> Rect:
    """Slide the whole rect, keeping its size and staying inside the image."""
    min_x, min_y, max_x, max_y = rect
    span_x = max_x - min_x
    span_y = max_y - min_y
    min_x = min(max(min_x + delta_u, 0.0), 1.0 - span_x)
    min_y = min(max(min_y + delta_v, 0.0), 1.0 - span_y)
    return (min_x, min_y, min_x + span_x, min_y + span_y)


def resize_rect(
    rect: Rect, handle: str, delta_u: float, delta_v: float, minimum: float = MIN_SPAN
) -> Rect:
    """Drag one handle, holding the opposite edges still."""
    min_x, min_y, max_x, max_y = rect
    if handle in ("BL", "L", "TL"):
        min_x = min(max(min_x + delta_u, 0.0), max_x - minimum)
    if handle in ("BR", "R", "TR"):
        max_x = max(min(max_x + delta_u, 1.0), min_x + minimum)
    if handle in ("BL", "B", "BR"):
        min_y = min(max(min_y + delta_v, 0.0), max_y - minimum)
    if handle in ("TL", "T", "TR"):
        max_y = max(min(max_y + delta_v, 1.0), min_y + minimum)
    return (min_x, min_y, max_x, max_y)


def apply_aspect(
    rect: Rect,
    handle: str,
    aspect: float,
    extents: tuple[float, float],
    minimum: float = MIN_SPAN,
) -> Rect:
    """Force a rect to a width/height ratio, anchored opposite the dragged handle.

    The ratio is measured in world units, not pixels, so a locked square crop
    looks square in the viewport whatever the image aspect is.
    """
    if aspect <= 0.0 or extents[0] <= 0.0 or extents[1] <= 0.0:
        return rect
    min_x, min_y, max_x, max_y = rect
    world_x = (max_x - min_x) * extents[0]
    world_y = (max_y - min_y) * extents[1]

    if handle in ("L", "R"):
        world_y = world_x / aspect
    elif handle in ("T", "B"):
        world_x = world_y * aspect
    elif world_x / max(world_y, 1e-9) > aspect:
        world_x = world_y * aspect
    else:
        world_y = world_x / aspect

    span_x = max(world_x / extents[0], minimum)
    span_y = max(world_y / extents[1], minimum)

    # How far the rect can grow from its anchor before leaving the image.
    center_x = (min_x + max_x) * 0.5
    center_y = (min_y + max_y) * 0.5
    if handle in ("BL", "L", "TL"):
        room_x = max_x
    elif handle in ("BR", "R", "TR"):
        room_x = 1.0 - min_x
    else:
        room_x = 2.0 * min(center_x, 1.0 - center_x)
    if handle in ("BL", "B", "BR"):
        room_y = max_y
    elif handle in ("TL", "T", "TR"):
        room_y = 1.0 - min_y
    else:
        room_y = 2.0 * min(center_y, 1.0 - center_y)

    # Shrink both axes together rather than clamping one, which would quietly
    # break the very ratio the lock is meant to hold.
    scale = min(1.0, room_x / span_x if span_x else 1.0, room_y / span_y if span_y else 1.0)
    span_x = max(span_x * scale, minimum)
    span_y = max(span_y * scale, minimum)

    if handle in ("BL", "L", "TL"):
        min_x = max_x - span_x
    elif handle in ("BR", "R", "TR"):
        max_x = min_x + span_x
    else:
        min_x, max_x = center_x - span_x * 0.5, center_x + span_x * 0.5

    if handle in ("BL", "B", "BR"):
        min_y = max_y - span_y
    elif handle in ("TL", "T", "TR"):
        max_y = min_y + span_y
    else:
        min_y, max_y = center_y - span_y * 0.5, center_y + span_y * 0.5

    return clamp_rect((min_x, min_y, max_x, max_y), minimum)


def uv_to_local(obj: bpy.types.Object, u: float, v: float) -> Vector:
    """Point on the source image plane, in object-local space."""
    x0, y0, x1, y1 = source_quad(obj)
    return Vector((x0 + u * (x1 - x0), y0 + v * (y1 - y0), 0.0))


def local_to_uv(obj: bpy.types.Object, point: Vector) -> tuple[float, float]:
    """Inverse of :func:`uv_to_local`."""
    x0, y0, x1, y1 = source_quad(obj)
    span_x = x1 - x0 if x1 != x0 else 1.0
    span_y = y1 - y0 if y1 != y0 else 1.0
    return ((point.x - x0) / span_x, (point.y - y0) / span_y)


def clamp_rect(rect: Rect, minimum: float = 1e-3) -> Rect:
    """Keep the rect inside the image and never let it collapse."""
    min_x, min_y, max_x, max_y = rect
    min_x, max_x = sorted((min_x, max_x))
    min_y, max_y = sorted((min_y, max_y))
    min_x = min(max(min_x, 0.0), 1.0 - minimum)
    min_y = min(max(min_y, 0.0), 1.0 - minimum)
    max_x = max(min(max_x, 1.0), min_x + minimum)
    max_y = max(min(max_y, 1.0), min_y + minimum)
    return (min_x, min_y, max_x, max_y)
