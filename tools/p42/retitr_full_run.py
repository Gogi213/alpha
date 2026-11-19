#!/usr/bin/env python3
"""П-42 §13.24 / §13.24.2 (TK-599): драйвер счёта на srv — движок по окнам `wa/wb/wc`, таблицы признаков группы 6, ворота, шаг 0, автомат, заморозка.
Запускать на srv под `systemd-run` (не с ПК), код — из рабочего дерева ветки (rsync). Всё, что запускает движок, требует `TK599_GO=1` (ставит координатор после «go»; до того — только `plan`).

  python3 retitr_full_run.py plan                  # печать команд (движок, сборка таблиц признаков, ворота) без запуска и без чтения чего-либо
  TK599_GO=1 python3 retitr_full_run.py gates      # G1–G3b: хэши бинаря/монет/манифестов, «окна без потерь», обрезка конца данных (G5 — следующей командой)
  TK599_GO=1 python3 retitr_full_run.py gate5      # только G5 (сигнальный поток базы стороны → доля непустых значений признаков по месяцам)
  TK599_GO=1 python3 retitr_full_run.py run        # ворота → шаг 0 → проходы 1–2, совместная сетка, свёртки, проверка вперёд → results.pkl, results.json
  TK599_GO=1 python3 retitr_full_run.py sens [--ns 20,10,15,25]   # §13.24.5: чувствительность порога минимума сделок; только после run.rc и заморозки (tk599-frozen.json); N = 20 — контроль первым → results/sens.json

Команда движка на окно (последовательно по окнам; каждый вариант считает ОБЕ стороны, лишняя не читается):
  env P42_T0=<T0> <bin> breakout tick --cache <root>/<w>/cache --trades <root>/<w>/aggtrades --variants <файл> --symbols "$(cat symbols.txt)" --out <root>/out/<w> --threads 6 [--ftab <таблица потока>]
Запросы делятся на «без фильтра» (одним вызовом) и «с фильтром признака» (по сторонам: в таблице обязаны быть столбцы всех `fa` вызова и ключи всех кандидатов потока варианта).
Таблицы признаков (§13.24.2 п. 6): на окно — движок `P42_SIGSX_ONLY=1 P42_SIGSX=14:73` по потоку конфигурации после группы 5 без `F` → `retitr_ftab.py build … --base-date <T0 окна>`
(производственный код TK-588/592/594) → таблица потока `ftab/<окно>/<vid>/ftab.csv` (§13.24.8: вызов с фильтром читает ТОЛЬКО таблицу своего потока; union нет);
перекрёстная охрана — по столбцам-чистым функциям ключа `(sym, sig_ms)` (`big1800`, `oi_c24`, `loi_turn`) в `<S|L>-pure.csv`: расхождение значений — ошибка.
Протокол: docs/research/P-42-level-breakout.md §13.24, §13.24.1, §13.24.2; тесты — tools/p42/tests/test_retitr_full_run.py (раннер подпроцессов подменяется).
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import pickle
import re
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
FROZEN = os.path.join(REPO, "docs", "research", "freeze", "tk599-frozen.json")        # заморозка N = 20 (§13.24 п. 6); `sens` без неё не стартует (§13.24.5 п. 7)
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
# §13.24.6 (TK-607): допустимые бинари движка {md5: sha256}: a03c91df (§13.24) и бинарь «только скорость» (склад суток сделок, кэш сигналов; ворота и PASS критика — docs/findings/p42-speed-2026-11-18.md §9–§10).
ACCEPTED_BIN = {RECORDED["bin_md5"]: RECORDED["bin_sha256"], "a112684a495e61cf5219610e4eecff22": "ee28fd7b85eada7dd30ef96e3a91bba14df0489fb929c7a7e7163c8b72a49b53"}
GuardError = rf.GuardError


def require_go(env=None):
    if (env if env is not None else os.environ).get(GO_ENV) != "1":
        raise GuardError("%s=1 не задан: запуск движка до «go» координатора запрещён (§13.24, TK-598 заморожен и прочитан)" % GO_ENV)


# ---- пути, раннер ---------------------------------------------------------------------------------------------------------------

@dataclasses.dataclass
class Paths:
    root: str = ROOT
    tick_store: str = ""                                # TK-607: каталог склада суток сделок (`--tick-store`); пусто — прежний путь без склада
    sig_cache: str = ""                                 # TK-607: каталог кэша сигналов (`--sig-cache`)
    store_cap_gb: int = 3
    store_min_free_gb: int = 5

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

    def union(self, w, side):                           # §13.24.8: больше не пишется и не читается (история до 19.11)
        return self.j("ftab", w, "%s-union.csv" % side)

    def pure(self, w, side):                            # §13.24.8: столбцы — чистые функции (sym, sig_ms); перекрёстная охрана между потоками
        return self.j("ftab", w, "%s-pure.csv" % side)

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

    @property
    def stop(self):                                     # файл остановки на границе раунда (`touch` → драйвер останавливается перед следующим раундом, код 75)
        return self.j("STOP")


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


# ---- таблица потока: охрана и сверка счётчиков фильтра (§13.24.8, условия критика) ----------------------------------------------------

def stream_table(path):
    """Таблица потока `ftab/<окно>/<vid>/ftab.csv`: ключ (sym, sig_ms) уникален внутри файла, иначе GuardError (условие 1). → (монеты, sig_ms, {признак: массив})."""
    if not os.path.exists(path):
        raise GuardError("нет таблицы потока %s" % path)
    syms, ms, cols = rfx.read_ftab(path)
    if len(set(zip(syms, (int(v) for v in ms)))) != len(syms):
        raise GuardError("таблица потока %s: ключ (sym, sig_ms) не уникален (%d строк)" % (path, len(syms)))
    return syms, ms, cols


def filter_counts_py(tab, patch):
    """Те же счётчики, что у движка (`ftab.rs::passes`), по той же таблице: нет значения / NaN — проходит; `lo` отбрасывает value < fv, `hi` — value > fv. → (строк, отброшено)."""
    col = tab[2].get(patch["fa"])
    if col is None:
        raise GuardError("в таблице потока нет столбца %s" % patch["fa"])
    fv = float(patch["fv"])
    drop = (~np.isnan(col)) & ((col > fv) if patch["fo"] == "hi" else (col < fv))
    return int(len(col)), int(drop.sum())


def check_filter_counts(meta_path, side_name, req, tab):
    """Условие 2 критика (§13.24.8 п. 5): до шага портфеля — `no_key = 0` и счётчики движка (кандидатов, отброшено) равны счёту Python по таблице потока;
    GuardError при любом расхождении. → строка для журнала (кандидатов, строк таблицы, отброшено движком / Python, прошло движком / Python, no_key)."""
    with open(meta_path, encoding="utf-8") as f:
        st = json.load(f)[side_name].get("filter_[cands,dropped,no_key,no_value]")
    if st is None:
        raise GuardError("%s: в meta.json нет счётчиков фильтра признака" % req.vid)
    _plain, feats = rc.parse_keys(req.keys)
    if len(feats) != 1:
        raise GuardError("%s: у варианта с фильтром должен быть ровно один признак (§13.24.2), а их %d" % (req.vid, len(feats)))
    rows, drop = filter_counts_py(tab, feats[0])
    cands, dropped, no_key = int(st[0]), int(st[1]), int(st[2])
    if no_key != 0:
        raise GuardError("%s: no_key = %d (таблица потока не покрывает кандидатов)" % (req.vid, no_key))
    if cands != rows or dropped != drop:
        raise GuardError("%s: счётчики фильтра движка (кандидатов %d, отброшено %d) ≠ счёт Python по таблице потока (строк %d, отброшено %d)" % (req.vid, cands, dropped, rows, drop))
    return {"vid": req.vid, "cands": cands, "rows": rows, "dropped_engine": dropped, "dropped_py": drop, "kept_engine": cands - dropped, "kept_py": rows - drop, "no_key": no_key}


# ---- движок ---------------------------------------------------------------------------------------------------------------------

class Engine:
    """Портфельные прогоны: `calls(reqs)` — план вызовов (чистая функция), `launch(reqs)` — выполнение (для `OutDirEvaluator(launch=…)`)."""

    def __init__(self, paths, runner, symbols_text, bin_path=BIN, threads=THREADS, chunk=None, ensure_tables=None, windows=rf.WINDOWS, prefix="b"):
        self.paths, self.runner, self.symbols, self.bin, self.threads, self.chunk = paths, runner, symbols_text, bin_path, threads, chunk
        self.ensure_tables, self.windows = ensure_tables, windows
        self.prefix = prefix                                            # пакеты `<prefix>NNNN`: у `sens` — «s» (журналы основного прогона `b0001…` не перезаписываются)
        self.n = self.last_batch()                                      # после перезапуска нумерация продолжается: файлы `var/bNNNN.txt` и журналы не перезаписываются

    def last_batch(self):
        """Наибольший номер пакета `<prefix>NNNN` среди журналов `log/<prefix>NNNN-<окно>.log` и `var/<prefix>NNNN.txt` (0 — нет ни одного)."""
        best = 0
        for d in (self.paths.log, self.paths.var):
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for fn in names:
                m = re.fullmatch(r"%s(\d{4})(?:-[a-z]+\.log|\.txt)" % re.escape(self.prefix), fn)
                if m:
                    best = max(best, int(m.group(1)))
        return best

    def command(self, w, vfile, out, ftab=None, sigsx_only=False):
        cmd = [self.bin, "breakout", "tick", "--cache", self.paths.cache(w.name), "--trades", self.paths.trades(w.name), "--variants", vfile,
               "--symbols", self.symbols, "--out", out, "--threads", str(self.threads)]
        if ftab:
            cmd += ["--ftab", ftab]
        if self.paths.tick_store:
            cmd += ["--tick-store", self.paths.tick_store, "--tick-store-cap-gb", str(self.paths.store_cap_gb),
                    "--tick-store-min-free-gb", str(self.paths.store_min_free_gb)]
        if self.paths.sig_cache:
            cmd += ["--sig-cache", self.paths.sig_cache]
        env = {"P42_T0": w.t0}
        if sigsx_only:
            env.update({"P42_SIGSX_ONLY": "1", "P42_SIGSX": SIGSX_DAYS})
        return cmd, env

    def groups(self, reqs):
        """[(сторона таблицы или None, [запросы])]: без фильтра — один список, с фильтром признака (`fa=`) — по сторонам и потокам (§13.24.8); порядок стабилен."""
        plain = [r for r in reqs if "fa=" not in r.keys]
        out = [(None, plain)] if plain else []
        for s in rf.SIDES:                                              # §13.24.8: вызов с фильтром — на ОДИН поток (его собственная таблица в `--ftab`)
            by = {}
            for r in reqs:
                if "fa=" in r.keys and r.side == s:
                    by.setdefault(self.stream_need(r).hash, []).append(r)
            for h in sorted(by):
                out.append((s, by[h]))
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
            need = None
            if side is not None:
                needs = {}
                for r in part:
                    n = self.stream_need(r)
                    needs[n.hash] = n
                if len(needs) != 1:
                    raise GuardError("вызов с фильтром должен быть на один поток (§13.24.8), получено %d" % len(needs))
                if self.ensure_tables is None:
                    raise GuardError("нет сборщика таблиц признаков для вызова с фильтром")
                need = next(iter(needs.values()))
                self.ensure_tables([need])
            self.n += 1
            name = "%s%04d" % (self.prefix, self.n)
            vfile = self.paths.var + "/" + name + ".txt"
            os.makedirs(self.paths.var, exist_ok=True)
            with open(vfile, "w", encoding="utf-8", newline="\n") as f:
                f.write(variants_text(part))
            for w in self.windows:
                tpath = self.paths.need_tab(w.name, need) if need else None      # §13.24.8: таблица ИМЕННО этого потока (по его vid), не «последняя таблица стороны»
                tab = stream_table(tpath) if need else None                      # охрана уникальности ключа в файле — до вызова движка
                cmd, env = self.command(w, vfile, self.paths.out(w.name), tpath)
                rcode = self.runner.run(cmd, env, os.path.join(self.paths.log, "%s-%s.log" % (name, w.name)))
                if rcode != 0:
                    raise GuardError("движок: окно %s, пакет %s (%d вариантов), код возврата %d\n%s" % (
                        w.name, name, len(part), rcode, tail(os.path.join(self.paths.log, "%s-%s.log" % (name, w.name)))))
                for r in part:
                    p = os.path.join(self.paths.out(w.name), r.vid, rf.ENGINE_SIDE[r.side] + "-trades.csv")
                    if not os.path.exists(p):
                        raise GuardError("движок не записал %s (окно %s, пакет %s)" % (p, w.name, name))
                if need:                                                         # сверка счётчиков фильтра — до того, как результат попадёт в шаги процедуры
                    os.makedirs(self.paths.log, exist_ok=True)
                    with open(os.path.join(self.paths.log, "filtercheck.tsv"), "a", encoding="utf-8") as f:
                        for r in part:
                            c = check_filter_counts(os.path.join(self.paths.out(w.name), r.vid, "meta.json"), rf.ENGINE_SIDE[r.side], r, tab)
                            f.write("%s\t%s\t%s\t%s\t%s\t%d\t%d\t%d\t%d\t%d\t%d\t%d\n" % (name, w.name, need.vid, c["vid"], r.side, c["cands"], c["rows"], c["dropped_engine"],
                                                                                         c["dropped_py"], c["kept_engine"], c["kept_py"], c["no_key"]))
            for w in self.windows:                                       # метка успеха пакета — только после всех окон (оборванный пакет не считается посчитанным)
                for r in part:
                    open(os.path.join(self.paths.out(w.name), r.vid, DONE_MARK), "w").close()


# ---- таблицы признаков --------------------------------------------------------------------------------------------------------------

def _same(a, b):
    return bool(np.array_equal(np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64), equal_nan=True))


def pure_cols(side):
    """Столбцы таблицы признаков, которые считает `alpha-feat` (`retitr_ftab.TK594_MODE`): чистые функции `(sym, sig_ms)` — одинаковы у всех потоков (§13.24.8 п. 2).
    Остальные — из строк эпизодов сигнального дампа движка, зависят от параметров потока и между потоками не сравниваются."""
    return tuple(n for n, _ in rfx.FEATURES[side] if n in rfx.TK594_MODE)


def merge_union(path, new_path, only=None):
    """Сливает таблицу потока в общий файл окна и стороны: ключ (sym, sig_ms) уникален; тот же ключ с другим значением признака — GuardError (ничего не записывается).
    `only` — перечень столбцов (§13.24.8: общий файл `pure` — только чистые функции ключа); None — все. Возвращает (строк в файле, новых строк)."""
    syms, ms, cols = rfx.read_ftab(new_path)
    names = [n for n in cols if only is None or n in only]
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
    """Сборка таблиц признаков потоков (`FtabNeed`): сигнальный дамп движка → `retitr_ftab.py build` → таблица потока (+ охрана `pure`, §13.24.8); метки готовности по (окно, поток)."""

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
        env = {"TK594_TRADES": self.paths.trades(w.name), "TK594_METRICS": self.paths.metrics_all}
        if os.environ.get("P42_FEAT_BIN"):
            env["P42_FEAT_BIN"] = os.environ["P42_FEAT_BIN"]            # Rust-сборщик признаков (TK-609); без переменной строит Python-эталон
        return env

    def build_cmd(self, w, need):
        d = self.paths.need_dir(w.name, need)
        return [self.python, os.path.join(HERE, "retitr_ftab.py"), "build", need.side, "--sigsx", self.sigsx_file(w, need), "--work", d,
                "--out", self.paths.need_tab(w.name, need), "--prod", self.prod, "--cache", self.paths.cache(w.name),
                "--trades", self.paths.trades(w.name), "--procs", str(self.procs), "--base-date", w.t0]

    def feat_bin(self):
        """Сборщик признаков потока/деривативов: `P42_FEAT_BIN` (Rust `alpha-feat`, TK-609; читает `retitr_ftab.run_tk594` из окружения) или Python-эталон. → (путь или 'python', md5)."""
        fb = os.environ.get("P42_FEAT_BIN", "")
        if not fb:
            return "python", ""
        if not os.path.isfile(fb):
            raise GuardError("P42_FEAT_BIN=%s: файла нет — таблицы признаков не собираются (§13.24: сборщик задан, но недоступен)" % fb)
        return fb, file_hashes(fb)[1]

    def build(self, needs):
        """Сборка таблиц потоков. Возобновляема по (окно, поток): метка `ready` окна — таблица готова, сигнальный дамп и сборка для неё не повторяются (остановка/перезапуск теряет только таблицу в работе);
        `ready(need)` — метки на всех окнах. Кто собрал таблицу (Python / alpha-feat + md5) — в `built-by.json` каталога потока."""
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
        todo_w = {w.name: [n for n in todo if not os.path.exists(self.paths.ready(w.name, n))] for w in self.windows}
        for w in self.windows:                                          # окна последовательно: один вызов движка на окно на все ещё не готовые на нём потоки
            wt = todo_w[w.name]
            if not wt:
                continue
            vfile = os.path.join(self.paths.var, "sx-%s-%s.txt" % (w.name, hashlib.sha256("".join(n.hash for n in wt).encode()).hexdigest()[:10]))
            with open(vfile, "w", encoding="utf-8", newline="\n") as f:
                f.write("".join("%s %s\n" % (n.vid, n.keys) for n in wt))
            cmd, env = self.engine.command(w, vfile, self.paths.sx(w.name), None, sigsx_only=True)
            log = os.path.join(self.paths.log, "sx-%s-%s.log" % (w.name, wt[0].vid))
            rcode = self.runner.run(cmd, env, log)
            if rcode != 0:
                raise GuardError("движок (сигнальный дамп), окно %s, код возврата %d\n%s" % (w.name, rcode, tail(log)))
            for n in wt:
                if not os.path.exists(self.sigsx_file(w, n)):
                    raise GuardError("нет сигнального дампа %s" % self.sigsx_file(w, n))
        fb, fb_md5 = self.feat_bin()
        for n in todo:
            for w in self.windows:
                if os.path.exists(self.paths.ready(w.name, n)):
                    continue
                os.makedirs(self.paths.need_dir(w.name, n), exist_ok=True)
                log = os.path.join(self.paths.log, "ftab-%s-%s.log" % (w.name, n.vid))
                rcode = self.runner.run(self.build_cmd(w, n), self.build_env(w), log)
                if rcode != 0:
                    raise GuardError("retitr_ftab build: окно %s, поток %s, код возврата %d\n%s" % (w.name, n.vid, rcode, tail(log)))
                merge_union(self.paths.pure(w.name, n.side), self.paths.need_tab(w.name, n), only=pure_cols(n.side))        # §13.24.8: охрана чистых столбцов; движок читает таблицу потока
                with open(os.path.join(self.paths.need_dir(w.name, n), "built-by.json"), "w", encoding="utf-8") as f:
                    json.dump({"feat_bin": fb, "feat_bin_md5": fb_md5}, f)
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
    accepted = dict(ACCEPTED_BIN) if recorded is RECORDED else {recorded["bin_md5"]: recorded["bin_sha256"]}      # §13.24.6: a03c91df и бинарь, записанный в протоколе до смены
    res["G1_bin_sha256"], res["G1_bin_md5"] = accepted.get(md5) == sha, md5 in accepted
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
    stop: object = None                                     # `stop()` → истина ⇒ остановка перед следующим раундом (файл STOP); None — без остановки


def make_ctx(root=ROOT, runner=None, nboot=rf.NBOOT, chunk=None, procs=6, threads=THREADS, bin_path=None, symbols=SYMBOLS, grids=GRIDS, funding=FUNDING,
             prod=PROD, rates=None, python=None, metrics=True, prefix="b", tick_store="", sig_cache=""):
    bin_file = bin_path or os.path.join(root, "bin", "alpha-master")
    bin_md5 = file_hashes(bin_file)[1] if os.path.exists(bin_file) else None
    if bin_md5 in ACCEPTED_BIN and bin_md5 != RECORDED["bin_md5"]:                                  # бинарь «только скорость» (§13.24.6): склад суток сделок и кэш сигналов по умолчанию; "off" — выключить
        tick_store, sig_cache = tick_store or os.path.join(root, "tick-store"), sig_cache or os.path.join(root, "sig-cache")
    tick_store, sig_cache = ("" if tick_store == "off" else tick_store), ("" if sig_cache == "off" else sig_cache)
    for d in (tick_store, sig_cache):
        if d:
            os.makedirs(d, exist_ok=True)
    paths = Paths(root, tick_store=tick_store, sig_cache=sig_cache)
    runner = runner or SubprocessRunner()
    spec = rf.Spec.load(grids)
    text, coins = read_symbols(symbols)
    engine = Engine(paths, runner, text, bin_path or paths.j("bin", "alpha-master"), threads, chunk, prefix=prefix)
    engine_conv = Engine(paths, runner, text, bin_path or paths.j("bin", "alpha-master"), threads, chunk, windows=rf.CONV_WINDOWS)
    store = FtabStore(paths, runner, engine, prod, procs, python=python, prepare=(lambda: metrics_tree(paths)) if metrics else None)
    engine.ensure_tables = store.build
    os.makedirs(paths.npz, exist_ok=True)
    ev = rf.OutDirEvaluator(paths.j("out"), coins, rates if rates is not None else rf.load_funding_rates(funding, coins), launch=engine.launch, cache_dir=paths.npz,
                            run_t0={w.name: w.t0 for w in rf.WINDOWS}, ftab_builder=store.build, ftab_ready=store.ready,
                            bin_md5=bin_md5, allowed_md5=set(ACCEPTED_BIN), legacy_md5=RECORDED["bin_md5"])
    return Ctx(paths, runner, spec, coins, text, engine, store, ev, nboot, engine_conv, stop=lambda: os.path.exists(paths.stop))


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
    outcomes = rf.drive(rf.procedure_gens(ev, spec, len(ctx.coins), provider=provider, nboot=ctx.nboot), ev, stop=ctx.stop)
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


# ---- §13.24.5: чувствительность порога минимума сделок ---------------------------------------------------------------------------------

def parse_ns(text):
    """`--ns 20,10,15,25` → [20, 10, 15, 25]: значения из `SENS_N` без повторов, контрольный N = 20 — первым (§13.24.5 п. 4)."""
    ns = [int(x) for x in str(text).split(",") if x.strip()]
    if not ns or len(set(ns)) != len(ns) or any(n not in rf.SENS_N for n in ns):
        raise GuardError("--ns: нужны значения из %s без повторов, получено %s" % (list(rf.SENS_N), ns))
    if ns[0] != rf.CONTROL_N:
        raise GuardError("--ns: контрольный N = %d обязан идти первым (§13.24.5 п. 4), получено %s" % (rf.CONTROL_N, ns))
    return ns


def require_main_done(paths, frozen=FROZEN):
    """Основной прогон закончен (`run.rc`, `results/results.pkl`: оба процесса пишут в одни `out/<окно>/<vid>` — параллельно нельзя) и заморозка N = 20 существует (§13.24.5 п. 7).
    Возвращает (путь results.pkl, sha256 файла заморозки)."""
    rcf, pkl = paths.j("run.rc"), paths.j("results", "results.pkl")
    if not os.path.exists(rcf):
        raise GuardError("нет %s: основной прогон ещё идёт (или не запускался) — `sens` параллельно с ним запрещён (§13.24.5 п. 7)" % rcf)
    if not os.path.exists(pkl):
        raise GuardError("нет %s: основной прогон не дал результатов (run.rc = %s)" % (pkl, open(rcf, encoding="utf-8").read().strip()))
    if not os.path.exists(frozen):
        raise GuardError("нет %s: `sens` не запускается, пока заморозка основного прогона (N = 20) не закоммичена (§13.24.5 п. 7)" % frozen)
    return pkl, rf.sha256_file(frozen)


def totals(ser):
    S, N = rf.day_sums(ser)
    n, sm = int(N.sum()), float(S.sum())
    return {"n": n, "sum": sm, "per_trade": (sm / n if n else None)}


def sens_summary(ctx, res):
    """Сводка одного N (§13.24.5 п. 6): `summary()` основного прогона + на сторону: сделок / Σ $ / $/сделку итога и базы на проверке вперёд (105 сут.) и на полном окне (136 сут.),
    клеток всего / выбывших по (а) в обоих проходах полной процедуры."""
    out = summary(res, ctx.spec)
    for s in rf.SIDES:
        wf, full = res["wf"][s], res["outcomes"]["%s:full" % s]
        st = wf["stat"]
        fin = ctx.ev.get(rf.Req(s, rf.cfg_keys(ctx.spec, s, full.final), False))
        base = ctx.ev.get(rf.Req(s, rf.cfg_keys(ctx.spec, s, {}), False))
        rows = [r for step in list(full.steps1) + list(full.steps2) for r in step.rows]
        out[s].update({
            "fwd_totals": {"final": {"n": int(st.n_c), "sum": float(st.s_c), "per_trade": (float(st.s_c) / st.n_c if st.n_c else None)},
                           "base": {"n": int(st.n_r), "sum": float(st.s_r), "per_trade": (float(st.s_r) / st.n_r if st.n_r else None)}},
            "full_totals": {"final": totals(fin), "base": totals(base)},
            "cells": {"total": len(rows), "dropped_a": sum(1 for r in rows if not r["kept"])}})
    return out


def same_summary(a, b, tol=1e-12, path=""):
    """Различия двух jsonable-сводок: строки, булевы, None — равны; числа — в пределах `tol` относительно; NaN = NaN. Возвращает список путей с различием."""
    if isinstance(a, dict) and isinstance(b, dict):
        diff = []
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                diff.append("%s/%s: нет в одной из сводок" % (path, k))
            else:
                diff += same_summary(a[k], b[k], tol, "%s/%s" % (path, k))
        return diff
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return ["%s: длина %d ≠ %d" % (path, len(a), len(b))]
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in same_summary(x, y, tol, "%s[%d]" % (path, i))]
    num = (int, float)
    if isinstance(a, num) and isinstance(b, num) and not isinstance(a, bool) and not isinstance(b, bool):
        if a != a and b != b:
            return []
        return [] if abs(a - b) <= tol * max(1.0, abs(a), abs(b)) else ["%s: %r ≠ %r" % (path, a, b)]
    return [] if a == b else ["%s: %r ≠ %r" % (path, a, b)]


def sens_run(ctx, g5, ns, control=None, out_dir=None, gates=None, c595=None, meta=None):
    """§13.24.5: полная процедура при каждом N из `ns` (контрольный N = 20 — первым; обязан совпасть с `control` = результаты основного прогона и обойтись без запусков движка).
    После каждого N пишет `<out_dir>/sens.json` (и `sens-<N>.pkl`). Различие контроля — GuardError (после записи sens.json). Окна — только wa/wb/wc, даты ≤ 13.11 (п. 8)."""
    rf.check_sens_windows(rf.WINDOWS, ctx.ev.windows, ctx.engine.windows, ctx.store.windows)
    sens = {"meta": meta or {}, "ns": [], "min_trades": {}, "control": None, "by_n": {}}
    for n in ns:
        b0 = len(ctx.ev.batches)
        with rf.min_trades_override(n):
            res = run_all(ctx, g5, gates, c595)
        summ = jsonable(sens_summary(ctx, res))
        summ["engine_variants"] = int(sum(ctx.ev.batches[b0:]))
        sens["ns"].append(n)
        sens["min_trades"][str(n)] = list(rf.min_trades_for(n))
        sens["by_n"][str(n)] = summ
        bad = None
        if n == rf.CONTROL_N:
            if control is None:
                bad = "контроль N = 20: результатов основного прогона нет — сравнивать не с чем"
            else:
                ref = jsonable(summary(control, ctx.spec))
                diff = [d for s in rf.SIDES for d in same_summary(ref[s], {k: summ[s][k] for k in ref[s]}, path=s)]
                sens["control"] = {"equal": not diff, "diff": diff[:40], "engine_variants": summ["engine_variants"]}
                if diff:
                    bad = "контроль N = 20 не совпал с results.pkl основного прогона (%d различий, первое: %s)" % (len(diff), diff[0])
                elif summ["engine_variants"]:
                    bad = "контроль N = 20 запускал движок (%d вариантов): серии основного прогона не найдены в кэше" % summ["engine_variants"]
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, "sens-%d.pkl" % n), "wb") as f:
                pickle.dump(res, f)
            with open(os.path.join(out_dir, "sens.json"), "w", encoding="utf-8") as f:
                json.dump(sens, f, ensure_ascii=False, indent=1)
        if bad:
            raise GuardError(bad)
    return sens


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
    print("# пример вызова с фильтром признака (группа 6): --ftab <таблица потока окна>, один вызов на поток (§13.24.8)")
    w = rf.WINDOWS[0]
    print(show_cmd(*eng.command(w, paths.var + "/b00NN.txt", paths.out(w.name), paths.need_tab(w.name, rf.FtabNeed("L", rf.cfg_keys(spec, "L", {}))))))
    return 0


def cmd_gates(a):
    require_go()
    ctx = make_ctx(a.root, nboot=a.nboot, chunk=a.chunk, procs=a.procs, threads=a.threads, bin_path=a.bin, prod=a.prod, tick_store=getattr(a, "tick_store", ""), sig_cache=getattr(a, "sig_cache", ""))
    g = run_gates(ctx, with_g5=False)                                   # G1/G2/G3/G3b; G5 — отдельной командой после докачки метрик
    os.makedirs(ctx.paths.gate, exist_ok=True)
    json.dump(jsonable(g), open(os.path.join(ctx.paths.gate, "gates-g123.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(jsonable(g), ensure_ascii=False, indent=1))
    return 0


def run_gates(ctx, with_g5=True, reuse=None):
    """G1/G2 — всегда заново (хэши бинаря/монет/сеток/манифестов). `reuse` — `gate/gates.json` первого запуска (перезапуск `run --reuse-gates`): G3 и G5 берутся оттуда (данные и бинарь движка те же,
    иначе G1/G2 не пройдут), G3 заново не считается."""
    g = {"G12": gate12(ctx.paths, ctx.engine.bin)}
    if g["G12"]["fail"]:
        raise GuardError("ворота G1/G2 не пройдены: %s" % g["G12"]["fail"])
    if reuse is not None:
        if not reuse.get("G3") or not reuse.get("G5"):
            raise GuardError("gates.json без G3/G5 — `--reuse-gates` невозможен, запустите `run` без флага")
        g["G3"], g["G5"] = reuse["G3"], reuse["G5"]
        bad = g3_failures(g["G3"])
        if bad:
            raise GuardError("G3 в gates.json не чист: %s" % bad)
        return g
    g["G3"] = gate3(ctx.paths, ctx.engine, ctx.spec, ctx.engine_conv)
    bad = g3_failures(g["G3"])
    if bad:
        raise GuardError("G3 «окна без потерь» / сходимость предыстории: различия %s" % bad)
    if with_g5:
        g["G5"] = gate5(ctx.store, ctx.spec)
    return g


def cmd_gate5(a):
    require_go()
    ctx = make_ctx(a.root, nboot=a.nboot, chunk=a.chunk, procs=a.procs, threads=a.threads, bin_path=a.bin, prod=a.prod, tick_store=getattr(a, "tick_store", ""), sig_cache=getattr(a, "sig_cache", ""))
    g5 = gate5(ctx.store, ctx.spec)
    os.makedirs(ctx.paths.gate, exist_ok=True)
    json.dump(g5, open(os.path.join(ctx.paths.gate, "g5.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for s, d in g5.items():
        for fa, v in d.items():
            print("%s %-14s %-3s %s %s" % (s, fa, v["fo"], "титруется" if v["ok"] else "СНЯТ", ["%.3f" % x for x in v["shares"]]))
    return 0


def cmd_run(a):
    require_go()
    ctx = make_ctx(a.root, nboot=a.nboot, chunk=a.chunk, procs=a.procs, threads=a.threads, bin_path=a.bin, prod=a.prod, tick_store=getattr(a, "tick_store", ""), sig_cache=getattr(a, "sig_cache", ""))
    gp = os.path.join(ctx.paths.gate, "gates.json")
    if a.reuse_gates:                                                    # перезапуск: ворота первого запуска остаются в силе, файл не переписывается
        with open(gp, encoding="utf-8") as f:
            gates = run_gates(ctx, reuse=json.load(f))
        print("перезапуск: G3/G5 из %s; STOP-файл %s" % (gp, ctx.paths.stop))
    else:
        gates = run_gates(ctx)
        os.makedirs(ctx.paths.gate, exist_ok=True)
        json.dump(jsonable(gates), open(gp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    res = run_all(ctx, gates["G5"], gates)
    out = ctx.paths.j("results")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "results.pkl"), "wb") as f:
        pickle.dump(res, f)
    json.dump(jsonable(summary(res, ctx.spec)), open(os.path.join(out, "results.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(jsonable(summary(res, ctx.spec)), ensure_ascii=False, indent=1))
    return 0


def cmd_sens(a):
    require_go()
    ns = parse_ns(a.ns)
    rf.check_sens_windows(rf.WINDOWS)
    ctx = make_ctx(a.root, nboot=a.nboot, chunk=a.chunk, procs=a.procs, threads=a.threads, bin_path=a.bin, prod=a.prod, prefix="s", tick_store=getattr(a, "tick_store", ""), sig_cache=getattr(a, "sig_cache", ""))
    pkl, frozen_sha = require_main_done(ctx.paths)
    with open(os.path.join(ctx.paths.gate, "gates.json"), encoding="utf-8") as f:
        gates = json.load(f)
    with open(pkl, "rb") as f:
        control = pickle.load(f)
    sens = sens_run(ctx, gates["G5"], ns, control, ctx.paths.j("results"), gates, meta={"frozen_sha256": frozen_sha, "ns": ns, "nboot": ctx.nboot})
    print(json.dumps(sens, ensure_ascii=False, indent=1))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan", "gates", "gate5", "run", "sens"])
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--bin", default="")
    ap.add_argument("--prod", default=PROD)
    ap.add_argument("--threads", type=int, default=THREADS)
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--tick-store", default=os.environ.get("P42_TICK_STORE", ""), help="TK-607: каталог склада суток сделок движка (пусто — без склада); по умолчанию из окружения P42_TICK_STORE")
    ap.add_argument("--sig-cache", default=os.environ.get("P42_SIG_CACHE", ""), help="TK-607: каталог кэша сигналов движка (пусто — без кэша); по умолчанию из окружения P42_SIG_CACHE")
    ap.add_argument("--chunk", type=int, default=0, help="вариантов в одном вызове движка (0 — все недостающие одним пакетом)")
    ap.add_argument("--nboot", type=int, default=rf.NBOOT)
    ap.add_argument("--ns", default="20,10,15,25", help="`sens`: пороги N (контрольный 20 — первым)")
    ap.add_argument("--reuse-gates", action="store_true", help="`run`: перезапуск — G3/G5 из gate/gates.json первого запуска, G1/G2 заново")
    a = ap.parse_args(argv)
    try:
        return {"plan": cmd_plan, "gates": cmd_gates, "gate5": cmd_gate5, "run": cmd_run, "sens": cmd_sens}[a.cmd](a)
    except GuardError as e:
        print("ОСТАНОВКА (охрана): %s" % e, file=sys.stderr)
        return 3
    except rf.StopRequested as e:
        print("ОСТАНОВ по файлу STOP на границе раунда: %s; посчитанное лежит в npz/out/ftab, продолжение — `run --reuse-gates`" % e, file=sys.stderr)
        return 75


if __name__ == "__main__":
    sys.exit(main())
