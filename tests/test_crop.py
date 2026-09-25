"""Integration tests: run the real operators against real Blender datablocks."""

from __future__ import annotations

import bpy
from mathutils import Vector
import numpy as np

from betterref.crop import utils

from .common import BlenderIntegrationTest


class CropOperatorTests(BlenderIntegrationTest):
    def test_begin_records_the_pristine_state(self):
        obj = self.make_reference(width=64, height=32, display_size=5.0)
        source = obj.data
        self.assert_operator_finished(bpy.ops.betterref.crop_begin())

        props = obj.betterref_crop
        self.assertIs(source, props.source_image)
        self.assertTrue(source.use_fake_user)
        self.assertAlmostEqual(5.0, props.source_display_size)
        self.assertEqual((0.0, 0.0, 1.0, 1.0), props.rect)
        self.assertFalse(props.is_cropped)

    def test_begin_is_idempotent(self):
        obj = self.make_reference()
        source = obj.data
        bpy.ops.betterref.crop_begin()
        bpy.ops.betterref.crop_set_rect(min_x=0.25, min_y=0.25, max_x=0.75, max_y=0.75)
        bpy.ops.betterref.crop_begin()
        # A second begin must not adopt the cropped image as the new source.
        self.assertIs(source, obj.betterref_crop.source_image)

    def test_crop_produces_the_expected_pixel_dimensions(self):
        obj = self.make_reference(width=64, height=32)
        self.assert_operator_finished(
            bpy.ops.betterref.crop_set_rect(
                min_x=0.25, min_y=0.25, max_x=0.75, max_y=0.75
            )
        )
        self.assertEqual([32, 16], list(obj.data.size))
        self.assertTrue(obj.betterref_crop.is_cropped)
        self.assertIsNot(obj.data, obj.betterref_crop.source_image)

    def test_crop_slices_the_correct_region(self):
        obj = self.make_reference(width=64, height=32)
        source_pixels = self.read_pixels(obj.data)
        bpy.ops.betterref.crop_set_rect(min_x=0.25, min_y=0.5, max_x=0.75, max_y=1.0)

        expected = source_pixels[16:32, 16:48, :]
        actual = self.read_pixels(obj.data)
        self.assertEqual(expected.shape, actual.shape)
        np.testing.assert_allclose(expected, actual, atol=1e-6)

    def test_crop_snaps_to_whole_pixels(self):
        # A rect between pixel edges is rounded to the nearest whole pixel; the
        # display transform is then derived from the snapped rect, not the
        # requested one, so quad and pixels stay exactly aligned.
        obj = self.make_reference(width=64, height=32)
        bpy.ops.betterref.crop_set_rect(min_x=0.3, min_y=0.1, max_x=0.8, max_y=0.6)
        self.assertEqual([32, 16], list(obj.data.size))

    def test_operator_poll_rejects_non_reference_objects(self):
        mesh = bpy.data.meshes.new("M")
        obj = bpy.data.objects.new("Cube", mesh)
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        self.assertFalse(bpy.ops.betterref.crop_begin.poll())
        self.assertFalse(bpy.ops.betterref.crop_modal.poll())


