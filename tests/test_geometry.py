"""Pure-math tests for the crop geometry, independent of any scene state."""

from __future__ import annotations

from mathutils import Matrix
import unittest

from betterref.crop import utils


class ImageExtentsTests(unittest.TestCase):
    def test_landscape_long_axis_is_display_size(self):
        extent_x, extent_y = utils.image_extents(64, 32, 4.0)
        self.assertAlmostEqual(4.0, extent_x)
        self.assertAlmostEqual(2.0, extent_y)

    def test_portrait_long_axis_is_display_size(self):
        extent_x, extent_y = utils.image_extents(32, 64, 4.0)
        self.assertAlmostEqual(2.0, extent_x)
        self.assertAlmostEqual(4.0, extent_y)

    def test_square(self):
        self.assertEqual((3.0, 3.0), utils.image_extents(16, 16, 3.0))

    def test_degenerate_size_does_not_divide_by_zero(self):
        self.assertEqual((2.0, 2.0), utils.image_extents(0, 0, 2.0))


class QuadCornersTests(unittest.TestCase):
    def test_default_offset_centers_the_quad(self):
        x0, y0, x1, y1 = utils.quad_corners((64, 32), 4.0, (-0.5, -0.5))
        self.assertAlmostEqual(-2.0, x0)
        self.assertAlmostEqual(2.0, x1)
        self.assertAlmostEqual(-1.0, y0)
        self.assertAlmostEqual(1.0, y1)

    def test_zero_offset_puts_origin_at_the_corner(self):
        x0, y0, x1, y1 = utils.quad_corners((64, 32), 4.0, (0.0, 0.0))
        self.assertAlmostEqual(0.0, x0)
        self.assertAlmostEqual(0.0, y0)
        self.assertAlmostEqual(4.0, x1)
        self.assertAlmostEqual(2.0, y1)


class PixelBoundsTests(unittest.TestCase):
    def test_full_rect_covers_the_image(self):
        self.assertEqual((0, 0, 64, 32), utils.pixel_bounds(64, 32, (0.0, 0.0, 1.0, 1.0)))

    def test_rect_snaps_to_whole_pixels(self):
        self.assertEqual(
            (16, 8, 48, 24), utils.pixel_bounds(64, 32, (0.25, 0.25, 0.75, 0.75))
        )

    def test_out_of_range_rect_is_clamped(self):
        self.assertEqual(
            (0, 0, 64, 32), utils.pixel_bounds(64, 32, (-1.0, -1.0, 2.0, 2.0))
        )

    def test_inverted_rect_is_normalized(self):
        self.assertEqual(
            (16, 8, 48, 24), utils.pixel_bounds(64, 32, (0.75, 0.75, 0.25, 0.25))
        )

    def test_collapsed_rect_keeps_at_least_one_pixel(self):
        x0, y0, x1, y1 = utils.pixel_bounds(64, 32, (0.5, 0.5, 0.5, 0.5))
        self.assertGreaterEqual(x1 - x0, 1)
        self.assertGreaterEqual(y1 - y0, 1)


class CropTransformTests(unittest.TestCase):
    """The kept region must not move when the crop is applied."""

    def _anchor(self, size, display_size, offset, u, v):
        x0, y0, x1, y1 = utils.quad_corners(size, display_size, offset)
        return (x0 + u * (x1 - x0), y0 + v * (y1 - y0))

    def assert_content_pinned(self, size, display_size, offset, rect):
        # The transform is only exact for a rect that lands on pixel edges, which
        # is what apply_crop always feeds it. Snap here the same way it does.
        bounds = utils.pixel_bounds(size[0], size[1], rect)
        min_x, min_y, max_x, max_y = utils.exact_rect(size, bounds)
        new_size = (bounds[2] - bounds[0], bounds[3] - bounds[1])
        new_display_size, new_offset = utils.crop_transform(
            size, display_size, offset, (min_x, min_y, max_x, max_y)
        )
        # Sample the rect's own corners plus its center.
        for u, v in ((0.0, 0.0), (1.0, 1.0), (0.5, 0.5), (0.25, 0.75)):
            source_u = min_x + u * (max_x - min_x)
            source_v = min_y + v * (max_y - min_y)
            before = self._anchor(size, display_size, offset, source_u, source_v)
            after = self._anchor(new_size, new_display_size, new_offset, u, v)
            self.assertAlmostEqual(before[0], after[0], places=5)
            self.assertAlmostEqual(before[1], after[1], places=5)

    def test_full_rect_is_a_no_op(self):
        display_size, offset = utils.crop_transform(
            (64, 32), 4.0, (-0.5, -0.5), (0.0, 0.0, 1.0, 1.0)
        )
        self.assertAlmostEqual(4.0, display_size)
        self.assertAlmostEqual(-0.5, offset[0])
        self.assertAlmostEqual(-0.5, offset[1])

    def test_centered_crop_pins_content(self):
        self.assert_content_pinned((64, 32), 4.0, (-0.5, -0.5), (0.25, 0.25, 0.75, 0.75))

    def test_corner_crop_pins_content(self):
        self.assert_content_pinned((64, 32), 4.0, (-0.5, -0.5), (0.0, 0.5, 0.5, 1.0))

    def test_portrait_crop_pins_content(self):
        self.assert_content_pinned((32, 128), 6.0, (-0.5, -0.5), (0.1, 0.2, 0.6, 0.9))

    def test_nonstandard_offset_pins_content(self):
        self.assert_content_pinned((80, 40), 3.0, (0.0, -1.0), (0.3, 0.1, 0.9, 0.7))

    def test_crop_to_square_region_of_landscape(self):
        # A crop whose aspect flips the long axis must still land correctly.
        self.assert_content_pinned((128, 32), 8.0, (-0.5, -0.5), (0.4, 0.0, 0.5, 1.0))


