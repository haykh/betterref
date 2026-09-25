from __future__ import annotations

import bpy

from . import utils


class BETTERREF_PT_crop_3dv(bpy.types.Panel):
    bl_label = "Reference Crop"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BetterRef"

    def draw(self, context: bpy.types.Context):
        if (layout := self.layout) is None:
            return
        obj = context.object
        if not utils.is_image_empty(obj):
            layout.label(text="Select a reference image empty", icon="INFO")
            return

        props = obj.betterref_crop
        source = utils.source_state(obj)[0]

        layout.operator(
            "betterref.crop_modal", text="Crop Image", icon="IMAGE_REFERENCE"
        )

        if source is not None:
            bounds = utils.pixel_bounds(source.size[0], source.size[1], props.rect)
            box = layout.box()
            box.label(text=source.name, icon="IMAGE_DATA")
            box.label(
                text=f"{bounds[2] - bounds[0]} x {bounds[3] - bounds[1]} px  "
                f"(source {source.size[0]} x {source.size[1]})"
            )

        column = layout.column(align=True)
        column.prop(props, "min_x")
        column.prop(props, "max_x")
        column.separator()
        column.prop(props, "min_y")
        column.prop(props, "max_y")

        row = layout.row(align=True)
        row.prop(props, "lock_aspect")
        sub = row.row(align=True)
        sub.enabled = props.lock_aspect
        sub.prop(props, "aspect_ratio", text="")

        row = layout.row()
        row.enabled = props.is_cropped
        row.operator("betterref.crop_reset", icon="LOOP_BACK")
