_needs_reload = "bpy" in locals()

from . import crop

if _needs_reload:
    import importlib

    importlib.reload(crop)
    print("BetterRef Reloaded")


def register():
    crop.register()


def unregister():
    crop.unregister()


if __name__ == "__main__":
    register()
