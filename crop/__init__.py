_needs_reload = "bpy" in locals()

import bpy


def classes():
    from .props import BetterRef_CropProps
    from .operators import Crop_Begin, Crop_Reset, Crop_SetRect
    from .modal import Crop_Modal
    from .ui import BETTERREF_PT_crop_3dv

    return (
        BetterRef_CropProps,
        Crop_Begin,
        Crop_Reset,
        Crop_SetRect,
        Crop_Modal,
        BETTERREF_PT_crop_3dv,
    )


@bpy.app.handlers.persistent
def _pack_crops(_dummy):
    """Pack every crop before the file is written.

    Crops baked during an interactive drag are left unpacked for speed, so this
    is the point where they become ordinary packed images. Without it they stay
    flagged as modified and Blender offers to save them.
    """
    from . import utils

    utils.persist_all()


@bpy.app.handlers.persistent
def _rebuild_crops(_dummy):
    """Re-bake crops that did not survive the load.

    Packed crops come back with their pixels intact and need nothing. This is
    the fallback for files written before crops were packed, where the derived
    image is still a GENERATED datablock and therefore reloads empty.
    """
    from . import utils

    rebuilt: set[int] = set()
    for obj in bpy.data.objects:
        props = getattr(obj, "betterref_crop", None)
        if props is None or not props.is_cropped:
            continue
        derived = props.derived_image
        if derived is not None and derived.packed_file:
            continue  # pixels came back with the file
        source = props.source_image
        if source is None or source.size[0] == 0 or source.size[1] == 0:
            continue
        if derived is not None and derived.as_pointer() in rebuilt:
            # Objects that legitimately share a crop share its datablock, so
            # one rebuild already served this one.
            continue
        # allow_fork=False: regenerating is not a user edit. Forking here would
        # add a new image datablock on every file load.
        if utils.apply_crop(obj, allow_fork=False):
            current = props.derived_image
            if current is not None:
                rebuilt.add(current.as_pointer())


def register():
    from .props import BetterRef_CropProps
    from .tool import BETTERREF_TT_crop

    for cls in classes():
        bpy.utils.register_class(cls)

    bpy.types.Object.betterref_crop = bpy.props.PointerProperty(
        type=BetterRef_CropProps
    )
    bpy.utils.register_tool(
        BETTERREF_TT_crop, after={"builtin.transform"}, separator=True
    )

    if _rebuild_crops not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_rebuild_crops)
    if _pack_crops not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_pack_crops)


def unregister():
    """Tear everything down, even if a step fails.

    Unregistering has to be total: if one step raises, the rest are skipped and
    the add-on is left half-registered, which then blocks the next enable with
    errors like "Tool 'betterref.crop' already exists". Each step is guarded
    independently so one failure cannot strand the others.
    """
    from .tool import BETTERREF_TT_crop
    from . import utils

    if _rebuild_crops in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_rebuild_crops)
    if _pack_crops in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_pack_crops)

    utils.clear_cache()

    try:
        bpy.utils.unregister_tool(BETTERREF_TT_crop)
    except Exception as error:
        print(f"BetterRef: could not remove the crop tool ({error})")

    if hasattr(bpy.types.Object, "betterref_crop"):
        del bpy.types.Object.betterref_crop

    for cls in reversed(classes()):
        try:
            bpy.utils.unregister_class(cls)
        except Exception as error:
            print(f"BetterRef: could not unregister {cls.__name__} ({error})")


if _needs_reload:
    import importlib

    from . import utils, props, operators, modal, tool, ui

    importlib.reload(utils)
    importlib.reload(props)
    importlib.reload(operators)
    importlib.reload(modal)
    importlib.reload(tool)
    importlib.reload(ui)