class CageMatrixTests(unittest.TestCase):
    def test_rect_survives_a_matrix_round_trip(self):
        extents = (4.0, 2.0)
        for rect in (
            (0.0, 0.0, 1.0, 1.0),
            (0.25, 0.25, 0.75, 0.75),
            (0.0, 0.5, 0.5, 1.0),
            (0.1, 0.2, 0.6, 0.9),
        ):
            with self.subTest(rect=rect):
                matrix = utils.rect_to_offset_matrix(rect, extents)
                result = utils.offset_matrix_to_rect(matrix, extents)
                for expected, actual in zip(rect, result):
                    self.assertAlmostEqual(expected, actual, places=6)

    def test_identity_matrix_is_the_full_image(self):
        rect = utils.offset_matrix_to_rect(Matrix.Identity(4), (4.0, 2.0))
        for expected, actual in zip((0.0, 0.0, 1.0, 1.0), rect):
            self.assertAlmostEqual(expected, actual, places=6)

    def test_out_of_bounds_matrix_is_clamped_into_the_image(self):
        matrix = utils.rect_to_offset_matrix((-0.5, -0.5, 1.5, 1.5), (4.0, 2.0))
        min_x, min_y, max_x, max_y = utils.offset_matrix_to_rect(matrix, (4.0, 2.0))
        self.assertGreaterEqual(min_x, 0.0)
        self.assertGreaterEqual(min_y, 0.0)
        self.assertLessEqual(max_x, 1.0)
        self.assertLessEqual(max_y, 1.0)


class HandleTests(unittest.TestCase):
    def test_handles_sit_on_the_rect(self):
        handles = utils.handle_uvs((0.2, 0.4, 0.8, 1.0))
        self.assertEqual((0.2, 0.4), handles["BL"])
        self.assertEqual((0.8, 1.0), handles["TR"])
        self.assertAlmostEqual(0.5, handles["B"][0])
        self.assertAlmostEqual(0.4, handles["B"][1])
        self.assertAlmostEqual(0.8, handles["R"][0])
        self.assertAlmostEqual(0.7, handles["R"][1])

    def test_every_named_handle_is_produced(self):
        self.assertEqual(set(utils.HANDLES), set(utils.handle_uvs((0.0, 0.0, 1.0, 1.0))))


class MoveRectTests(unittest.TestCase):
    def test_move_preserves_size(self):
        min_x, min_y, max_x, max_y = utils.move_rect((0.2, 0.2, 0.6, 0.5), 0.1, -0.1)
        self.assertAlmostEqual(0.4, max_x - min_x)
        self.assertAlmostEqual(0.3, max_y - min_y)
        self.assertAlmostEqual(0.3, min_x)
        self.assertAlmostEqual(0.1, min_y)

    def test_move_stops_at_the_image_edge_without_shrinking(self):
        min_x, min_y, max_x, max_y = utils.move_rect((0.6, 0.6, 1.0, 1.0), 0.5, 0.5)
        self.assertAlmostEqual(0.4, max_x - min_x)
        self.assertAlmostEqual(1.0, max_x)
        self.assertAlmostEqual(1.0, max_y)

    def test_move_stops_at_the_origin_without_shrinking(self):
        min_x, min_y, max_x, max_y = utils.move_rect((0.0, 0.0, 0.4, 0.4), -0.5, -0.5)
        self.assertAlmostEqual(0.0, min_x)
        self.assertAlmostEqual(0.0, min_y)
        self.assertAlmostEqual(0.4, max_x)