class CropAnchoringTests(BlenderIntegrationTest):
    """The kept pixels must occupy the same world space after cropping."""

    @staticmethod
    def _world_of_uv(obj: bpy.types.Object, u: float, v: float) -> Vector:
        image = obj.data
        x0, y0, x1, y1 = utils.quad_corners(
            (image.size[0], image.size[1]),
            obj.empty_display_size,
            obj.empty_image_offset,
        )
        return obj.matrix_world @ Vector(
            (x0 + u * (x1 - x0), y0 + v * (y1 - y0), 0.0)
        )

    def assert_anchored(self, obj, rect, samples=((0.0, 0.0), (1.0, 1.0), (0.5, 0.5))):
        # The crop snaps to whole pixels, so the region that actually survives is
        # the snapped rect. That is the region whose position must not change.
        source = obj.betterref_crop.source_image or obj.data
        size = (source.size[0], source.size[1])
        min_x, min_y, max_x, max_y = utils.exact_rect(
            size, utils.pixel_bounds(size[0], size[1], rect)
        )
        before = [
            self._world_of_uv(
                obj,
                min_x + u * (max_x - min_x),
                min_y + v * (max_y - min_y),
            )
            for u, v in samples
        ]
        bpy.ops.betterref.crop_set_rect(
            min_x=rect[0], min_y=rect[1], max_x=rect[2], max_y=rect[3]
        )
        after = [self._world_of_uv(obj, u, v) for u, v in samples]
        for expected, actual in zip(before, after):
            self.assertAlmostEqual(expected.x, actual.x, places=4)
            self.assertAlmostEqual(expected.y, actual.y, places=4)
            self.assertAlmostEqual(expected.z, actual.z, places=4)

    def test_centered_crop_does_not_move_content(self):
        obj = self.make_reference(width=64, height=32, display_size=5.0)
        self.assert_anchored(obj, (0.25, 0.25, 0.75, 0.75))

    def test_corner_crop_does_not_move_content(self):
        obj = self.make_reference(width=64, height=32, display_size=5.0)
        self.assert_anchored(obj, (0.0, 0.5, 0.5, 1.0))

    def test_portrait_crop_does_not_move_content(self):
        obj = self.make_reference(width=32, height=96, display_size=3.0)
        self.assert_anchored(obj, (0.125, 0.25, 0.625, 0.75))

    def test_crop_survives_a_transformed_object(self):
        obj = self.make_reference(width=64, height=32, display_size=5.0)
        obj.location = (1.5, -2.0, 0.75)
        obj.rotation_euler = (0.4, 0.2, 1.1)
        obj.scale = (2.0, 2.0, 2.0)
        bpy.context.view_layer.update()
        self.assert_anchored(obj, (0.3, 0.1, 0.8, 0.6))


class CropReversibilityTests(BlenderIntegrationTest):
    def test_widening_the_crop_recovers_discarded_pixels(self):
        obj = self.make_reference(width=64, height=32)
        source_pixels = self.read_pixels(obj.betterref_crop.source_image or obj.data)

        bpy.ops.betterref.crop_set_rect(min_x=0.4, min_y=0.4, max_x=0.6, max_y=0.6)
        self.assertEqual([12, 6], list(obj.data.size))

        # Going back out must re-slice the source, not upscale the tight crop.
        bpy.ops.betterref.crop_set_rect(min_x=0.0, min_y=0.0, max_x=1.0, max_y=1.0)
        self.assertEqual([64, 32], list(obj.data.size))
        np.testing.assert_allclose(
            source_pixels, self.read_pixels(obj.data), atol=1e-6
        )

    def test_reset_restores_the_source_image_and_transform(self):
        obj = self.make_reference(width=64, height=32, display_size=5.0)
        source = obj.data
        original_offset = tuple(obj.empty_image_offset)

        bpy.ops.betterref.crop_set_rect(min_x=0.2, min_y=0.3, max_x=0.7, max_y=0.8)
        self.assertIsNot(source, obj.data)

        self.assert_operator_finished(bpy.ops.betterref.crop_reset())
        self.assertIs(source, obj.data)
        self.assertAlmostEqual(5.0, obj.empty_display_size)
        self.assertAlmostEqual(original_offset[0], obj.empty_image_offset[0])
        self.assertAlmostEqual(original_offset[1], obj.empty_image_offset[1])
        self.assertFalse(obj.betterref_crop.is_cropped)
        self.assertEqual((0.0, 0.0, 1.0, 1.0), obj.betterref_crop.rect)

    def test_successive_crops_do_not_compound(self):
        obj = self.make_reference(width=64, height=32)
        bpy.ops.betterref.crop_set_rect(min_x=0.25, min_y=0.25, max_x=0.75, max_y=0.75)
        bpy.ops.betterref.crop_set_rect(min_x=0.25, min_y=0.25, max_x=0.75, max_y=0.75)
        # The rect is always relative to the source, so the result is unchanged.
        self.assertEqual([32, 16], list(obj.data.size))

    def test_source_datablock_is_reused_not_duplicated(self):
        self.make_reference(width=64, height=32)
        count_before = len(bpy.data.images)
        for span in (0.8, 0.6, 0.4):
            margin = (1.0 - span) * 0.5
            bpy.ops.betterref.crop_set_rect(
                min_x=margin, min_y=margin, max_x=1.0 - margin, max_y=1.0 - margin
            )
        # One source plus one derived image, however many times we re-crop.
        self.assertEqual(count_before + 1, len(bpy.data.images))


