"""Cross-language fixtures and validation for displayed surface identity."""

import unittest

from city_surface_identity import displayed_surface_sha256


ROADBED = [
    {
        "outline": [[0, 0], [12.5, 0], [12.5, 4], [0, 4]],
        "holes": [[[2, 1], [3, 1], [3, 2], [2, 2]]],
    },
    {"outline": [[-1, -2], [0, -2], [0, -1]], "holes": []},
]
WALKBED = [{"outline": [[0, 5], [12.5, 5], [12.5, 8], [0, 8]], "holes": []}]
FIXED_SHA256 = "a404db26fd7476aabca0da048ffb36bc0d97c11675f49024b890d88ec4937f91"


class DisplaySurfaceIdentityTests(unittest.TestCase):
    def test_matches_fixed_python_typescript_binary_fixture(self) -> None:
        self.assertEqual(displayed_surface_sha256(ROADBED, WALKBED), FIXED_SHA256)

    def test_preserves_order_and_detects_point_and_hole_changes(self) -> None:
        baseline = displayed_surface_sha256(ROADBED, WALKBED)
        reordered = [dict(ROADBED[0], outline=list(reversed(ROADBED[0]["outline"]))), ROADBED[1]]
        moved = [dict(ROADBED[0], outline=[[0, 0], [12.5001, 0], [12.5, 4], [0, 4]]), ROADBED[1]]
        changed_hole = [dict(ROADBED[0], holes=[[[2, 1], [3.01, 1], [3, 2], [2, 2]]]), ROADBED[1]]
        self.assertNotEqual(displayed_surface_sha256(reordered, WALKBED), baseline)
        self.assertNotEqual(displayed_surface_sha256(moved, WALKBED), baseline)
        self.assertNotEqual(displayed_surface_sha256(changed_hole, WALKBED), baseline)

    def test_negative_zero_is_canonical_and_bad_geometry_is_rejected(self) -> None:
        negative_zero = [dict(ROADBED[0], outline=[[-0.0, -0.0], *ROADBED[0]["outline"][1:]]), ROADBED[1]]
        self.assertEqual(displayed_surface_sha256(negative_zero, WALKBED), displayed_surface_sha256(ROADBED, WALKBED))
        with self.assertRaisesRegex(ValueError, "at least three points"):
            displayed_surface_sha256([{"outline": [[0, 0], [1, 0]], "holes": []}], [])
        with self.assertRaisesRegex(TypeError, "finite numbers"):
            displayed_surface_sha256([{"outline": [[0, 0], [1, 0], [0, float("nan")]], "holes": []}], [])


if __name__ == "__main__":
    unittest.main()
