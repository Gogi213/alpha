"""TK-208: results/run_cells/cells на каноническом хеше; прямой join с plan_cells = выписка (have_hashes)."""
import os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import canon_ids, cellhash_reg, db, master  # noqa: E402


class CanonIdsTest(unittest.TestCase):
    def test_merge_and_idempotent(self):
        cs = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        rc = [{"run_id": "R", "cell_id": "a"}, {"run_id": "R", "cell_id": "b"}]
        rs = [{"run_id": "R", "cell_id": "a", "month": "m"}, {"run_id": "R", "cell_id": "b", "month": "m"}]
        m = {"a": "H", "b": "H", "c": "C"}
        c1, r1, s1 = canon_ids.migrate_rows(cs, rc, rs, m)
        self.assertEqual([(d["id"], d["alias"]) for d in c1], [("H", ["a", "b"]), ("C", ["c"])])
        self.assertEqual((len(r1), len(s1)), (1, 1))
        self.assertEqual(canon_ids.migrate_rows(c1, r1, s1, {}), (c1, r1, s1))

    def test_registry_is_canonical(self):
        """Каждая клетка results — канонический хеш (карта TK-186 тождественна), join с планом = выписка."""
        rows = cellhash_reg.build()
        self.assertTrue(all(r[0] == r[1] for r in rows), [r[0] for r in rows if r[0] != r[1]][:3])
        res = {d["cell_id"] for d in db.jl("results.jsonl")}
        plan = {d["cell_hash"].strip() for d in db.jl("plan_cells.jsonl")}
        have = set(master.have_hashes())
        self.assertEqual(len(plan & res), len(plan & have))

    def test_status_sums_to_all_hypotheses(self):
        """render status: одна строка статуса на гипотезу, сумма = числу гипотез в БД (145; методы В-228 — отдельно)."""
        c = master.connect()
        n = c.execute(f"select count(*) from hypotheses where {master.HYP_ONLY}").fetchone()[0]
        nm = c.execute("select count(*) from hypotheses where json_extract(ext, '$.class') = 'method'").fetchone()[0]
        st = master.hyp_status(c, {})
        c.close()
        self.assertEqual((len(st), n, nm), (145, 145, 4))


if __name__ == "__main__":
    unittest.main()
