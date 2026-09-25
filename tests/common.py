from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

import bpy
import numpy as np


class BlenderIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        artifacts = Path(os.environ["BETTERREF_TEST_ARTIFACTS"])
        artifacts.mkdir(parents=True, exist_ok=True)
        if os.environ.get("BETTERREF_TEST_KEEP_ARTIFACTS") == "1":
            cls._tempdir = None
            cls.workdir = artifacts / cls.__name__
            cls.workdir.mkdir(exist_ok=True)
        else:
            cls._tempdir = tempfile.TemporaryDirectory(
                prefix=f"{cls.__name__}_", dir=artifacts
            )
            cls.workdir = Path(cls._tempdir.name)

    @classmethod
    def tearDownClass(cls):
        if cls._tempdir is not None:
            cls._tempdir.cleanup()

    def setUp(self):
        self.clear_scene()

    @staticmethod
    def clear_scene():
        if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for image in list(bpy.data.images):
            if image.name != "Render Result":
                image.use_fake_user = False
                bpy.data.images.remove(image, do_unlink=True)

    def assert_operator_finished(self, result):
        self.assertEqual({"FINISHED"}, set(result))

    # -- fixtures --------------------------------------------------------

    @staticmethod
    def gradient_pixels(width: int, height: int) -> np.ndarray:
        """Deterministic RGBA data whose value encodes the pixel coordinate.

        Encoding the position in the colour lets a test assert that the *right*
        region was sliced out, not merely that some region of the right size was.
        """
        ys, xs = np.indices((height, width), dtype=np.float32)
        pixels = np.empty((height, width, 4), dtype=np.float32)
        pixels[..., 0] = xs / max(width - 1, 1)
        pixels[..., 1] = ys / max(height - 1, 1)
        pixels[..., 2] = 0.25
        pixels[..., 3] = 1.0
        return pixels

    def make_image(self, name: str, width: int, height: int) -> bpy.types.Image:
        image = bpy.data.images.new(name, width, height, alpha=True, float_buffer=True)
        image.colorspace_settings.name = "Non-Color"
        image.pixels.foreach_set(self.gradient_pixels(width, height).ravel())
        image.update()
        return image

    def make_reference(
        self,
        name: str = "Ref",
        width: int = 64,
        height: int = 32,
        display_size: float = 5.0,
    ) -> bpy.types.Object:
        image = self.make_image(f"{name}_src", width, height)
        obj = bpy.data.objects.new(name, None)
        obj.empty_display_type = "IMAGE"
        obj.data = image
        obj.empty_display_size = display_size
        obj.empty_image_offset = (-0.5, -0.5)
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        return obj

    def make_file_reference(
        self,
        name: str = "Ref",
        width: int = 64,
        height: int = 32,
        display_size: float = 5.0,
        file_format: str = "OPEN_EXR",
    ) -> bpy.types.Object:
        """A reference backed by a real file, as a user's would be.

        make_reference builds its source with images.new(), which leaves a
        GENERATED datablock that Blender considers modified. Tests about saving
        need a source that is clean to begin with.
        """
        image = self.make_image(f"{name}_src", width, height)
        suffix = "exr" if file_format == "OPEN_EXR" else file_format.lower()
        path = str(self.workdir / f"{name}.{suffix}")
        image.filepath_raw = path
        image.file_format = file_format
        image.save()
        bpy.data.images.remove(image)

        loaded = bpy.data.images.load(path)
        obj = bpy.data.objects.new(name, None)
        obj.empty_display_type = "IMAGE"
        obj.data = loaded
        obj.empty_display_size = display_size
        obj.empty_image_offset = (-0.5, -0.5)
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        # duplicate() acts on the selection, not just the active object
        obj.select_set(True)
        return obj

    @staticmethod
    def duplicate_active() -> bpy.types.Object:
        """Shift+D on the active object, via the real duplicate operator."""
        bpy.ops.object.duplicate()
        return bpy.context.object

    @staticmethod
    def read_pixels(image: bpy.types.Image) -> np.ndarray:
        buffer = np.empty(image.size[0] * image.size[1] * 4, dtype=np.float32)
        image.pixels.foreach_get(buffer)
        return buffer.reshape(image.size[1], image.size[0], 4)
