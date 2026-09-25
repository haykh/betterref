from __future__ import annotations

import bpy

from . import utils


class BETTERREF_TT_crop(bpy.types.WorkSpaceTool):
    """Toolbar entry point into crop mode.

    Activating a tool cannot start a modal operator by itself, so the tool binds
    a click to ``betterref.crop_modal``: pick the tool, click the reference, and
    the crop interface takes over until it is confirmed or cancelled.
    """

    bl_space_type = "VIEW_3D"
    bl_context_mode = "OBJECT"
    bl_idname = "betterref.crop"
    bl_label = "Crop Reference"
    bl_description = (
        "Crop a reference image.\n"
        "Click the image to enter crop mode; the full image stays available, "
        "so a crop can always be widened again"
    )
    bl_icon = "ops.transform.resize"
    bl_keymap = (
        ("betterref.crop_modal", {"type": "LEFTMOUSE", "value": "PRESS"}, None),
        (
            "betterref.crop_reset",
            {"type": "X", "value": "PRESS", "alt": True},
            None,
        ),
    )

    # Blender calls draw_settings unbound, so it takes no self.
    def draw_settings(context, layout, tool):
        obj = context.object
        if not utils.is_image_empty(obj):
            layout.label(text="Select a reference image", icon="INFO")
            return

        source = utils.source_state(obj)[0]
        props = obj.betterref_crop
        if source is not None:
            bounds = utils.pixel_bounds(source.size[0], source.size[1], props.rect)
            layout.label(
                text=f"{bounds[2] - bounds[0]} x {bounds[3] - bounds[1]} px"
            )

        row = layout.row(align=True)
        row.prop(
            props,
            "lock_aspect",
            text="",
            icon="LOCKED" if props.lock_aspect else "UNLOCKED",
        )
        sub = row.row(align=True)
        sub.enabled = props.lock_aspect
        sub.prop(props, "aspect_ratio", text="Aspect")

        layout.operator("betterref.crop_modal", text="Crop", icon="IMAGE_REFERENCE")
        layout.operator("betterref.crop_reset", text="", icon="LOOP_BACK")