class DuplicateIndependenceTests(BlenderIntegrationTest):
    """Shift+D copies the property group, including the derived-image pointer.

    Without a copy-on-write fork, re-cropping the copy bakes into the datablock
    the original is still displaying and silently re-crops both.
    """

    def test_cropping_a_copy_leaves_the_original_untouched(self):
        original = self.make_reference(width=64, height=32)
        bpy.ops.betterref.crop_set_rect(min_x=0.25, min_y=0.25, max_x=0.75, max_y=0.75)
        original_image = original.data
        original_pixels = self.read_pixels(original_image).copy()

        copy = self.duplicate_active()
        self.assertIsNot(copy, original)
        # Right after duplicating, sharing is correct: they are the same crop.
        self.assertEqual(original.data, copy.data)

        bpy.ops.betterref.crop_set_rect(min_x=0.0, min_y=0.0, max_x=0.5, max_y=0.5)

        self.assertNotEqual(original.data, copy.data)
        self.assertEqual(original_image, original.data)
        np.testing.assert_allclose(
            original_pixels, self.read_pixels(original.data), atol=1e-6
        )

    def test_each_copy_shows_its_own_region(self):
        original = self.make_reference(width=64, height=32)
        source_pixels = self.read_pixels(original.data).copy()

        bpy.ops.betterref.crop_set_rect(min_x=0.0, min_y=0.0, max_x=0.5, max_y=0.5)
        copy = self.duplicate_active()
        bpy.ops.betterref.crop_set_rect(min_x=0.5, min_y=0.5, max_x=1.0, max_y=1.0)

        np.testing.assert_allclose(
            source_pixels[0:16, 0:32, :], self.read_pixels(original.data), atol=1e-6
        )
        np.testing.assert_allclose(
            source_pixels[16:32, 32:64, :], self.read_pixels(copy.data), atol=1e-6
        )

    def test_cropping_the_original_after_duplicating_also_forks(self):
        original = self.make_reference(width=64, height=32)
        bpy.ops.betterref.crop_set_rect(min_x=0.25, min_y=0.25, max_x=0.75, max_y=0.75)

        copy = self.duplicate_active()
        copy_image = copy.data
        copy_pixels = self.read_pixels(copy_image).copy()

        # Re-crop the ORIGINAL this time; the copy must not move.
        bpy.context.view_layer.objects.active = original
        bpy.ops.betterref.crop_set_rect(min_x=0.0, min_y=0.0, max_x=0.25, max_y=0.25)

        self.assertNotEqual(original.data, copy.data)
        self.assertEqual(copy_image, copy.data)
        np.testing.assert_allclose(
            copy_pixels, self.read_pixels(copy.data), atol=1e-6
        )

    def test_duplicating_an_uncropped_reference_stays_independent(self):
        original = self.make_reference(width=64, height=32)
        copy = self.duplicate_active()
        bpy.ops.betterref.crop_set_rect(min_x=0.25, min_y=0.25, max_x=0.75, max_y=0.75)

        self.assertEqual([32, 16], list(copy.data.size))
        self.assertEqual([64, 32], list(original.data.size))

    def test_the_source_image_stays_shared(self):
        original = self.make_reference(width=64, height=32)
        bpy.ops.betterref.crop_set_rect(min_x=0.25, min_y=0.25, max_x=0.75, max_y=0.75)
        copy = self.duplicate_active()
        bpy.ops.betterref.crop_set_rect(min_x=0.0, min_y=0.0, max_x=0.5, max_y=0.5)

        # Forking the derived image must not fork the source as well: sharing it
        # is what keeps duplicates cheap and keeps both re-widenable.
        self.assertEqual(
            original.betterref_crop.source_image, copy.betterref_crop.source_image
        )
        sources = [i for i in bpy.data.images if i.name.startswith("Ref_src")]
        self.assertEqual(1, len(sources))

    def test_resetting_a_copy_does_not_disturb_the_original(self):
        original = self.make_reference(width=64, height=32)
        bpy.ops.betterref.crop_set_rect(min_x=0.25, min_y=0.25, max_x=0.75, max_y=0.75)
        original_image = original.data
        original_pixels = self.read_pixels(original_image).copy()

        copy = self.duplicate_active()
        self.assert_operator_finished(bpy.ops.betterref.crop_reset())

        self.assertEqual([64, 32], list(copy.data.size))
        self.assertFalse(copy.betterref_crop.is_cropped)
        # The original still has its crop, and its datablock was not deleted.
        self.assertEqual(original_image, original.data)
        self.assertEqual([32, 16], list(original.data.size))
        np.testing.assert_allclose(
            original_pixels, self.read_pixels(original.data), atol=1e-6
        )

    def test_panel_sliders_on_a_copy_leave_the_original_alone(self):
        """The N-panel path goes through the property update callback.

        Reported as the case that broke: the sliders write straight to the
        property group rather than going through an operator.
        """
        original = self.make_reference(width=64, height=32)
        bpy.ops.betterref.crop_set_rect(min_x=0.25, min_y=0.25, max_x=0.75, max_y=0.75)
        original_image = original.data
        original_pixels = self.read_pixels(original_image).copy()
        original_rect = tuple(original.betterref_crop.rect)

        copy = self.duplicate_active()
        # Exactly what dragging a slider does.
        copy.betterref_crop.max_x = 0.5
        copy.betterref_crop.min_y = 0.0

        self.assertNotEqual(original.data, copy.data)
        self.assertEqual(original_image, original.data)
        np.testing.assert_allclose(
            original_pixels, self.read_pixels(original.data), atol=1e-6
        )
        # The copy's own rect must not have leaked back either.
        self.assertEqual(original_rect, tuple(original.betterref_crop.rect))

    def test_panel_sliders_write_to_the_right_object(self):
        original = self.make_reference(width=64, height=32)
        bpy.ops.betterref.crop_begin()
        copy = self.duplicate_active()

        copy.betterref_crop.max_x = 0.5
        self.assertAlmostEqual(0.5, copy.betterref_crop.max_x)
        self.assertAlmostEqual(1.0, original.betterref_crop.max_x)

    def test_repeated_duplicates_each_get_their_own_image(self):
        self.make_reference(width=64, height=32)
        bpy.ops.betterref.crop_set_rect(min_x=0.25, min_y=0.25, max_x=0.75, max_y=0.75)

        copies = []
        for index in range(3):
            copies.append(self.duplicate_active())
            span = 0.2 + index * 0.2
            bpy.ops.betterref.crop_set_rect(
                min_x=0.0, min_y=0.0, max_x=span, max_y=span
            )

        shown = [copy.data.as_pointer() for copy in copies]
        self.assertEqual(len(shown), len(set(shown)))


