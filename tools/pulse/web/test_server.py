"""Тесты табло-сервера (команды, ключи, приём сводок): python -m unittest tools/pulse/web/test_server.py"""
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


class TeamsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / "token").write_text("x" * 32)
        os.environ["BOARD_DATA"] = str(d / "board")
        os.environ["BOARD_TOKEN_FILE"] = str(d / "token")
        (d / "board").mkdir()
        import server
        self.s = importlib.reload(server)

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_status_yet(self):
        doc, err = self.s.load_status()
        self.assertIsNone(doc)
        self.assertTrue(err)

    def test_alpha_stays_on_top_level(self):
        (self.s.DATA / "status.json").write_text(json.dumps({"view2": {"a": 1}, "built_at": "t"}))
        doc, _ = self.s.load_status()
        self.assertEqual(doc["view2"], {"a": 1})
        self.assertEqual([t["id"] for t in doc["teams"]], ["alpha"])

    def test_new_team_key_ingest_and_isolation(self):
        (self.s.DATA / "status.json").write_text(json.dumps({"view2": {"a": 1}}))
        t = self.s.new_team("  Инвойс ")
        self.assertEqual(t["name"], "Инвойс")
        self.assertNotIn(t["key"], self.s.TEAMS_FILE.read_text(encoding="utf-8"))  # ключ не хранится, только sha256
        self.assertEqual(self.s.team_by_key(t["key"])["id"], t["id"])
        self.assertIsNone(self.s.team_by_key("rpv_wrong"))
        self.assertTrue(self.s.ingest(t["id"], b'{"view2": {"b": 2}, "built_at": "x"}'))
        self.assertFalse(self.s.ingest(t["id"], b'{"no": 1}'))
        self.assertFalse(self.s.ingest(t["id"], b"not json"))
        doc, _ = self.s.load_status()
        by = {x["id"]: x for x in doc["teams"]}
        self.assertEqual(by[t["id"]]["view2"], {"b": 2})
        self.assertEqual(by["alpha"]["view2"], {"a": 1})
        self.assertEqual(doc["view2"], {"a": 1})

    def test_delete_revokes_key_and_drops_summary(self):
        (self.s.DATA / "status.json").write_text(json.dumps({"view2": {"a": 1}}))
        t, u = self.s.new_team("A"), self.s.new_team("B")
        self.assertTrue(self.s.ingest(t["id"], b'{"view2": {"b": 2}}'))
        self.assertTrue(self.s.delete_team(t["id"]))
        self.assertIsNone(self.s.team_by_key(t["key"]))
        self.assertFalse((self.s.TEAMS_DIR / (t["id"] + ".json")).exists())
        self.assertEqual(self.s.team_by_key(u["key"])["id"], u["id"])
        self.assertFalse(self.s.delete_team(t["id"]))
        self.assertFalse(self.s.delete_team("alpha"))
        doc, _ = self.s.load_status()
        self.assertEqual([x["id"] for x in doc["teams"]], ["alpha", u["id"]])

    def test_team_limit(self):
        for _ in range(self.s.MAX_TEAMS):
            self.assertIsNotNone(self.s.new_team("x"))
        self.assertIsNone(self.s.new_team("y"))


if __name__ == "__main__":
    unittest.main()
