"""Regression tests for `app.services.astronomy_service`.

The deterministic helpers and fallback path run offline. The Skyfield-
based golden test is gated on the `RUN_SKYFIELD_TESTS` environment
variable because it downloads ~17MB of JPL DE421 data on first call.

Run all offline tests:
    python -m unittest discover -s tests

Include the golden test (requires network on first run):
    RUN_SKYFIELD_TESTS=1 python -m unittest discover -s tests
"""

from __future__ import annotations

import os
import unittest

from app.services import astronomy_service as astro


_PHASE_LABELS = {
    "New Moon",
    "Waxing Crescent",
    "First Quarter",
    "Waxing Gibbous",
    "Full Moon",
    "Waning Gibbous",
    "Last Quarter",
    "Waning Crescent",
}


class ContractTests(unittest.TestCase):
    """Lock the public response shape so consumers never break silently."""

    def test_fallback_satisfies_contract(self) -> None:
        payload = astro._fallback_astronomy(43.45, -80.49, "2026-08-15", "01:00")
        self.assertTrue(astro.validate_response(payload))
        self.assertIsInstance(payload["planets"], list)
        self.assertIn(payload["moon_phase"], _PHASE_LABELS)
        self.assertIn(
            payload["darkness_level"],
            {
                "Astronomical Night",
                "Nautical Twilight",
                "Civil Twilight",
                "Dusk/Dawn",
                "Daylight",
            },
        )
        self.assertIn("stars", payload)
        self.assertIn("constellations", payload)
        self.assertEqual(payload["stars"], [])
        self.assertEqual(payload["constellations"], [])

    def test_validate_response_rejects_missing_keys(self) -> None:
        self.assertFalse(astro.validate_response({"moon_phase": "Full Moon"}))


class HelperTests(unittest.TestCase):
    def test_phase_name_boundaries(self) -> None:
        self.assertEqual(astro._phase_name(0.0), "New Moon")
        self.assertEqual(astro._phase_name(90.0), "First Quarter")
        self.assertEqual(astro._phase_name(180.0), "Full Moon")
        self.assertEqual(astro._phase_name(270.0), "Last Quarter")
        self.assertEqual(astro._phase_name(360.0), "New Moon")

    def test_angular_separation_orthogonal(self) -> None:
        self.assertAlmostEqual(
            astro._angular_separation_deg(0.0, 0.0, 0.0, 90.0), 90.0, places=5
        )
        self.assertAlmostEqual(
            astro._angular_separation_deg(45.0, 0.0, 45.0, 180.0), 90.0, places=5
        )

    def test_moon_proximity_factor_edges(self) -> None:
        self.assertEqual(astro._moon_proximity_factor(None), 1.0)
        self.assertEqual(astro._moon_proximity_factor(15.0), 1.0)
        self.assertEqual(astro._moon_proximity_factor(120.0), 0.0)
        midpoint = (
            astro.MOON_PROXIMITY_FULL_PENALTY_DEG
            + astro.MOON_PROXIMITY_NO_PENALTY_DEG
        ) / 2
        self.assertAlmostEqual(astro._moon_proximity_factor(midpoint), 0.5, places=5)


class MilkyWayStatusTests(unittest.TestCase):
    def test_daylight_is_not_visible(self) -> None:
        visible, label = astro._milky_way_status(
            sun_altitude=10.0,
            moon_altitude=0.0,
            moon_illumination=0.0,
            core_altitude=30.0,
        )
        self.assertFalse(visible)
        self.assertEqual(label, "Not visible")

    def test_core_below_horizon(self) -> None:
        visible, label = astro._milky_way_status(
            sun_altitude=-30.0,
            moon_altitude=-10.0,
            moon_illumination=0.0,
            core_altitude=-15.0,
        )
        self.assertFalse(visible)
        self.assertEqual(label, "Core below horizon")

    def test_dark_sky_high_core_is_excellent(self) -> None:
        visible, label = astro._milky_way_status(
            sun_altitude=-30.0,
            moon_altitude=-20.0,
            moon_illumination=0.0,
            core_altitude=40.0,
        )
        self.assertTrue(visible)
        self.assertEqual(label, "Excellent")

    def test_full_moon_close_to_core_degrades_quality(self) -> None:
        _, label_close = astro._milky_way_status(
            sun_altitude=-30.0,
            moon_altitude=30.0,
            moon_illumination=95.0,
            core_altitude=25.0,
            moon_core_separation_deg=15.0,
        )
        _, label_far = astro._milky_way_status(
            sun_altitude=-30.0,
            moon_altitude=30.0,
            moon_illumination=95.0,
            core_altitude=25.0,
            moon_core_separation_deg=120.0,
        )
        ranking = ["Poor", "Average", "Good", "Excellent"]
        self.assertGreater(ranking.index(label_far), ranking.index(label_close))


class PlanetKeysTests(unittest.TestCase):
    def test_all_seven_classical_planets_listed(self) -> None:
        self.assertEqual(
            set(astro._PLANET_KEYS),
            {"Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune"},
        )

    def test_each_planet_has_at_least_one_candidate(self) -> None:
        for name, candidates in astro._PLANET_KEYS.items():
            self.assertTrue(candidates, f"{name} has no candidate keys")