class CropPropertyTests(BlenderIntegrationTest):
    def test_editing_a_rect_property_re_bakes_immediately(self):
        obj = self.make_reference(width=64, height=32)
        bpy.ops.betterref.crop_begin()
        obj.betterref_crop.max_x = 0.5
        self.assertEqual([32, 32], list(obj.data.size))

    def test_rect_setter_clamps_inverted_input(self):
        obj = self.make_reference(width=64, height=32)
        bpy.ops.betterref.crop_begin()
        obj.betterref_crop.rect = (0.9, 0.9, 0.1, 0.1)
        min_x, min_y, max_x, max_y = obj.betterref_crop.rect
        self.assertLess(min_x, max_x)
        self.assertLess(min_y, max_y)

    def test_float_and_colorspace_are_carried_to_the_crop(self):
        obj = self.make_reference(width=64, height=32)
        source = obj.data
        bpy.ops.betterref.crop_set_rect(min_x=0.0, min_y=0.0, max_x=0.5, max_y=0.5)
        self.assertEqual(source.is_float, obj.data.is_float)
        self.assertEqual(
            source.colorspace_settings.name, obj.data.colorspace_settings.name
        )


class CropPersistenceTests(BlenderIntegrationTest):
    """Derived images are regenerated on load rather than stored in the .blend."""

    def _crop_image_names(self):
        return sorted(i.name for i in bpy.data.images if i.name.startswith("BR_Crop"))

    def test_reloading_does_not_add_image_datablocks(self):
        """Reopening must not invent new images.

        Regression guard: copy-on-write forking ran during the post-load
        rebuild too, so every reopen split a legitimately shared crop into an
        extra datablock that was not there when the file was saved.
        """
        self.make_file_reference()
        bpy.ops.betterref.crop_set_rect(min_x=0.25, min_y=0.25, max_x=0.75, max_y=0.75)
        # Shift+D without re-cropping: both objects share one derived image,
        # which is correct -- they are the same crop.
        bpy.ops.object.duplicate()
        before = self._crop_image_names()
        self.assertEqual(1, len(before))

        blend_path = str(self.workdir / "shared.blend")
        bpy.ops.wm.save_as_mainfile(filepath=blend_path)
        for _ in range(3):
            bpy.ops.wm.open_mainfile(filepath=blend_path)
            self.assertEqual(before, self._crop_image_names())
            bpy.ops.wm.save_mainfile()
            self.assertEqual(before, self._crop_image_names())

    def test_reload_keeps_independently_cropped_copies_separate(self):
        self.make_file_reference()
        bpy.ops.betterref.crop_set_rect(min_x=0.25, min_y=0.25, max_x=0.75, max_y=0.75)
        bpy.ops.object.duplicate()
        copy = bpy.context.object
        copy.name = "RefCopy"
        bpy.ops.betterref.crop_set_rect(min_x=0.0, min_y=0.0, max_x=0.5, max_y=0.5)
        expected_names = self._crop_image_names()
        self.assertEqual(2, len(expected_names))

        blend_path = str(self.workdir / "forked.blend")
        bpy.ops.wm.save_as_mainfile(filepath=blend_path)
        bpy.ops.wm.open_mainfile(filepath=blend_path)

        self.assertEqual(expected_names, self._crop_image_names())
        reloaded_original = bpy.data.objects["Ref"]
        reloaded_copy = bpy.data.objects["RefCopy"]
        self.assertNotEqual(reloaded_original.data, reloaded_copy.data)
        self.assertEqual([32, 16], list(reloaded_original.data.size))
        self.assertEqual([32, 16], list(reloaded_copy.data.size))

    def test_crop_is_rebuilt_after_save_and_reload(self):
        obj = self.make_file_reference()
        bpy.ops.betterref.crop_set_rect(min_x=0.25, min_y=0.5, max_x=0.75, max_y=1.0)
        expected = self.read_pixels(obj.data).copy()
        expected_display_size = obj.empty_display_size

        blend_path = str(self.workdir / "persistence.blend")
        bpy.ops.wm.save_as_mainfile(filepath=blend_path)
        bpy.ops.wm.open_mainfile(filepath=blend_path)

        reloaded = bpy.data.objects["Ref"]
        props = reloaded.betterref_crop
        self.assertTrue(props.is_cropped)
        self.assertEqual([32, 16], list(reloaded.data.size))
        self.assertAlmostEqual(expected_display_size, reloaded.empty_display_size, 4)
        np.testing.assert_allclose(
            expected, self.read_pixels(reloaded.data), atol=1e-5
        )


