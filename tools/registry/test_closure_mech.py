import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(__file__))
import closure_mech
from closures import g124_profit_throttle as g124

D = 86_400_000


class T(unittest.TestCase):
    def test_scale1_is_identity(self):  # «выкл = прежний»: scale 1 не меняет сделок (вне домена, прямой вызов)
        c = [(i * D // 3, 1.0 if i % 2 else -0.5) for i in range(30)]
        self.assertEqual(g124.run(c, 90, 3, 1.0), c)

    def test_throttle_after_big_day(self):
        c = [(0, 1.0), (D, 0.5), (2 * D, 10.0), (3 * D, 5.0), (4 * D, 5.0)]
        out = g124.run(c, 90, 1, 0.0)
        self.assertEqual([u for _, u in out], [1.0, 0.5, 10.0, 0.0, 5.0])

    def test_domain_refusal(self):
        with self.assertRaises(SystemExit):
            list(closure_mech.grid_points(g124.PARAMS, "q=91"))
        self.assertEqual(len(list(closure_mech.grid_points(g124.PARAMS))), 4 * 5 * 3)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            p, o = os.path.join(d, "c.json"), os.path.join(d, "o.csv")
            json.dump({"v": {"2026-01": {"0": [[0, 1.0], [D, 2.0]]}}}, open(p, "w"))
            closure_mech.main(["--closes", p, "--mechanic", "g124_profit_throttle", "--grid", "q=90;D=1;scale=0.0", "--out", o])
            self.assertEqual(len(open(o).read().splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