class CatalogTests(unittest.TestCase):
    def test_bright_stars_are_unique_and_sane(self) -> None:
        names = [name for name, *_ in astro._BRIGHT_STARS]
        self.assertEqual(len(names), len(set(names)), "duplicate star names")
        for name, ra, dec, mag in astro._BRIGHT_STARS:
            self.assertGreaterEqual(ra, 0.0)
            self.assertLess(ra, 24.0)
            self.assertGreaterEqual(dec, -90.0)
            self.assertLessEqual(dec, 90.0)
            self.assertLess(mag, 4.0, f"{name} too dim for bright catalog")

    def test_constellation_anchors_exist_in_star_catalog(self) -> None:
        star_names = {name for name, *_ in astro._BRIGHT_STARS}
        for const_name, anchor, ra, dec in astro._CONSTELLATIONS:
            if anchor is None:
                self.assertIsNotNone(ra, f"{const_name} missing both anchor and centroid")
                self.assertIsNotNone(dec, f"{const_name} missing both anchor and centroid")
            else:
                self.assertIn(
                    anchor,
                    star_names,
                    f"{const_name} anchor {anchor} not in bright-star catalog",
                )


@unittest.skipUnless(
    os.environ.get("RUN_SKYFIELD_TESTS"),
    "Set RUN_SKYFIELD_TESTS=1 to enable Skyfield/JPL golden tests "
    "(downloads de421.bsp on first run).",
)
class SkyfieldGoldenTests(unittest.TestCase):
    """Real ephemeris call against a known dark-sky location."""

    LATITUDE = 43.45
    LONGITUDE = -80.49
    DATE = "2026-08-15"
    TIME = "01:00"

    def test_known_summer_midnight(self) -> None:
        result = astro.get_astronomy_data(
            self.LATITUDE, self.LONGITUDE, self.DATE, self.TIME
        )
        self.assertTrue(astro.validate_response(result))

        self.assertLess(
            result["sun_altitude"],
            astro.SUN_ALT_NAUTICAL_DEG,
            "Sun should be well below horizon at 01:00 local in mid-August",
        )
        self.assertEqual(
            result["darkness_level"],
            "Astronomical Night",
            "Mid-August 1am at 43N should be true astronomical night",
        )

        planets = result["planets"]
        self.assertGreaterEqual(
            len(planets), 5, "Expected ephemeris to resolve at least 5 planets"
        )
        for planet in planets:
            self.assertIn("altitude", planet)
            self.assertIn("azimuth", planet)
            self.assertIn("visible", planet)

        self.assertIn("moon_core_separation", result)
        self.assertGreaterEqual(result["moon_core_separation"], 0.0)
        self.assertLessEqual(result["moon_core_separation"], 180.0)

        stars = result["stars"]
        self.assertEqual(
            len(stars),
            len(astro._BRIGHT_STARS),
            "Every catalogued bright star should be evaluated",
        )
        for star in stars:
            self.assertIn("name", star)
            self.assertIn("magnitude", star)
            self.assertGreaterEqual(star["altitude"], -90.0)
            self.assertLessEqual(star["altitude"], 90.0)
            self.assertGreaterEqual(star["azimuth"], 0.0)
            self.assertLess(star["azimuth"], 360.0)
            self.assertEqual(star["visible"], star["altitude"] > 0)

        constellations = result["constellations"]
        self.assertEqual(
            len(constellations),
            len(astro._CONSTELLATIONS),
            "Every catalogued constellation should be evaluated",
        )
        names = {c["name"] for c in constellations}
        self.assertIn("Sagittarius", names)
        self.assertIn("Cygnus", names)

    def test_dynamic_across_time_and_observer(self) -> None:
        """Stars and constellations must change with time and location."""
        a = astro.get_astronomy_data(self.LATITUDE, self.LONGITUDE, self.DATE, "01:00")
        b = astro.get_astronomy_data(self.LATITUDE, self.LONGITUDE, self.DATE, "05:00")
        c = astro.get_astronomy_data(-33.86, 151.21, self.DATE, "01:00")  # Sydney

        def _by_name(items):
            return {item["name"]: item for item in items}

        a_stars, b_stars, c_stars = (
            _by_name(x["stars"]) for x in (a, b, c)
        )

        # A bright star like Vega must appear in all three runs and have
        # different alt/az for different time and different observer.
        self.assertIn("Vega", a_stars)
        self.assertIn("Vega", b_stars)
        self.assertIn("Vega", c_stars)
        self.assertNotEqual(
            (a_stars["Vega"]["altitude"], a_stars["Vega"]["azimuth"]),
            (b_stars["Vega"]["altitude"], b_stars["Vega"]["azimuth"]),
            "Stars should move across the sky with time",
        )
        self.assertNotEqual(
            (a_stars["Vega"]["altitude"], a_stars["Vega"]["azimuth"]),
            (c_stars["Vega"]["altitude"], c_stars["Vega"]["azimuth"]),
            "Stars should be observer-dependent",
        )

        # Constellation anchors must agree with the bright-star alt/az
        # whenever the anchor exists in the bright-star catalog.
        a_const = _by_name(a["constellations"])
        cygnus = a_const["Cygnus"]
        deneb = a_stars["Deneb"]
        self.assertEqual(cygnus["altitude"], deneb["altitude"])
        self.assertEqual(cygnus["azimuth"], deneb["azimuth"])


if __name__ == "__main__":
    unittest.main()