class CropPackingTests(BlenderIntegrationTest):
    """Crops are packed so Blender treats them like any other reference.

    A GENERATED image with written pixels is permanently 'modified', which is
    what made Blender offer to save images on quit. Blender refuses to pack
    generated images (auto-pack skips them too), so the crop is given real file
    bytes and packed.
    """

    @staticmethod
    def _prompts_to_save_images() -> bool:
        """The same condition Blender's own save-modified-images UI uses."""
        return bpy.ops.image.save_all_modified.poll()

    def test_plain_reference_never_prompts(self):
        self.make_file_reference(width=64, height=32)
        self.assertFalse(self._prompts_to_save_images())

    def test_cropping_does_not_make_blender_want_to_save_images(self):
        self.make_file_reference(width=64, height=32)
        bpy.ops.betterref.crop_set_rect(min_x=0.25, min_y=0.25, max_x=0.75, max_y=0.75)
        self.assertFalse(self._prompts_to_save_images())

    def test_crop_is_packed_and_clean(self):
        obj = self.make_file_reference(width=64, height=32)
        bpy.ops.betterref.crop_set_rect(min_x=0.25, min_y=0.25, max_x=0.75, max_y=0.75)
        derived = obj.data
        self.assertTrue(derived.packed_file)
        self.assertFalse(derived.is_dirty)
        self.assertEqual("FILE", derived.source)

    def test_packing_preserves_float_pixels_exactly(self):
        obj = self.make_file_reference(width=64, height=32)
        source_pixels = self.read_pixels(obj.betterref_crop.source_image or obj.data)
        bpy.ops.betterref.crop_set_rect(min_x=0.25, min_y=0.5, max_x=0.75, max_y=1.0)
        np.testing.assert_allclose(
            source_pixels[16:32, 16:48, :], self.read_pixels(obj.data), atol=1e-6
        )

    def test_reset_after_packing_restores_the_source(self):
        obj = self.make_file_reference(width=64, height=32)
        source = obj.data
        bpy.ops.betterref.crop_set_rect(min_x=0.25, min_y=0.25, max_x=0.75, max_y=0.75)
        self.assert_operator_finished(bpy.ops.betterref.crop_reset())
        self.assertEqual(source, obj.data)
        self.assertFalse(self._prompts_to_save_images())

    def test_slider_edits_are_packed_before_the_file_is_written(self):
        # The live path skips packing so dragging stays responsive; the
        # save_pre handler is what guarantees a clean file.
        obj = self.make_file_reference(width=64, height=32)
        bpy.ops.betterref.crop_begin()
        obj.betterref_crop.max_x = 0.5
        self.assertFalse(obj.data.packed_file)
        self.assertTrue(self._prompts_to_save_images())

        bpy.ops.wm.save_as_mainfile(filepath=str(self.workdir / "packed.blend"))
        self.assertTrue(obj.data.packed_file)
        self.assertFalse(self._prompts_to_save_images())

    def test_repeated_crops_do_not_accumulate_image_datablocks(self):
        # Each bake starts from a fresh datablock because a packed one cannot
        # be re-packed; the old one must be removed rather than left behind.
        self.make_file_reference(width=64, height=32)
        for span in (0.9, 0.7, 0.5, 0.3):
            margin = (1.0 - span) * 0.5
            bpy.ops.betterref.crop_set_rect(
                min_x=margin, min_y=margin, max_x=1.0 - margin, max_y=1.0 - margin
            )
        crops = [i for i in bpy.data.images if i.name.startswith("BR_Crop")]
        self.assertEqual(1, len(crops))
        self.assertNotIn(".001", crops[0].name)

    def test_packed_crop_gets_a_tidy_unpack_path(self):
        obj = self.make_file_reference(width=64, height=32)
        bpy.ops.betterref.crop_set_rect(min_x=0.25, min_y=0.25, max_x=0.75, max_y=0.75)
        # The source name already carries an extension; the crop must not end
        # up as "...exr.exr".
        self.assertFalse(obj.data.filepath.lower().endswith(".exr.exr"))
        self.assertTrue(obj.data.filepath.startswith("//"))

    def test_repacking_after_a_second_crop_stays_clean(self):
        obj = self.make_file_reference(width=64, height=32)
        for span in (0.8, 0.6, 0.4):
            margin = (1.0 - span) * 0.5
            bpy.ops.betterref.crop_set_rect(
                min_x=margin, min_y=margin, max_x=1.0 - margin, max_y=1.0 - margin
            )
            self.assertTrue(obj.data.packed_file)
            self.assertFalse(self._prompts_to_save_images())


