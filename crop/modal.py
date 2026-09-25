"""Interactive crop mode.

Blender does not let an add-on register a new object mode, so this is a modal
operator instead -- the same mechanism the Knife tool and the sequencer
transforms use. It behaves like a mode: it takes over input, draws its own
interface, and ends on confirm or cancel.

While the mode is active the empty shows the *whole* source image, with the
discarded area dimmed. Nothing is baked until the crop is confirmed, so dragging
stays responsive no matter how large the reference is.
"""

from __future__ import annotations

import bpy
from bpy_extras import view3d_utils
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from mathutils.geometry import intersect_line_plane

from . import utils

HANDLE_RADIUS_PX = 14.0
HANDLE_SIZE_PX = 4.5
DIM_COLOR = (0.0, 0.0, 0.0, 0.65)
OUTLINE_COLOR = (1.0, 1.0, 1.0, 0.95)
THIRDS_COLOR = (1.0, 1.0, 1.0, 0.22)
HANDLE_COLOR = (1.0, 1.0, 1.0, 0.95)
HANDLE_COLOR_ACTIVE = (1.0, 0.7, 0.15, 1.0)

# Cursor shown while hovering each handle.
_CURSORS = {
    "BL": "SCROLL_XY",
    "BR": "SCROLL_XY",
    "TR": "SCROLL_XY",
    "TL": "SCROLL_XY",
    "B": "SCROLL_Y",
    "T": "SCROLL_Y",
    "L": "SCROLL_X",
    "R": "SCROLL_X",
    "MOVE": "SCROLL_XY",
}


