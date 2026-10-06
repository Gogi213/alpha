import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import supervise as S  # noqa: E402


class T(unittest.TestCase):
    def test_decide(self):
        self.assertEqual(S.decide(30, 100), "ok")
        self.assertEqual(S.decide(700, 100), "restart")
        self.assertEqual(S.decide(None, 100), "restart")
        self.assertEqual(S.decide(30, 0), "start")
        self.assertEqual(S.decide(None, 0), "start")

    def test_age(self):
        d = Path(tempfile.mkdtemp())
        now = time.time()
        ts = (datetime.fromtimestamp(now) - timedelta(seconds=700)).astimezone().isoformat()
        (d / "h.json").write_text('{"ts": "%s"}' % ts, encoding="utf-8")
        self.assertAlmostEqual(S.heartbeat_age(d / "h.json", "ts", now), 700, delta=2)
        self.assertIsNone(S.heartbeat_age(d / "none.json", "ts", now))

    def test_run_once_restarts_only_stale(self):
        starts, kills = [], []
        S.LOG = Path(tempfile.mkdtemp()) / "s.log"
        ages = {"state.json": 5, "watch-heartbeat.json": 5}
        orig = S.heartbeat_age
        S.heartbeat_age = lambda p, f, n: 900 if p.name == "watch-heartbeat.json" else 5
        try:
            r = S.run_once(start=lambda s, o, e: starts.append(s.name) or 0, alive=lambda p: 42, kill=kills.append)
        finally:
            S.heartbeat_age = orig
        self.assertEqual(r, {"dispatch": "ok", "watch": "restart"})
        self.assertEqual((starts, kills), (["watch.py"], [42]))
        self.assertIn("watch: restart", S.LOG.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