class ResizeRectTests(unittest.TestCase):
    BASE = (0.25, 0.25, 0.75, 0.75)

    def test_each_corner_moves_only_its_own_edges(self):
        cases = {
            "BL": (0.35, 0.35, 0.75, 0.75),
            "BR": (0.25, 0.35, 0.85, 0.75),
            "TR": (0.25, 0.25, 0.85, 0.85),
            "TL": (0.35, 0.25, 0.75, 0.85),
        }
        for handle, expected in cases.items():
            with self.subTest(handle=handle):
                result = utils.resize_rect(self.BASE, handle, 0.1, 0.1)
                for want, got in zip(expected, result):
                    self.assertAlmostEqual(want, got, places=6)

    def test_edge_handles_move_one_axis_only(self):
        left = utils.resize_rect(self.BASE, "L", 0.1, 0.1)
        self.assertAlmostEqual(0.35, left[0])
        self.assertAlmostEqual(0.25, left[1])
        self.assertAlmostEqual(0.75, left[3])

        top = utils.resize_rect(self.BASE, "T", 0.1, 0.1)
        self.assertAlmostEqual(0.25, top[0])
        self.assertAlmostEqual(0.75, top[2])
        self.assertAlmostEqual(0.85, top[3])

    def test_resize_is_clamped_to_the_image(self):
        result = utils.resize_rect(self.BASE, "TR", 5.0, 5.0)
        self.assertAlmostEqual(1.0, result[2])
        self.assertAlmostEqual(1.0, result[3])

    def test_dragging_an_edge_past_its_opposite_does_not_invert(self):
        result = utils.resize_rect(self.BASE, "L", 5.0, 0.0)
        self.assertLess(result[0], result[2])
        result = utils.resize_rect(self.BASE, "T", 0.0, -5.0)
        self.assertLess(result[1], result[3])


class ApplyAspectTests(unittest.TestCase):
    EXTENTS = (4.0, 2.0)  # a 2:1 image

    def _world_aspect(self, rect):
        min_x, min_y, max_x, max_y = rect
        return ((max_x - min_x) * self.EXTENTS[0]) / (
            (max_y - min_y) * self.EXTENTS[1]
        )

    def test_square_lock_gives_a_square_in_world_units(self):
        rect = utils.apply_aspect((0.1, 0.25, 0.9, 0.75), "TR", 1.0, self.EXTENTS)
        self.assertAlmostEqual(1.0, self._world_aspect(rect), places=5)

    def test_wide_lock_is_honoured(self):
        rect = utils.apply_aspect((0.2, 0.2, 0.8, 0.8), "TR", 16 / 9, self.EXTENTS)
        self.assertAlmostEqual(16 / 9, self._world_aspect(rect), places=5)

    def test_corner_drag_anchors_the_opposite_corner(self):
        rect = utils.apply_aspect((0.2, 0.3, 0.9, 0.8), "TR", 1.0, self.EXTENTS)
        # Dragging the top-right must leave the bottom-left where it was.
        self.assertAlmostEqual(0.2, rect[0], places=6)
        self.assertAlmostEqual(0.3, rect[1], places=6)

    def test_opposite_corner_drag_anchors_the_other_side(self):
        rect = utils.apply_aspect((0.2, 0.3, 0.9, 0.8), "BL", 1.0, self.EXTENTS)
        self.assertAlmostEqual(0.9, rect[2], places=6)
        self.assertAlmostEqual(0.8, rect[3], places=6)

    def test_edge_drag_grows_the_other_axis_about_its_centre(self):
        rect = utils.apply_aspect((0.2, 0.3, 0.8, 0.7), "R", 1.0, self.EXTENTS)
        self.assertAlmostEqual(0.5, (rect[1] + rect[3]) * 0.5, places=6)
        self.assertAlmostEqual(1.0, self._world_aspect(rect), places=5)

    def test_invalid_aspect_is_ignored(self):
        base = (0.2, 0.3, 0.8, 0.7)
        self.assertEqual(base, utils.apply_aspect(base, "TR", 0.0, self.EXTENTS))
        self.assertEqual(base, utils.apply_aspect(base, "TR", 1.0, (0.0, 0.0)))

    def test_result_stays_inside_the_image(self):
        rect = utils.apply_aspect((0.0, 0.0, 1.0, 1.0), "TR", 4.0, self.EXTENTS)
        self.assertGreaterEqual(rect[0], 0.0)
        self.assertGreaterEqual(rect[1], 0.0)
        self.assertLessEqual(rect[2], 1.0)
        self.assertLessEqual(rect[3], 1.0)

    def test_ratio_is_kept_even_when_the_rect_must_shrink_to_fit(self):
        # A locked square that cannot fit at the requested size shrinks on both
        # axes rather than clamping one and silently losing the ratio.
        for handle in utils.HANDLES:
            with self.subTest(handle=handle):
                rect = utils.apply_aspect(
                    (0.0, 0.0, 1.0, 1.0), handle, 1.0, self.EXTENTS
                )
                self.assertAlmostEqual(1.0, self._world_aspect(rect), places=5)
                self.assertGreaterEqual(rect[0], -1e-9)
                self.assertGreaterEqual(rect[1], -1e-9)
                self.assertLessEqual(rect[2], 1.0 + 1e-9)
                self.assertLessEqual(rect[3], 1.0 + 1e-9)


class ClampRectTests(unittest.TestCase):
    def test_inverted_input_is_sorted(self):
        min_x, min_y, max_x, max_y = utils.clamp_rect((0.8, 0.9, 0.2, 0.1))
        self.assertLess(min_x, max_x)
        self.assertLess(min_y, max_y)

    def test_never_collapses(self):
        min_x, min_y, max_x, max_y = utils.clamp_rect((0.5, 0.5, 0.5, 0.5))
        self.assertGreater(max_x, min_x)
        self.assertGreater(max_y, min_y)