class Crop_Modal(bpy.types.Operator):
    """Crop the reference image interactively"""

    bl_idname = "betterref.crop_modal"
    bl_label = "Crop Reference Image"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return utils.is_image_empty(context.object) and context.area is not None

    # -- lifecycle -------------------------------------------------------

    def invoke(self, context: bpy.types.Context, event):
        obj = context.object
        if not utils.begin_crop(obj):
            self.report({"ERROR"}, "Active object has no reference image")
            return {"CANCELLED"}

        props = obj.betterref_crop
        source = props.source_image
        if source is None or source.size[0] == 0 or source.size[1] == 0:
            self.report({"ERROR"}, "Source image has no pixel data")
            return {"CANCELLED"}

        self.obj = obj
        self.rect = utils.clamp_rect(props.rect)
        self.entry_rect = tuple(props.rect)
        self.entry_data = obj.data
        self.entry_display_size = obj.empty_display_size
        self.entry_offset = tuple(obj.empty_image_offset)
        self.handle = None
        self.hover = None
        self.drag_start_uv = None
        self.drag_start_rect = None
        self.area = context.area
        self.area_pointer = context.area.as_pointer()

        # Show the full source so the user can see what is being cut away.
        obj.data = source
        obj.empty_display_size = props.source_display_size
        obj.empty_image_offset = props.source_image_offset
        utils.cache_source(source)

        self._draw_view = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_3d, (), "WINDOW", "POST_VIEW"
        )
        self._draw_px = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_2d, (), "WINDOW", "POST_PIXEL"
        )
        context.workspace.status_text_set(
            "Drag handles to crop  |  Confirm: Enter/Click-away  |  Cancel: Esc/RMB"
            "  |  R: reset to full image"
        )
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def _teardown(self, context: bpy.types.Context):
        if getattr(self, "_draw_view", None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._draw_view, "WINDOW")
            self._draw_view = None
        if getattr(self, "_draw_px", None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._draw_px, "WINDOW")
            self._draw_px = None
        context.workspace.status_text_set(None)
        context.window.cursor_modal_restore()
        utils.clear_cache()
        if self.area is not None:
            self.area.tag_redraw()

    def cancel(self, context: bpy.types.Context):
        """Called when Blender aborts the modal from outside.

        Loading a file, closing the area or unregistering the add-on ends a
        modal operator without routing through modal(), so without this the
        draw handlers would outlive the operator and keep drawing.
        """
        self._cancel(context)

    def _confirm(self, context: bpy.types.Context):
        with utils.suspended():
            self.obj.betterref_crop.rect = self.rect
        self._teardown(context)
        if not utils.apply_crop(self.obj):
            self.report({"ERROR"}, "Could not bake the crop")
            return {"CANCELLED"}
        return {"FINISHED"}

    def _cancel(self, context: bpy.types.Context):
        with utils.suspended():
            self.obj.betterref_crop.rect = self.entry_rect
        self.obj.data = self.entry_data
        self.obj.empty_display_size = self.entry_display_size
        self.obj.empty_image_offset = self.entry_offset
        self._teardown(context)
        return {"CANCELLED"}

    # -- input -----------------------------------------------------------

    def modal(self, context: bpy.types.Context, event):
        if context.area is not None:
            context.area.tag_redraw()

        # Let the user keep navigating the viewport while cropping.
        if event.type in {
            "MIDDLEMOUSE",
            "WHEELUPMOUSE",
            "WHEELDOWNMOUSE",
            "NUMPAD_1",
            "NUMPAD_3",
            "NUMPAD_7",
            "NUMPAD_9",
            "NUMPAD_2",
            "NUMPAD_4",
            "NUMPAD_6",
            "NUMPAD_8",
            "NUMPAD_5",
            "NUMPAD_PERIOD",
        }:
            return {"PASS_THROUGH"}

        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            return self._cancel(context)

        if event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            return self._confirm(context)

        if event.type == "R" and event.value == "PRESS":
            self.rect = (0.0, 0.0, 1.0, 1.0)
            return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE":
            uv = self._uv_from_mouse(context, event)
            if self.handle is not None and uv is not None:
                self._drag_to(uv)
            else:
                self.hover = self._pick_handle(context, event)
                cursor = _CURSORS.get(self.hover, "DEFAULT")
                context.window.cursor_modal_set(cursor)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                picked = self._pick_handle(context, event)
                if picked is None:
                    # Clicking outside the image confirms, like clicking away
                    # from a text field.
                    return self._confirm(context)
                self.handle = picked
                self.drag_start_uv = self._uv_from_mouse(context, event)
                self.drag_start_rect = self.rect
                return {"RUNNING_MODAL"}
            if event.value == "RELEASE":
                self.handle = None
                self.drag_start_uv = None
                self.drag_start_rect = None
                return {"RUNNING_MODAL"}

        return {"RUNNING_MODAL"}

    def _drag_to(self, uv: tuple[float, float]):
        if self.drag_start_uv is None or self.drag_start_rect is None:
            return
        delta_u = uv[0] - self.drag_start_uv[0]
        delta_v = uv[1] - self.drag_start_uv[1]
        props = self.obj.betterref_crop

        if self.handle == "MOVE":
            self.rect = utils.move_rect(self.drag_start_rect, delta_u, delta_v)
            return

        rect = utils.resize_rect(
            self.drag_start_rect, self.handle, delta_u, delta_v
        )
        if props.lock_aspect:
            rect = utils.apply_aspect(
                rect, self.handle, props.aspect_ratio, utils.source_extents(self.obj)
            )
        self.rect = rect

    # -- picking ---------------------------------------------------------

    def _ray(self, context: bpy.types.Context, event):
        region = context.region
        rv3d = context.region_data
        if region is None or rv3d is None:
            return None
        coord = (event.mouse_region_x, event.mouse_region_y)
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        return origin, direction

    def _uv_from_mouse(self, context: bpy.types.Context, event):
        """Project the cursor onto the image plane and return its UV."""
        ray = self._ray(context, event)
        if ray is None:
            return None
        origin, direction = ray
        matrix = self.obj.matrix_world
        plane_co = matrix.translation
        plane_no = (matrix.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
        hit = intersect_line_plane(
            origin, origin + direction * 1.0e6, plane_co, plane_no
        )
        if hit is None:
            return None
        return utils.local_to_uv(self.obj, matrix.inverted() @ hit)

    def _region_point(self, context: bpy.types.Context, u: float, v: float):
        world = self.obj.matrix_world @ utils.uv_to_local(self.obj, u, v)
        return view3d_utils.location_3d_to_region_2d(
            context.region, context.region_data, world
        )

    def _pick_handle(self, context: bpy.types.Context, event):
        """Nearest handle within the grab radius, else MOVE inside, else None."""
        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        best = None
        best_distance = HANDLE_RADIUS_PX
        for name, (u, v) in utils.handle_uvs(self.rect).items():
            point = self._region_point(context, u, v)
            if point is None:
                continue
            distance = (Vector(point) - mouse).length
            if distance <= best_distance:
                best = name
                best_distance = distance
        if best is not None:
            return best

        uv = self._uv_from_mouse(context, event)
        if uv is None:
            return None
        min_x, min_y, max_x, max_y = self.rect
        if min_x <= uv[0] <= max_x and min_y <= uv[1] <= max_y:
            return "MOVE"
        return None

    # -- drawing ---------------------------------------------------------

    def _draw_3d(self):
        context = bpy.context
        # Blender re-wraps RNA structs on every access, so identity comparison
        # would fail even for the same area. Compare the underlying pointer.
        if context.area is None or context.area.as_pointer() != self.area_pointer:
            return
        x0, y0, x1, y1 = utils.source_quad(self.obj)
        span_x, span_y = x1 - x0, y1 - y0
        if span_x <= 0.0 or span_y <= 0.0:
            return
        min_x, min_y, max_x, max_y = self.rect
        cx0, cx1 = x0 + min_x * span_x, x0 + max_x * span_x
        cy0, cy1 = y0 + min_y * span_y, y0 + max_y * span_y

        matrix = self.obj.matrix_world

        def world(px, py):
            return matrix @ Vector((px, py, 0.0))

        # Dim the four strips outside the crop. The full source is on screen, so
        # a flat dark overlay is all that is needed.
        coords, indices = [], []
        for sx0, sy0, sx1, sy1 in (
            (x0, y0, x1, cy0),
            (x0, cy1, x1, y1),
            (x0, cy0, cx0, cy1),
            (cx1, cy0, x1, cy1),
        ):
            if sx1 - sx0 <= 0.0 or sy1 - sy0 <= 0.0:
                continue
            base = len(coords)
            coords.extend(
                (
                    world(sx0, sy0),
                    world(sx1, sy0),
                    world(sx1, sy1),
                    world(sx0, sy1),
                )
            )
            indices.extend(((base, base + 1, base + 2), (base, base + 2, base + 3)))

        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("LESS_EQUAL")
        gpu.state.depth_mask_set(False)

        if coords:
            shader = gpu.shader.from_builtin("UNIFORM_COLOR")
            batch = batch_for_shader(shader, "TRIS", {"pos": coords}, indices=indices)
            shader.bind()
            shader.uniform_float("color", DIM_COLOR)
            batch.draw(shader)

        line_shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        line_shader.bind()
        line_shader.uniform_float(
            "viewportSize", (context.region.width, context.region.height)
        )

        thirds = []
        for index in (1, 2):
            fraction = index / 3.0
            thirds.extend(
                (
                    world(cx0 + (cx1 - cx0) * fraction, cy0),
                    world(cx0 + (cx1 - cx0) * fraction, cy1),
                    world(cx0, cy0 + (cy1 - cy0) * fraction),
                    world(cx1, cy0 + (cy1 - cy0) * fraction),
                )
            )
        line_shader.uniform_float("lineWidth", 1.0)
        line_shader.uniform_float("color", THIRDS_COLOR)
        batch_for_shader(line_shader, "LINES", {"pos": thirds}).draw(line_shader)

        outline = [
            world(cx0, cy0),
            world(cx1, cy0),
            world(cx1, cy1),
            world(cx0, cy1),
            world(cx0, cy0),
        ]
        line_shader.uniform_float("lineWidth", 2.0)
        line_shader.uniform_float("color", OUTLINE_COLOR)
        batch_for_shader(line_shader, "LINE_STRIP", {"pos": outline}).draw(line_shader)

        gpu.state.depth_mask_set(True)
        gpu.state.depth_test_set("NONE")
        gpu.state.blend_set("NONE")

    def _draw_2d(self):
        context = bpy.context
        # Blender re-wraps RNA structs on every access, so identity comparison
        # would fail even for the same area. Compare the underlying pointer.
        if context.area is None or context.area.as_pointer() != self.area_pointer:
            return
        # Handles are drawn in screen space so they stay easy to grab at any zoom.
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        gpu.state.blend_set("ALPHA")
        active = self.handle or self.hover
        for name, (u, v) in utils.handle_uvs(self.rect).items():
            point = self._region_point(context, u, v)
            if point is None:
                continue
            size = HANDLE_SIZE_PX + (2.0 if name == active else 0.0)
            px, py = point
            corners = (
                (px - size, py - size),
                (px + size, py - size),
                (px + size, py + size),
                (px - size, py + size),
            )
            batch = batch_for_shader(
                shader,
                "TRIS",
                {"pos": corners},
                indices=((0, 1, 2), (0, 2, 3)),
            )
            shader.bind()
            shader.uniform_float(
                "color", HANDLE_COLOR_ACTIVE if name == active else HANDLE_COLOR
            )
            batch.draw(shader)
        gpu.state.blend_set("NONE")

        self._draw_readout(context)

    def _draw_readout(self, context: bpy.types.Context):
        import blf

        source = utils.source_state(self.obj)[0]
        if source is None:
            return
        bounds = utils.pixel_bounds(source.size[0], source.size[1], self.rect)
        text = f"{bounds[2] - bounds[0]} x {bounds[3] - bounds[1]} px"
        font = 0
        blf.size(font, 13)
        blf.color(font, 1.0, 1.0, 1.0, 0.9)
        blf.position(font, 18, 36, 0)
        blf.draw(font, text)
