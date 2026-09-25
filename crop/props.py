from __future__ import annotations

import bpy

from . import utils


def _rect_changed(self, context: bpy.types.Context):
    """Re-bake whenever the rect moves, from the gizmo or from the panel.

    A full-resolution slice costs a few milliseconds once the source buffer is
    cached, so the crop can stay live during a drag instead of only committing
    on release.
    """
    if utils.updates_suspended():
        return
    obj = self.id_data
    if not isinstance(obj, bpy.types.Object):
        return
    # persist=False: this fires on every tick of a slider drag, and packing
    # writes a temp file. The save_pre handler packs it before the file lands.
    utils.apply_crop(obj, use_cache=True, persist=False)


class BetterRef_CropProps(bpy.types.PropertyGroup):
    """Per-object crop state, stored on the reference empty."""

    def _rect_get(self) -> tuple[float, float, float, float]:
        return (self.min_x, self.min_y, self.max_x, self.max_y)

    def _rect_set(self, value) -> None:
        min_x, min_y, max_x, max_y = utils.clamp_rect(tuple(value))
        self.min_x, self.min_y = min_x, min_y
        self.max_x, self.max_y = max_x, max_y

    rect = property(_rect_get, _rect_set)

    source_image: bpy.props.PointerProperty(
        type=bpy.types.Image,
        name="Source",
        description="Untouched image the crop is sliced from",
    )
    derived_image: bpy.props.PointerProperty(
        type=bpy.types.Image,
        name="Cropped",
        description="Generated image currently shown on the empty",
    )
    is_cropped: bpy.props.BoolProperty(
        name="Is Cropped",
        description="Whether the displayed image is a crop of the source",
        default=False,
    )

    min_x: bpy.props.FloatProperty(
        name="Left",
        description="Left edge of the crop, as a fraction of the source width",
        default=0.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_rect_changed,
    )
    min_y: bpy.props.FloatProperty(
        name="Bottom",
        description="Bottom edge of the crop, as a fraction of the source height",
        default=0.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_rect_changed,
    )
    max_x: bpy.props.FloatProperty(
        name="Right",
        description="Right edge of the crop, as a fraction of the source width",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_rect_changed,
    )
    max_y: bpy.props.FloatProperty(
        name="Top",
        description="Top edge of the crop, as a fraction of the source height",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_rect_changed,
    )

    source_display_size: bpy.props.FloatProperty(
        name="Source Display Size",
        description="empty_display_size before the first crop was applied",
        default=1.0,
    )
    source_image_offset: bpy.props.FloatVectorProperty(
        name="Source Image Offset",
        description="empty_image_offset before the first crop was applied",
        size=2,
        default=(-0.5, -0.5),
    )

    lock_aspect: bpy.props.BoolProperty(
        name="Lock Aspect",
        description="Keep the crop at a fixed aspect ratio while dragging",
        default=False,
    )
    aspect_ratio: bpy.props.FloatProperty(
        name="Aspect Ratio",
        description="Width divided by height the crop is held at",
        default=1.0,
        min=0.01,
        max=100.0,
    )