class RegistrationTests(BlenderIntegrationTest):
    def test_object_property_group_is_attached(self):
        obj = self.make_reference()
        self.assertIsNotNone(getattr(obj, "betterref_crop", None))

    def test_operators_are_registered(self):
        for name in ("crop_begin", "crop_modal", "crop_reset", "crop_set_rect"):
            with self.subTest(operator=name):
                self.assertTrue(hasattr(bpy.ops.betterref, name))

    def test_panel_is_registered(self):
        self.assertTrue(hasattr(bpy.types, "BETTERREF_PT_crop_3dv"))

    def test_tool_is_registered_after_the_builtin_transform_tool(self):
        from bl_ui.space_toolsystem_common import ToolSelectPanelHelper

        from betterref.crop.tool import BETTERREF_TT_crop

        bound = {entry[0] for entry in BETTERREF_TT_crop.bl_keymap}
        self.assertIn("betterref.crop_modal", bound)

        helper = ToolSelectPanelHelper._tool_class_from_space_type("VIEW_3D")
        idnames = [
            item.idname
            for item in ToolSelectPanelHelper._tools_flatten(
                helper._tools.get("OBJECT", [])
            )
            if item is not None
        ]
        self.assertIn("betterref.crop", idnames)
        self.assertEqual(
            idnames.index("builtin.transform") + 1, idnames.index("betterref.crop")
        )
