import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mechanics as mech


class T(unittest.TestCase):
    def test_bridge_samples_equal_rust(self):
        """Имена по шаблону и доменам (Python) = имена, которые сгенерировал и проверил Rust (поле samples снимка)."""
        n = 0
        for r in mech.ROWS:
            self.assertEqual(mech.sample_names(r), r["samples"], r["name_template"])
            n += len(r["samples"])
        self.assertGreater(n, 300)

    def test_build_picks_one_row(self):
        self.assertEqual(mech.build("NoStop", x8=8, x2=2), ("nostop2", None))
        self.assertEqual(mech.build("NoStop", x8=12, x2=3), ("nostopx12", None))
        self.assertEqual(mech.build("PyrReinstall", n=3, u12=4, u3=1), ("pyre3u1", None))
        self.assertEqual(mech.build("PyrReinstall", n=3, u12=5, u3=None), ("pyre3w5", None))
        name, why = mech.build("PyrFresh", parts=13)
        self.assertIsNone(name)
        self.assertIn("parts", why)

    def test_r2_prefixes(self):
        self.assertTrue(mech.is_r2_exit("tslx8t4"))
        self.assertTrue(mech.is_r2_exit("wall2m2"))
        self.assertFalse(mech.is_r2_exit("gone50be"))
        self.assertFalse(mech.is_r2_exit("weat30s60m10"))


if __name__ == "__main__":
    unittest.main()
