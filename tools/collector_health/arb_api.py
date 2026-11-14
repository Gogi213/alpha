"""Вкладка «Арбитраж» страницы здоровья: чтение результата почасового счёта (tools/arb_stats/arb_live.py).

<dir>/arb.json — строки таблицы (одним файлом, отдаётся как есть); <dir>/chart-<gen>.jsonl — ряды графиков, по строке на пару,
смещение и длина строки лежат в строке таблицы (o, l). Только стандартная библиотека; файлы читаются по mtime, пишутся атомарно (rename).
"""
import json
from pathlib import Path
from urllib.parse import parse_qs


class Arb:
    def __init__(self, directory):
        self.dir = Path(directory)
        self._mt = None
        self._body = None
        self._doc = None
        self._idx = {}

    def _load(self):
        p = self.dir / "arb.json"
        try:
            mt = p.stat().st_mtime_ns
        except OSError:
            self._mt = self._body = self._doc = None
            return
        if mt != self._mt:
            body = p.read_bytes()
            doc = json.loads(body)
            self._idx = {(r["c"], r["a"], r["b"]): r for r in doc["rows"]}
            self._mt, self._body, self._doc = mt, body, doc

    def table(self):
        """Тело ответа /api/arb: arb.json как есть; пока первого счёта нет — {"pending": true}."""
        self._load()
        return self._body if self._body is not None else b'{"pending":true}'

    def chart(self, query):
        """Тело ответа /api/arb/chart?c=<монета>&a=<биржа>&b=<биржа>; None — такой пары нет."""
        self._load()
        if self._doc is None:
            return None
        q = {k: v[0] for k, v in parse_qs(query).items()}
        r = self._idx.get((q.get("c"), q.get("a"), q.get("b")))
        if r is None:
            return None
        name = self._doc["chart"]
        if "/" in name or "\\" in name:
            return None
        try:
            with open(self.dir / name, "rb") as f:
                f.seek(r["o"])
                line = f.read(r["l"])
        except OSError:
            return None
        head = {"gen": self._doc["gen"], "t0": self._doc["t0"], "t1": self._doc["t1"], "slot": self._doc["slot"]}
        return json.dumps(head, separators=(",", ":")).encode()[:-1] + b"," + line.strip()[1:]
