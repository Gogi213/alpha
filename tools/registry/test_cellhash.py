import json, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cellhash as ch, cellhash_reg as cr

B1F = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
base = lambda **k: {"form": B1F, "set": cr.B1_SET, "exec": {**cr.STD_EXEC, "busy_skip": "off"}, "sigma": "study/sigma240", **k}


class T(unittest.TestCase):
    def test_two_paths_same_hash(self):
        a = base()
        b = {"entry": ch.parse_entry("ladder3x0..0.0409sw2"), "stop": {"kind": "pct_bps", "v": 200.0}, "take": ch.parse_take("tr1x1"),
             "deadline_s": "14400", "ttl": 1800, "set": {**cr.B1_SET, "min_age_s": "Some(2700)", "post_only": "true", "frontrun_only": "false"},
             "exec": {**cr.STD_EXEC, "place_ns": 4200000.0, "busy_skip": "off"}, "sigma": "/data/x/e-jan/study/sigma240"}
        self.assertEqual(ch.cell_hash(a), ch.cell_hash(b))

    def test_half_default_is_f2(self):
        h = lambda e: ch.normalize(base(form=B1F + "-" + e))["exit"]
        self.assertEqual(h("halfstop"), ["halfstop", "f", 2])
        self.assertNotEqual(ch.cell_hash(base(form=B1F + "-halfstop")), ch.cell_hash(base(form=B1F + "-halfstopf1")))

    def test_different_config_different_hash(self):
        h0 = ch.cell_hash(base())
        for v in (base(form=B1F.replace("pct2", "pct2.5")), base(form=B1F.replace("14400", "21600")), base(form=B1F + "-pyre5u1"),
                  base(exec={**cr.STD_EXEC, "busy_skip": "on"}), base(sigma="study/sigma120"), base(logic="bounce-v2"),
                  base(variant={"family": "g83", "axes": {"q": "k50"}}), base(set={**cr.B1_SET, "min_age_s": 3600})):
            self.assertNotEqual(h0, ch.cell_hash(v))

    def test_provenance_excluded(self):
        self.assertEqual(ch.cell_hash(base()), ch.cell_hash({**base(), "group": "x", "source": "y", "set_name": "z"}))

    def test_registry_map_stable_and_b1_collapse(self):
        rows = cr.build()
        self.assertEqual(len(rows), 442)
        b1 = {r[1] for r in rows if r[4] in ("p12-r1/B1", "p12-ext/" + B1F) or (r[4] == "r3c/e106-pop" and r[3] == B1F)}
        self.assertEqual(len(b1), 1)

    def test_plan(self):
        import tempfile
        rows = cr.build()
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write(json.dumps(base()) + "\n" + json.dumps(base()) + "\n" + json.dumps(base(form=B1F.replace("pct2", "pct2.25"))) + "\n")
        done, todo = cr.cmd_plan(f.name, rows)
        os.unlink(f.name)
        self.assertEqual((len(done), len(todo)), (1, 1))


if __name__ == "__main__":
    unittest.main()
