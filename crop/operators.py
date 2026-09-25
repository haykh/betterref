from __future__ import annotations

import bpy

from . import utils


class Crop_Begin(bpy.types.Operator):
    """Remember the source image so the reference can be cropped reversibly"""

    bl_idname = "betterref.crop_begin"
    bl_label = "Begin Crop"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return utils.is_image_empty(context.object)

    def execute(self, context: bpy.types.Context):
        obj = context.object
        if not utils.begin_crop(obj):
            self.report({"ERROR"}, "Active object has no reference image")
            return {"CANCELLED"}
        return {"FINISHED"}


class Crop_Reset(bpy.types.Operator):
    """Restore the full uncropped reference image"""

    bl_idname = "betterref.crop_reset"
    bl_label = "Reset Crop"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        obj = context.object
        return obj is not None and obj.betterref_crop.source_image is not None

    def execute(self, context: bpy.types.Context):
        obj = context.object
        if not utils.reset_crop(obj):
            self.report({"ERROR"}, "No source image recorded for this object")
            return {"CANCELLED"}
        utils.clear_cache()
        return {"FINISHED"}


class Crop_SetRect(bpy.types.Operator):
    """Set the crop rectangle directly, in normalized source coordinates"""

    bl_idname = "betterref.crop_set_rect"
    bl_label = "Set Crop Rectangle"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    min_x: bpy.props.FloatProperty(name="Left", default=0.0)
    min_y: bpy.props.FloatProperty(name="Bottom", default=0.0)
    max_x: bpy.props.FloatProperty(name="Right", default=1.0)
    max_y: bpy.props.FloatProperty(name="Top", default=1.0)

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return utils.is_image_empty(context.object)

    def execute(self, context: bpy.types.Context):
        obj = context.object
        if not utils.begin_crop(obj):
            self.report({"ERROR"}, "Active object has no reference image")
            return {"CANCELLED"}
        obj.betterref_crop.rect = (self.min_x, self.min_y, self.max_x, self.max_y)
        if not utils.apply_crop(obj):
            self.report({"ERROR"}, "Source image has no pixel data")
            return {"CANCELLED"}
        utils.clear_cache()
        return {"FINISHED"}
