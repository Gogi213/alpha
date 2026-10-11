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

    def test_registry_map_self_check_and_distinct(self):
        rows = cr.build()
        self.assertEqual(len(rows), 473)  # 442 + 31 клетка R2-B (load_r2b.py, 12.10)
        self.assertEqual(cr.check(rows), [])  # равный хеш => равные trades/sum_net по общим месяцам results
        h = {r[4]: r[1] for r in rows}
        self.assertNotEqual(h["p05-b/p05-f25-u25k"], h["p05-c/p05-f25-a120-u25k"])  # возраст p05-b 2700, не 7200
        self.assertNotEqual(h["r3c/e106-pop"], h["p12-r1/B1"])  # e106-pop = B1 + фильтр r1_born_shift
        self.assertNotEqual(h["p12-r1/B1"], h["p12-ext/" + B1F])  # разные партии (авг 398 vs 402 сделок)

    def test_each_exec_flag_changes_hash(self):
        h0 = ch.cell_hash(base())
        for k, v in (("p95_place_ns", 1), ("p95_cancel_ns", 1), ("p95_taker_ns", 1), ("h3_usd", 5000), ("approach_bps", 30),
                     ("touches_from", "study/touches"), ("verdict_csv", "x/verdict.csv"), ("exit_group", "off")):
            self.assertNotEqual(h0, ch.cell_hash(base(exec={**cr.STD_EXEC, k: v})), k)
        self.assertEqual(h0, ch.cell_hash(base(exec={**cr.STD_EXEC, "verdict_csv": "/data/tk044/final3/verdict.csv"})))  # путь машины не входит

    def test_p12_r1_b1_has_no_batch(self):
        rows = cr.build()
        b1 = next(r for r in rows if r[4] == "p12-r1/B1")
        self.assertIsNone(json.loads(b1[6])["batch"])
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write(b1[6] + "\n")
        done, todo = cr.cmd_plan(f.name, rows)
        os.unlink(f.name)
        self.assertEqual((len(done), len(todo)), (1, 0))  # «уже в Летописи»

    def test_plan(self):
        import tempfile
        rows = cr.build()
        known = json.loads(rows[0][6])
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write("\n".join(json.dumps(x) for x in (known, known, {**known, "deadline_s": 99999})) + "\n")
        done, todo = cr.cmd_plan(f.name, rows)
        os.unlink(f.name)
        self.assertEqual((len(done), len(todo)), (1, 1))


if __name__ == "__main__":
    unittest.main()
