"""Вкладка «Арбитраж» страницы здоровья: чтение результата почасового счёта (tools/arb_stats/arb_live.py).

<dir>/arb.json — строки таблицы (отдаётся как есть; если рядом есть turnover.json службы arb_turnover.py — к строкам добавляется оборот за сутки v = [$ первой биржи, $ второй], vwin = [t0, t1]); <dir>/chart-<gen>.jsonl — ряды графиков, по строке на пару,
смещение и длина строки лежат в строке таблицы (o, l). Только стандартная библиотека; файлы читаются по mtime, пишутся атомарно (rename).
"""
import gzip
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs


class Arb:
    def __init__(self, directory):
        self.dir = Path(directory)
        self._mt = None
        self._tab = None            # (тело, etag, сжатое тело) — сжимается один раз на смену файлов
        self._body = None
        self._doc = None
        self._idx = {}

    def _stat(self, name):
        try:
            return (self.dir / name).stat().st_mtime_ns
        except OSError:
            return None

    def _turnover(self):
        """-> ({монета: {биржа: $}}, [t0, t1]) из turnover.json службы arb_turnover.py; нет файла или он не читается — (None, None)."""
        try:
            t = json.loads((self.dir / "turnover.json").read_bytes())
            return t["coin"], [t["t0"], t["t1"]]
        except (OSError, ValueError, KeyError, TypeError):
            return None, None

    def _load(self):
        mt = (self._stat("arb.json"), self._stat("turnover.json"))     # тело пересобирается при смене любого из двух файлов
        if mt[0] is None:
            self._mt = self._body = self._doc = self._tab = None
            return
        if mt != self._mt:
            body = (self.dir / "arb.json").read_bytes()
            doc = json.loads(body)
            coin, win = self._turnover()
            if coin is not None:                # оборот за сутки: v = [$ первой биржи, $ второй]; монеты или биржи нет — ключа нет, страница рисует «—»
                for r in doc["rows"]:
                    c = coin.get(r["c"]) or {}
                    if r["a"] in c and r["b"] in c:
                        r["v"] = [c[r["a"]], c[r["b"]]]
                doc["vwin"] = win
                body = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode()
            self._idx = {(r["c"], r["a"], r["b"]): r for r in doc["rows"]}
            self._tab = (body, '"%s"' % hashlib.md5(body).hexdigest()[:20], gzip.compress(body, 6))
            self._mt, self._body, self._doc = mt, body, doc

    def table(self):
        """(тело, etag, сжатое тело) ответа /api/arb: arb.json как есть; пока первого счёта нет — {"pending": true}."""
        self._load()
        return self._tab or (b'{"pending":true}', '"pending"', None)

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
