#!/usr/bin/env python3
"""П-42 §13.24 / §13.24.2 (TK-599): драйвер счёта на srv — движок по окнам `wa/wb/wc`, таблицы признаков группы 6, ворота, шаг 0, автомат, заморозка.
Запускать на srv под `systemd-run` (не с ПК), код — из рабочего дерева ветки (rsync). Всё, что запускает движок, требует `TK599_GO=1` (ставит координатор после «go»; до того — только `plan`).

  python3 retitr_full_run.py plan                  # печать команд (движок, сборка таблиц признаков, ворота) без запуска и без чтения чего-либо
  TK599_GO=1 python3 retitr_full_run.py gates      # G1–G3b: хэши бинаря/монет/манифестов, «окна без потерь», обрезка конца данных (G5 — следующей командой)
  TK599_GO=1 python3 retitr_full_run.py gate5      # только G5 (сигнальный поток базы стороны → доля непустых значений признаков по месяцам)
  TK599_GO=1 python3 retitr_full_run.py run        # ворота → шаг 0 → проходы 1–2, совместная сетка, свёртки, проверка вперёд → results.pkl, results.json

Команда движка на окно (последовательно по окнам; каждый вариант считает ОБЕ стороны, лишняя не читается):
  env P42_T0=<T0> <bin> breakout tick --cache <root>/<w>/cache --trades <root>/<w>/aggtrades --variants <файл> --symbols "$(cat symbols.txt)" --out <root>/out/<w> --threads 6 [--ftab <union>]
Запросы делятся на «без фильтра» (одним вызовом) и «с фильтром признака» (по сторонам: в таблице обязаны быть столбцы всех `fa` вызова и ключи всех кандидатов потока варианта).
Таблицы признаков (§13.24.2 п. 6): на окно — движок `P42_SIGSX_ONLY=1 P42_SIGSX=14:73` по потоку конфигурации после группы 5 без `F` → `retitr_ftab.py build … --base-date <T0 окна>`
(производственный код TK-588/592/594) → слияние в «union» окна и стороны (ключ (sym, sig_ms) уникален, расхождение значений — ошибка).
Протокол: docs/research/P-42-level-breakout.md §13.24, §13.24.1, §13.24.2; тесты — tools/p42/tests/test_retitr_full_run.py (раннер подпроцессов подменяется).
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import pickle
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retitr_cells as rc  # noqa: E402
import retitr_ftab as rfx  # noqa: E402
import retitr_full as rf  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
ROOT = "/data/p42-tk599"
BIN = ROOT + "/bin/alpha-master"
SYMBOLS = "/data/p42-t566/symbols.txt"
FUNDING = "/data/p42-metrics/funding"
METRICS_MAIN = "/data/p42-metrics"                    # метрики OI 18.07–13.11 (чужой каталог, только чтение)
PROD = "/data/tk594/py"
GRIDS = os.path.join(REPO, "docs", "research", "freeze", "tk599-grids.json")
MANIFESTS = os.path.join(REPO, "docs", "research", "freeze", "tk599-manifests.txt")
C595 = os.path.join(REPO, "docs", "research", "freeze", "tk595-aug-variants.txt")
GO_ENV = "TK599_GO"
DONE_MARK = "DONE.tk599"                                 # метка в каталоге варианта: пакет с ним завершился на всех трёх окнах
THREADS = 6
SIGSX_DAYS = "14:73"                                   # торговая часть сетки движка (прогрев 14 суток, торговое окно 60 суток: 14…73, §13.24.3)
RECORDED = {
    "bin_sha256": "f4a0d5a348757e23ec231f09796e9d2ca414eaaa08a9731ca6f356a3084cebca",
    "bin_md5": "a03c91df5a70c8eb32cbb7122943b8f9",
    "symbols_sha256": "25c9ad8caab30af583b18f0678189f381282cce23b5d42c043471481bf0eaae0",
    "grids_sha256": "e1d6bded92ddd44c68c130b98187756f9f3eaa36759291a619435e0e9a369e5d",
    "manifests_sha256": "e377de791796bcc93bee2ad5ad70c5ee69cf1e45499369637cdf293e07c9bd59",   # §13.24.4 п. 8 (глубина предыстории H = 32, 18.11); до этого 06e0993e… (§13.24.3 п. 7), 9f52379e…
}
GuardError = rf.GuardError


def require_go(env=None):
    if (env if env is not None else os.environ).get(GO_ENV) != "1":
        raise GuardError("%s=1 не задан: запуск движка до «go» координатора запрещён (§13.24, TK-598 заморожен и прочитан)" % GO_ENV)


# ---- пути, раннер ---------------------------------------------------------------------------------------------------------------

@dataclasses.dataclass
class Paths:
    root: str = ROOT

    def j(self, *a):
        return os.path.join(self.root, *a)

    def cache(self, w):
        return self.j(w, "cache")

    def trades(self, w):
        return self.j(w, "aggtrades")

    def out(self, w):                                   # корень выходов портфельных прогонов окна: <out>/<vid>/{long,short}-*.csv
        return self.j("out", w)

    def sx(self, w):                                    # выходы сигнальных дампов окна: <sx>/<vid>/{long,short}-sigsx.csv
        return self.j("sx", w)

    def union(self, w, side):
        return self.j("ftab", w, "%s-union.csv" % side)

    def need_dir(self, w, need):
        return self.j("ftab", w, need.vid)

    def need_tab(self, w, need):
        return os.path.join(self.need_dir(w, need), "ftab.csv")

    def ready(self, w, need):
        return self.j("ftab", w, "ready", need.hash)

    @property
    def metrics_jun_jul(self):                          # докачка 17.06–17.07 (юнит tk599-metrics-dl; по окончании — log/done)
        return self.j("metrics-jun-jul")

    @property
    def metrics_all(self):                              # дерево ссылок на оба источника: `TK594_METRICS` производственного кода читает один каталог
        return self.j("metrics-all")

    @property
    def var(self):
        return self.j("var")

    @property
    def npz(self):
        return self.j("npz")

    @property
    def log(self):
        return self.j("log")

    @property
    def gate(self):
        return self.j("gate")


class SubprocessRunner:
    """Запуск подпроцесса: код возврата; вывод — в журнал. Подменяется в тестах."""

    def run(self, cmd, env=None, log=None):
        e = dict(os.environ)
        e.update(env or {})
        if not log:
            return subprocess.run(cmd, env=e).returncode
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "ab") as f:
            f.write(("$ %s\n" % show_cmd(cmd, env)).encode("utf-8"))
            f.flush()
            return subprocess.run(cmd, env=e, stdout=f, stderr=subprocess.STDOUT).returncode


def show_cmd(cmd, env=None, width=160):
    """Команда для журнала / `plan`: длинные значения (список монет) сокращены."""
    toks = [t if len(t) <= width else t[:40] + "…(%d симв.)" % len(t) for t in cmd]
    return " ".join(["%s=%s" % kv for kv in sorted((env or {}).items())] + toks)


def tail(path, n=12):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return "(журнала нет)"


def metrics_tree(paths, sources=None):
    """Дерево ссылок `<root>/metrics-all/<SYM>/<SYM>-metrics-<день>.zip` на оба источника метрик (18.07–13.11 и 17.06–17.07); существующие ссылки не трогаются, `.sha256` не нужны.
    Требует окончания докачки (`metrics-jun-jul/log/done`) — иначе июльские признаки OI пусты и G5 снимет их зря. Возвращает число новых ссылок."""
    if sources is None:
        if not os.path.exists(os.path.join(paths.metrics_jun_jul, "log", "done")):
            raise GuardError("докачка метрик 17.06–17.07 не закончена (нет %s/log/done)" % paths.metrics_jun_jul)
        sources = (METRICS_MAIN, paths.metrics_jun_jul)
    n = 0
    for src in sources:
        for sym in sorted(os.listdir(src)):
            sd = os.path.join(src, sym)
            if sym in ("funding", "log") or not os.path.isdir(sd):
                continue
            dd = os.path.join(paths.metrics_all, sym)
            os.makedirs(dd, exist_ok=True)
            for fn in os.listdir(sd):
                if fn.endswith(".zip"):
                    dst = os.path.join(dd, fn)
                    if not os.path.lexists(dst):
                        os.symlink(os.path.realpath(os.path.join(sd, fn)), dst)
                        n += 1
    return n


def read_symbols(path):
    text = open(path, encoding="utf-8").read().rstrip("\n")            # как `$(cat файл)`: хвостовые переводы строки срезаются
    return text, [x for x in text.replace("\n", ",").split(",") if x]


def variants_text(reqs):
    return "".join("%s %s\n" % (r.vid, r.engine_keys) for r in reqs)


def chunks(xs, n):
    if not n or n <= 0:
        yield list(xs)
        return
    for i in range(0, len(xs), n):
        yield list(xs[i:i + n])


# ---- движок ---------------------------------------------------------------------------------------------------------------------

class Engine:
    """Портфельные прогоны: `calls(reqs)` — план вызовов (чистая функция), `launch(reqs)` — выполнение (для `OutDirEvaluator(launch=…)`)."""

    def __init__(self, paths, runner, symbols_text, bin_path=BIN, threads=THREADS, chunk=None, ensure_tables=None, windows=rf.WINDOWS):
        self.paths, self.runner, self.symbols, self.bin, self.threads, self.chunk = paths, runner, symbols_text, bin_path, threads, chunk
        self.ensure_tables, self.windows = ensure_tables, windows
        self.n = 0

    def command(self, w, vfile, out, ftab=None, sigsx_only=False):
        cmd = [self.bin, "breakout", "tick", "--cache", self.paths.cache(w.name), "--trades", self.paths.trades(w.name), "--variants", vfile,
               "--symbols", self.symbols, "--out", out, "--threads", str(self.threads)]
        if ftab:
            cmd += ["--ftab", ftab]
        env = {"P42_T0": w.t0}
        if sigsx_only:
            env.update({"P42_SIGSX_ONLY": "1", "P42_SIGSX": SIGSX_DAYS})
        return cmd, env

    def groups(self, reqs):
        """[(сторона таблицы или None, [запросы])]: без фильтра — один список, с фильтром признака (`fa=`) — по сторонам; порядок стабилен."""
        plain = [r for r in reqs if "fa=" not in r.keys]
        out = [(None, plain)] if plain else []
        for s in rf.SIDES:
            fr = [r for r in reqs if "fa=" in r.keys and r.side == s]
            if fr:
                out.append((s, fr))
        res = []
        for side, rs in out:
            for part in chunks(rs, self.chunk):
                res.append((side, part))
        return res

    @staticmethod
    def stream_need(req):
        """Поток кандидатов варианта с фильтром: те же ключи без `fa/fo/fv` (фильтр применяется к потоку до портфеля и сам поток не меняет)."""
        plain, _ = rc.parse_keys(req.keys)
        return rf.FtabNeed(req.side, " ".join("%s=%s" % kv for kv in plain))

    def done(self, req):
        return all(os.path.exists(os.path.join(self.paths.out(w.name), req.vid, DONE_MARK)) for w in self.windows)

    def launch(self, reqs):
        reqs = [r for r in reqs if not self.done(r)]                    # вариант, посчитанный на всех окнах в прошлом пакете, не пересчитывается
        for side, part in self.groups(reqs):
            if side is not None:
                needs = {}
                for r in part:
                    n = self.stream_need(r)
                    needs[n.hash] = n
                if self.ensure_tables is None:
                    raise GuardError("нет сборщика таблиц признаков для вызова с фильтром")
                self.ensure_tables(list(needs.values()))
            self.n += 1
            name = "b%04d" % self.n
            vfile = self.paths.var + "/" + name + ".txt"
            os.makedirs(self.paths.var, exist_ok=True)
            with open(vfile, "w", encoding="utf-8", newline="\n") as f:
                f.write(variants_text(part))
            for w in self.windows:
                cmd, env = self.command(w, vfile, self.paths.out(w.name), self.paths.union(w.name, side) if side else None)
                rcode = self.runner.run(cmd, env, os.path.join(self.paths.log, "%s-%s.log" % (name, w.name)))
                if rcode != 0:
                    raise GuardError("движок: окно %s, пакет %s (%d вариантов), код возврата %d\n%s" % (
                        w.name, name, len(part), rcode, tail(os.path.join(self.paths.log, "%s-%s.log" % (name, w.name)))))
                for r in part:
                    p = os.path.join(self.paths.out(w.name), r.vid, rf.ENGINE_SIDE[r.side] + "-trades.csv")
                    if not os.path.exists(p):
                        raise GuardError("движок не записал %s (окно %s, пакет %s)" % (p, w.name, name))
            for w in self.windows:                                       # метка успеха пакета — только после всех окон (оборванный пакет не считается посчитанным)
                for r in part:
                    open(os.path.join(self.paths.out(w.name), r.vid, DONE_MARK), "w").close()


# ---- таблицы признаков --------------------------------------------------------------------------------------------------------------

def _same(a, b):
    return bool(np.array_equal(np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64), equal_nan=True))


def merge_union(path, new_path):
    """Сливает таблицу потока в «union» окна и стороны: ключ (sym, sig_ms) уникален; тот же ключ с другим значением признака — GuardError (ничего не записывается).
    Возвращает (строк в union, новых строк)."""
    syms, ms, cols = rfx.read_ftab(new_path)
    names = list(cols)
    rows = {}
    if os.path.exists(path):
        osyms, oms, ocols = rfx.read_ftab(path)
        if list(ocols) != names:
            raise GuardError("union %s: столбцы %s ≠ столбцы таблицы потока %s" % (path, list(ocols), names))
        for i in range(len(osyms)):
            rows[(osyms[i], int(oms[i]))] = tuple(float(ocols[n][i]) for n in names)
    added = 0
    for i in range(len(syms)):
        k = (syms[i], int(ms[i]))
        v = tuple(float(cols[n][i]) for n in names)
        if k in rows:
            if not _same(rows[k], v):
                raise GuardError("union %s: ключ %s — значения признаков расходятся между потоками: %s ≠ %s" % (path, k, rows[k], v))
        else:
            rows[k] = v
            added += 1
    keys = sorted(rows)
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rfx.write_ftab(tmp, [k[0] for k in keys], np.array([k[1] for k in keys], dtype=np.int64), names,
                   [np.array([rows[k][j] for k in keys], dtype=np.float64) for j in range(len(names))])
    os.replace(tmp, path)
    return len(rows), added


class FtabStore:
    """Сборка таблиц признаков потоков (`FtabNeed`): сигнальный дамп движка → `retitr_ftab.py build` → union окна и стороны; метки готовности по (окно, поток)."""

    def __init__(self, paths, runner, engine, prod=PROD, procs=6, windows=rf.WINDOWS, python=None, prepare=None):
        self.paths, self.runner, self.engine, self.prod, self.procs, self.windows = paths, runner, engine, prod, procs, windows
        self.python = python or sys.executable
        self.prepare, self._prepared = prepare, False                 # prepare() — один раз перед первой сборкой (дерево метрик)
        self._tabs = {}

    def ready(self, need):
        return all(os.path.exists(self.paths.ready(w.name, need)) for w in self.windows)

    def sigsx_file(self, w, need):
        return os.path.join(self.paths.sx(w.name), need.vid, "%s-sigsx.csv" % rf.ENGINE_SIDE[need.side])

    def build_env(self, w):
        """Источники производственного кода TK-594 — на окно: сделки окна (`TK594_TRADES`: `<корень>/<SYM>/<SYM>-aggTrades-<день>.zip`) и метрики обоих источников (`TK594_METRICS`)."""
        return {"TK594_TRADES": self.paths.trades(w.name), "TK594_METRICS": self.paths.metrics_all}

    def build_cmd(self, w, need):
        d = self.paths.need_dir(w.name, need)
        return [self.python, os.path.join(HERE, "retitr_ftab.py"), "build", need.side, "--sigsx", self.sigsx_file(w, need), "--work", d,
                "--out", self.paths.need_tab(w.name, need), "--prod", self.prod, "--cache", self.paths.cache(w.name),
                "--trades", self.paths.trades(w.name), "--procs", str(self.procs), "--base-date", w.t0]

    def build(self, needs):
        seen, todo = set(), []
        for n in needs:
            if n.hash not in seen and not self.ready(n):
                seen.add(n.hash)
                todo.append(n)
        if not todo:
            return
        if self.prepare is not None and not self._prepared:
            self.prepare()
            self._prepared = True
        os.makedirs(self.paths.var, exist_ok=True)
        for w in self.windows:                                          # окна последовательно: один вызов движка на окно на все потоки
            vfile = os.path.join(self.paths.var, "sx-%s-%s.txt" % (w.name, hashlib.sha256("".join(n.hash for n in todo).encode()).hexdigest()[:10]))
            with open(vfile, "w", encoding="utf-8", newline="\n") as f:
                f.write("".join("%s %s\n" % (n.vid, n.keys) for n in todo))
            cmd, env = self.engine.command(w, vfile, self.paths.sx(w.name), None, sigsx_only=True)
            log = os.path.join(self.paths.log, "sx-%s-%s.log" % (w.name, todo[0].vid))
            rcode = self.runner.run(cmd, env, log)
            if rcode != 0:
                raise GuardError("движок (сигнальный дамп), окно %s, код возврата %d\n%s" % (w.name, rcode, tail(log)))
            for n in todo:
                if not os.path.exists(self.sigsx_file(w, n)):
                    raise GuardError("нет сигнального дампа %s" % self.sigsx_file(w, n))
        for n in todo:
            for w in self.windows:
                os.makedirs(self.paths.need_dir(w.name, n), exist_ok=True)
                log = os.path.join(self.paths.log, "ftab-%s-%s.log" % (w.name, n.vid))
                rcode = self.runner.run(self.build_cmd(w, n), self.build_env(w), log)
                if rcode != 0:
                    raise GuardError("retitr_ftab build: окно %s, поток %s, код возврата %d\n%s" % (w.name, n.vid, rcode, tail(log)))
                merge_union(self.paths.union(w.name, n.side), self.paths.need_tab(w.name, n))
                os.makedirs(os.path.dirname(self.paths.ready(w.name, n)), exist_ok=True)
                open(self.paths.ready(w.name, n), "w").close()

    def table(self, w, need):
        """(sym, sig_ms, {признак: значения}) таблицы признаков потока на окне `w` (до слияния)."""
        k = (w.name, need.hash)
        if k not in self._tabs:
            self._tabs[k] = rfx.read_ftab(self.paths.need_tab(w.name, need))
        return self._tabs[k]

    def candidates(self, need):
        """Кандидаты потока в зачётных диапазонах окон: (абсолютные сутки 0…135, {признак: значения}) — перекрытия окон не дублируются."""
        days, cols = [], {}
        for w in self.windows:
            syms, ms, c = self.table(w, need)
            d = rf.abs_day(w, ms)
            m = (d >= (w.lod - rf.D0).days) & (d <= (w.hid - rf.D0).days)
            days.append(d[m])
            for n, v in c.items():
                cols.setdefault(n, []).append(v[m])
        return np.concatenate(days), {n: np.concatenate(v) for n, v in cols.items()}


class FtabProvider:
    """`provider(side, keys, om)` для `run_pass` (группа 6): запрашивает таблицу потока `keys` (после группы 5, без `F`), возвращает границы срезов
    {(fa, fo): {10|20|25: fv}} по кандидатам ОБУЧАЮЩИХ суток `om`; признаки, снятые воротами G5, не участвуют."""

    def __init__(self, ev, store, spec, avail):
        self.ev, self.store, self.spec, self.avail = ev, store, spec, avail
        self.memo = {}

    def __call__(self, side, keys, om):
        need = rf.FtabNeed(side, keys)
        yield from rf._need(self.ev, [need])
        mk = (side, rc.canon(keys), tuple(int(d) for d in om))
        if mk not in self.memo:
            days, cols = self.store.candidates(need)
            out = {}
            for fa, fo in self.spec.features(side):
                if fa in self.avail.get(side, ()):
                    out[(fa, fo)] = rf.feature_cuts(cols[fa], days, om, fo)
            self.memo[mk] = out
        return self.memo[mk]


# ---- ворота ---------------------------------------------------------------------------------------------------------------------

def file_hashes(path):
    sha, md5 = hashlib.sha256(), hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            sha.update(b)
            md5.update(b)
    return sha.hexdigest(), md5.hexdigest()


def parse_manifests(text):
    """`== <окно>` + строки → {окно: [строки]}."""
    out, cur = {}, None
    for ln in text.splitlines():
        if ln.startswith("== "):
            cur = ln[3:].strip()
            out[cur] = []
        elif cur is not None and ln.strip():
            out[cur].append(ln.strip())
    return out


def gate12(paths, bin_path=BIN, symbols=SYMBOLS, grids=GRIDS, manifests=MANIFESTS, recorded=RECORDED):
    """G1 (бинарь md5/sha256), G2 (sha256 монет, сеток, файла манифестов; MANIFEST каждого окна на srv = записанному разделу). Возвращает {проверка: ок}; список невыполненных — в 'fail'."""
    res = {}
    sha, md5 = file_hashes(bin_path)
    res["G1_bin_sha256"], res["G1_bin_md5"] = sha == recorded["bin_sha256"], md5 == recorded["bin_md5"]
    res["G2_symbols_sha256"] = file_hashes(symbols)[0] == recorded["symbols_sha256"]
    res["G2_grids_sha256"] = file_hashes(grids)[0] == recorded["grids_sha256"]
    res["G2_manifests_sha256"] = file_hashes(manifests)[0] == recorded["manifests_sha256"]
    rec = parse_manifests(open(manifests, encoding="utf-8").read())
    for w in rf.WINDOWS + rf.CONV_WINDOWS:
        p = paths.j(w.name, "MANIFEST")
        got = [ln.strip() for ln in open(p, encoding="utf-8").read().splitlines() if ln.strip()] if os.path.exists(p) else None
        res["G2_manifest_" + w.name] = got is not None and got == rec.get(w.name)
    res["fail"] = [k for k, v in res.items() if v is False]
    return res


def gate3(paths, engine, spec, engine_conv=None):
    """G3 «окна без потерь» (§13.24.4 п. 4), G3b (обрезка конца данных) и проверка сходимости (п. 5) на `L_base`, `S_base`: прогон баз на трёх окнах (и на `wbx`, `wcx` — `engine_conv`), различия множеств сделок
    на зачётных кусках перекрытий (`G3_RANGES`) и на зачётных диапазонах окон с T0 на 7 суток раньше (`CONV_PAIRS`), диагностика разгона (`G3_RAMP`, не ворота).
    Возвращает {сторона: {'diff': [...], 'conv': [...], 'ramp': {...}, 'forced_ok': n, 'last_exit_ms': {...}}}; forced вне 13.11 или сделка за концом данных — GuardError."""
    reqs = [rf.Req(s, rf.cfg_keys(spec, s, {}), False) for s in rf.SIDES]
    engine.launch(reqs)
    if engine_conv is not None:
        engine_conv.launch(reqs)
    wins = {w.name: w for w in rf.WINDOWS + rf.CONV_WINDOWS}
    res = {}
    for r in reqs:
        def read(w):
            return rf.read_trades_csv(os.path.join(paths.out(w.name), r.vid, rf.ENGINE_SIDE[r.side] + "-trades.csv"))
        tabs = {w.name: read(w) for w in rf.WINDOWS}
        diff, ramp, conv = [], {}, []
        for a, b, lo, hi in rf.G3_RANGES:
            diff += ["%s–%s: %s" % (a, b, x) for x in rf.overlap_diff(tabs[a], wins[a], tabs[b], wins[b], lo, hi)]
        for a, b, lo, hi in rf.G3_RAMP:
            ramp["%s–%s %s…%s" % (a, b, lo, hi)] = rf.diff_by_day(tabs[a], wins[a], tabs[b], wins[b], lo, hi)
        if engine_conv is not None:
            for m, x in rf.CONV_PAIRS:
                tx = read(wins[x])
                dm = rf.overlap_diff(tabs[m], wins[m], tx, wins[x], wins[x].lo, wins[x].hi)
                conv += ["%s–%s: %s" % (m, x, d) for d in dm]
                ramp["conv %s–%s %s…%s" % (m, x, wins[x].lo, wins[x].hi)] = {"n_trades": len(rf._keyset(tabs[m], wins[m], wins[x].lo, wins[x].hi)), "n_diff": len(dm)}
        forced_ok = rf.check_forced(tabs)
        last = rf.check_data_end(tabs)                                                                  # §13.24.3 п. 7: сделок за концом данных нет
        res[r.side] = {"diff": diff, "conv": conv, "ramp": ramp, "forced_ok": forced_ok, "last_exit_ms": last}
    return res


def g3_failures(g3):
    """{сторона: первые различия} по результату `gate3` (перекрытия и сходимость); пусто — окна без потерь, предыстории достаточно."""
    return {s: (v["diff"] + v.get("conv", []))[:5] for s, v in g3.items() if v["diff"] or v.get("conv")}


def gate5(store, spec, base_keys_by_side=None):
    """G5: признак титруется, если доля непустых значений у кандидатов потока базы стороны ≥ 95 % в каждом из пяти месяцев. Возвращает {сторона: {признак: {ok, shares}}}."""
    out = {}
    for side in rf.SIDES:
        need = rf.FtabNeed(side, (base_keys_by_side or {}).get(side) or rf.cfg_keys(spec, side, {}))
        store.build([need])
        days, cols = store.candidates(need)
        months = rf.MONTH_OF_DAY[days]
        out[side] = {}
        for fa, fo in spec.features(side):
            ok, shares = rf.feature_availability(cols[fa], months)
            out[side][fa] = {"fo": fo, "ok": bool(ok), "shares": shares}
    return out


def avail_of(g5):
    return {s: {fa for fa, v in d.items() if v["ok"]} for s, d in g5.items()}


# ---- сборка контекста, шаг 0, прогон -------------------------------------------------------------------------------------------------

@dataclasses.dataclass
class Ctx:
    paths: Paths
    runner: object
    spec: object
    coins: list
    symbols: str
    engine: Engine
    store: FtabStore
    ev: object
    nboot: int
    engine_conv: object = None                              # движок окон проверки сходимости (wbx, wcx); нужен только воротам G3


def make_ctx(root=ROOT, runner=None, nboot=rf.NBOOT, chunk=None, procs=6, threads=THREADS, bin_path=None, symbols=SYMBOLS, grids=GRIDS, funding=FUNDING,
             prod=PROD, rates=None, python=None, metrics=True):
    paths = Paths(root)
    runner = runner or SubprocessRunner()
    spec = rf.Spec.load(grids)
    text, coins = read_symbols(symbols)
    engine = Engine(paths, runner, text, bin_path or paths.j("bin", "alpha-master"), threads, chunk)
    engine_conv = Engine(paths, runner, text, bin_path or paths.j("bin", "alpha-master"), threads, chunk, windows=rf.CONV_WINDOWS)
    store = FtabStore(paths, runner, engine, prod, procs, python=python, prepare=(lambda: metrics_tree(paths)) if metrics else None)
    engine.ensure_tables = store.build
    os.makedirs(paths.npz, exist_ok=True)
    ev = rf.OutDirEvaluator(paths.j("out"), coins, rates if rates is not None else rf.load_funding_rates(funding, coins), launch=engine.launch, cache_dir=paths.npz,
                            run_t0={w.name: w.t0 for w in rf.WINDOWS}, ftab_builder=store.build, ftab_ready=store.ready)
    return Ctx(paths, runner, spec, coins, text, engine, store, ev, nboot, engine_conv)


def c595_keys(path=C595):
    out = {}
    for ln in open(path, encoding="utf-8").read().splitlines():
        if ln.strip():
            vid, _, keys = ln.partition(" ")
            out[vid] = keys.strip()
    return out


def step0(ctx, c595=None):
    """Шаг 0: ряды баз (`L_base`, `S_base`) и сборок C TK-595 (`L_C`, `S_C`) на всём окне — описательно (по месяцам считает отчёт)."""
    c595 = c595 or c595_keys()
    reqs = {}
    for s in rf.SIDES:
        reqs[(s, "base")] = rf.Req(s, rf.cfg_keys(ctx.spec, s, {}), False)
        reqs[(s, "C")] = rf.Req(s, c595["%s_C" % s], False)
    ctx.ev.fulfil(list(reqs.values()))
    return {s: {k: ctx.ev.get(reqs[(s, k)]) for k in ("base", "C")} for s in rf.SIDES}


def run_all(ctx, g5, gates=None, c595=None):
    """Шаг 0 → автомат (проходы 1–2, совместная сетка, свёртки) → проверка вперёд, собственный вердикт, исход заморозки. Возвращает словарь результатов (pickle)."""
    ev, spec = ctx.ev, ctx.spec
    s0 = step0(ctx, c595)
    provider = FtabProvider(ev, ctx.store, spec, avail_of(g5))
    outcomes = rf.drive(rf.procedure_gens(ev, spec, len(ctx.coins), provider=provider, nboot=ctx.nboot), ev)
    res = {"gates": gates, "g5": g5, "step0": s0, "outcomes": outcomes, "wf": {}, "own_wf": {}, "freeze": {}, "batches": list(ev.batches), "nboot": ctx.nboot}
    for s in rf.SIDES:
        wf = rf.wf_stats(ev, spec, s, outcomes, ctx.nboot)
        res["wf"][s] = wf
        res["own_wf"][s] = rf.wf_own_verdict(ev, spec, s, outcomes, len(ctx.coins), ctx.nboot)
        res["freeze"][s] = rf.freeze_decision(wf)
    return res


def jsonable(o):
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return {k: jsonable(v) for k, v in dataclasses.asdict(o).items()}
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    return o


def summary(res, spec):
    """Короткая JSON-сводка результатов (без рядов): выборы, Δ вперёд, исход, m."""
    out = {}
    for s in rf.SIDES:
        wf = res["wf"][s]
        out[s] = {"freeze": res["freeze"][s], "folds": wf["folds"], "full": wf["full"], "converged_full": wf["converged_full"], "diverge": wf["diverge"],
                  "forward": {k: getattr(wf["stat"], k) for k in ("d", "se", "z", "lower", "dsum", "se_sum", "s_c", "s_r", "n_c", "n_r")},
                  "confirmed": wf["confirmed"], "guard": wf["guard"], "m": wf["m"],
                  "own_wf": {"verdict": res["own_wf"][s]["verdict"], "mean": res["own_wf"][s]["stat"].mean, "lower": res["own_wf"][s]["stat"].lower, "p": res["own_wf"][s]["stat"].p},
                  "final_keys": rf.cfg_keys(spec, s, res["outcomes"]["%s:full" % s].final)}
    return out


# ---- CLI ----------------------------------------------------------------------------------------------------------------------------

class DryRunner:
    def __init__(self):
        self.calls = []

    def run(self, cmd, env=None, log=None):
        self.calls.append((cmd, env))
        print(show_cmd(cmd, env))
        return 0


def cmd_plan(a):
    """Печать команд: сигнальный дамп базы стороны, сборка таблиц признаков, портфельные прогоны баз и сборок C на трёх окнах; ничего не запускает и не читает данных."""
    dry = DryRunner()
    paths = Paths(a.root)
    text = "<566 монет из %s>" % SYMBOLS
    eng = Engine(paths, dry, text, a.bin or paths.j("bin", "alpha-master"), a.threads, a.chunk)
    spec = rf.Spec.load(GRIDS)
    store = FtabStore(paths, dry, eng, a.prod, a.procs)
    c = c595_keys()
    print("# G1/G2: sha256/md5 бинаря, sha256 монет, сеток, манифестов (RECORDED), MANIFEST каждого окна на srv = разделу tk599-manifests.txt")
    for s in rf.SIDES:
        need = rf.FtabNeed(s, rf.cfg_keys(spec, s, {}))
        print("# G5, поток базы %s (%s)" % (s, need.vid))
        for w in rf.WINDOWS:
            vf = paths.var + "/sx-%s.txt" % w.name
            cmd, env = eng.command(w, vf, paths.sx(w.name), None, sigsx_only=True)
            print(show_cmd(cmd, env))
            print(show_cmd(store.build_cmd(w, need)))
    reqs = [rf.Req(s, k, False) for s in rf.SIDES for k in (rf.cfg_keys(spec, s, {}), c["%s_C" % s])]
    print("# шаг 0 / G3: базы и C на трёх окнах (пакет без фильтра)")
    for w in rf.WINDOWS:
        cmd, env = eng.command(w, paths.var + "/b0001.txt", paths.out(w.name), None)
        print(show_cmd(cmd, env))
    print(variants_text(reqs), end="")
    print("# проверка сходимости (§13.24.4 п. 5): базы на окнах с T0 на 7 суток раньше")
    for w in rf.CONV_WINDOWS:
        cmd, env = eng.command(w, paths.var + "/b0001.txt", paths.out(w.name), None)
        print(show_cmd(cmd, env))
    print("# пример вызова с фильтром признака (группа 6): --ftab <union окна и стороны>")
    w = rf.WINDOWS[0]
    print(show_cmd(*eng.command(w, paths.var + "/b00NN.txt", paths.out(w.name), paths.union(w.name, "L"))))
    return 0


def cmd_gates(a):
    require_go()
    ctx = make_ctx(a.root, nboot=a.nboot, chunk=a.chunk, procs=a.procs, threads=a.threads, bin_path=a.bin, prod=a.prod)
    g = run_gates(ctx, with_g5=False)                                   # G1/G2/G3/G3b; G5 — отдельной командой после докачки метрик
    os.makedirs(ctx.paths.gate, exist_ok=True)
    json.dump(jsonable(g), open(os.path.join(ctx.paths.gate, "gates-g123.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(jsonable(g), ensure_ascii=False, indent=1))
    return 0


def run_gates(ctx, with_g5=True):
    g = {"G12": gate12(ctx.paths, ctx.engine.bin)}
    if g["G12"]["fail"]:
        raise GuardError("ворота G1/G2 не пройдены: %s" % g["G12"]["fail"])
    g["G3"] = gate3(ctx.paths, ctx.engine, ctx.spec, ctx.engine_conv)
    bad = g3_failures(g["G3"])
    if bad:
        raise GuardError("G3 «окна без потерь» / сходимость предыстории: различия %s" % bad)
    if with_g5:
        g["G5"] = gate5(ctx.store, ctx.spec)
    return g


def cmd_gate5(a):
    require_go()
    ctx = make_ctx(a.root, nboot=a.nboot, chunk=a.chunk, procs=a.procs, threads=a.threads, bin_path=a.bin, prod=a.prod)
    g5 = gate5(ctx.store, ctx.spec)
    os.makedirs(ctx.paths.gate, exist_ok=True)
    json.dump(g5, open(os.path.join(ctx.paths.gate, "g5.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for s, d in g5.items():
        for fa, v in d.items():
            print("%s %-14s %-3s %s %s" % (s, fa, v["fo"], "титруется" if v["ok"] else "СНЯТ", ["%.3f" % x for x in v["shares"]]))
    return 0


def cmd_run(a):
    require_go()
    ctx = make_ctx(a.root, nboot=a.nboot, chunk=a.chunk, procs=a.procs, threads=a.threads, bin_path=a.bin, prod=a.prod)
    gates = run_gates(ctx)
    os.makedirs(ctx.paths.gate, exist_ok=True)
    json.dump(jsonable(gates), open(os.path.join(ctx.paths.gate, "gates.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    res = run_all(ctx, gates["G5"], gates)
    out = ctx.paths.j("results")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "results.pkl"), "wb") as f:
        pickle.dump(res, f)
    json.dump(jsonable(summary(res, ctx.spec)), open(os.path.join(out, "results.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(jsonable(summary(res, ctx.spec)), ensure_ascii=False, indent=1))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan", "gates", "gate5", "run"])
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--bin", default="")
    ap.add_argument("--prod", default=PROD)
    ap.add_argument("--threads", type=int, default=THREADS)
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--chunk", type=int, default=0, help="вариантов в одном вызове движка (0 — все недостающие одним пакетом)")
    ap.add_argument("--nboot", type=int, default=rf.NBOOT)
    a = ap.parse_args(argv)
    try:
        return {"plan": cmd_plan, "gates": cmd_gates, "gate5": cmd_gate5, "run": cmd_run}[a.cmd](a)
    except GuardError as e:
        print("ОСТАНОВКА (охрана): %s" % e, file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
