import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import vt_towns

class TownCentroids(unittest.TestCase):
    def setUp(self):
        self.c = vt_towns.load_town_centroids()

    def test_known_towns(self):
        for town, lat, lng in [("Burlington", 44.4874, -73.2312),
                               ("Montpelier", 44.2659, -72.5717),
                               ("Hinesburg", 44.3244, -73.0902)]:
            self.assertAlmostEqual(self.c[town][0], lat, places=3)
            self.assertAlmostEqual(self.c[town][1], lng, places=3)

    def test_aliases_resolve(self):
        self.assertEqual(self.c["St. Albans"], self.c["Saint Albans City"])

    def test_all_towns_inside_vermont(self):
        self.assertGreaterEqual(len(self.c), 256)
        for lat, lng in self.c.values():
            self.assertTrue(42.7 < lat < 45.1 and -73.5 < lng < -71.4)

    def test_county_map_still_works(self):
        self.assertEqual(vt_towns.load_town_county_map()["Burlington"], "Chittenden")
