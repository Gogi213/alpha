"""TK-476: закачка суток архива сделок Bybit. 403/404 = «нет в архиве» — без повторов (раньше 403 повторялся 6 раз со сном
42 с на сутки: 25 336 суток ≈ 6,2 ч из 7,5 ч скачивания); прочие ошибки — провал (строка), повтор запуском."""
import importlib.util, os, tempfile, unittest, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("closed", os.path.join(HERE, "..", "tk476-bybit-closed.py"))
closed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(closed)


def http_error(code):
    return urllib.error.HTTPError("u", code, "x", None, None)


class Fetch(unittest.TestCase):
    def setUp(self):
        self.raw = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.raw, "S"))
        self.calls = 0
        self.orig = closed.urllib.request.urlopen
        self.orig_sleep = closed.time.sleep
        closed.time.sleep = lambda s: None

    def tearDown(self):
        closed.urllib.request.urlopen = self.orig
        closed.time.sleep = self.orig_sleep

    def fail_with(self, code):
        def f(url, timeout=0):
            self.calls += 1
            raise http_error(code)
        closed.urllib.request.urlopen = f

    def test_403_and_404_are_absent_without_retry(self):
        for code in (403, 404):
            self.calls = 0
            self.fail_with(code)
            self.assertEqual(closed.fetch(("S", "2024-01-01", self.raw)), ("absent", "S", "2024-01-01"))
            self.assertEqual(self.calls, 1)

    def test_other_error_is_failure_string(self):
        self.fail_with(500)
        r = closed.fetch(("S", "2024-01-01", self.raw))
        self.assertIsInstance(r, str)
        self.assertEqual(self.calls, 6)

    def test_existing_file_skipped(self):
        open(os.path.join(self.raw, "S", "S2024-01-01.csv.gz"), "wb").close()
        self.fail_with(403)
        self.assertIsNone(closed.fetch(("S", "2024-01-01", self.raw)))
        self.assertEqual(self.calls, 0)


if __name__ == "__main__":
    unittest.main()
