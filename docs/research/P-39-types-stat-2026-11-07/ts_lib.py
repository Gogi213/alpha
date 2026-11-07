#!/usr/bin/env python3
"""TK-493, статистическое обоснование пункта 1 (шум / структурный разрыв / разовое давление), 07.11.2026.

ПОИСК/ОПИСАНИЕ, не подтверждение: читает все 35 суток (2465..2499) открыто, post hoc. Окно читается НЕ через conv_guard/conv_window (токен выдаёт только CEO и он сгорел), а своим
читателем .sap без защиты: только чтение, ничего в ~/xdl9/panels, ~/xdl9/c2/window*, ~/xdl9/c2/out*, ~/xdl9/c2g/out* не пишется. Журнал чтений - <out>/types_reads.log.

Весь счёт - существующие модули conv/*: conv_world.World (подкласс с читателями без защиты), conv_world.series_cell / load_series (ряды s, b, sigma, r6, общий фактор c),
conv_lib.PairSim / Feats / simulate_component (сделки), conv_calib.EpisodeSim (эпизоды §5.2), conv_hints.run_lengths (эпизоды-серии отчёта подсказок).
"""
import gzip
import json
import math
import os
import sys
import time

import numpy as np

MIN_DAY = 1440
GRID0_MS = 1_577_836_800_000
D0, D_F1, D_W0, D_W1 = 2465, 2478, 2479, 2499     # разогрев/формирование/окно
CODE_DIR = os.environ.get("TS_CODE", os.path.expanduser("~/xdl9/c2t/code/conv"))
sys.path.insert(0, CODE_DIR)
import conv_calib as CAL      # noqa: E402
import conv_guard as G        # noqa: E402  (только read_sap_header; защита окна НЕ используется - см. шапку)
import conv_grid as GR        # noqa: E402
import conv_hints as H        # noqa: E402
import conv_lib as L          # noqa: E402
import conv_run as CR         # noqa: E402
import conv_world as W        # noqa: E402

HOME = os.path.expanduser("~/xdl9")
OUT = os.path.expanduser("~/xdl9/c2t")
RAW_ROOT = os.path.expanduser("~/xdl/out")
CFG_OFFICIAL = dict(L=1440, step=5, nb=240, ls=60, nbs=20, dedup=True, delay=1, entry_mode="defer", exit_mode="ffill", ceiling=True, use_q6=True)
EXCH = ["aster", "binance", "bingx", "bitget", "bybit", "kraken", "lighter", "okx"]
EXI = {e: i for i, e in enumerate(EXCH)}
BASE_COMBOS = {"B1": L.Combo(25.2, 3, 1, 1440, 2.0), "B3": L.Combo(25.2, 3, 3, 1440, 2.0)}       # базовые правила анализа (θ_cost, k=3, T=1440, S0=2·g_in; без потолка и Q6)
V_COMBOS = {"V1": L.Combo(40.0, 3, 3, 1440, None), "V2": L.Combo(40.0, 3, 3, 120, None)}          # объявленные варианты П-38 r3 (с потолком, Q6; V1 - с Q5)
CP_PATH = os.path.join(HOME, "c2g/ng/out/calib/classpar_full.json")                               # sha256 5fe8ea73… (= classpar_full_nogate*.json репозитория)
Q5_PATH = os.path.join(HOME, "c2g/ng/out/calib/q5_full.json")                                     # sha256 6b756002…


def log_read(msg):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "types_reads.log"), "a", encoding="utf-8") as f:
        f.write("%s\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg))


# ------------------------------------------------------------------ читатели без защиты (только чтение)
def load_sap(path, d0, d1):
    """open/close на минутах [d0*1440, (d1+1)*1440): байт в байт как conv_guard.load_panel_range, но без защиты окна."""
    m0, m1 = d0 * MIN_DAY, (d1 + 1) * MIN_DAY
    nf = m1 - m0
    first, n = G.read_sap_header(path)
    lo, hi = max(m0, first), min(m1, first + n)
    o = np.full(nf, np.nan)
    c = np.full(nf, np.nan)
    if hi > lo:
        cnt = hi - lo
        assert hi <= m1 and cnt <= nf
        o[lo - m0:hi - m0] = np.fromfile(path, dtype="<f8", count=cnt, offset=24 + (lo - first) * 8)
        c[lo - m0:hi - m0] = np.fromfile(path, dtype="<f8", count=cnt, offset=24 + n * 8 + (lo - first) * 8)
    return o, c


def load_turnover(path, d0, d1):
    """Оборот по минутам из сырых свечей csv.gz (quote_v; пусто -> c*v); как conv_guard.load_turnover_raw без защиты."""
    m0, m1 = d0 * MIN_DAY, (d1 + 1) * MIN_DAY
    out = np.zeros(m1 - m0, dtype=np.float64)
    t_end_ms = GRID0_MS + m1 * 60_000
    t_beg_ms = GRID0_MS + m0 * 60_000
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        next(f, None)
        for line in f:
            p = line.strip().split(",")
            if len(p) < 6:
                continue
            t = int(p[0])
            if t >= t_end_ms:
                break
            if t < t_beg_ms:
                continue
            m = (t - GRID0_MS) // 60_000 - m0
            c, v = float(p[4]), float(p[5])
            q = p[6] if len(p) > 6 else ""
            out[m] = float(q) if q != "" else c * v
    return out


def load_funding(path, d0, d1):
    m0, m1 = d0 * MIN_DAY, (d1 + 1) * MIN_DAY
    if not os.path.isfile(path):
        return np.zeros(0, np.int64), np.zeros(0)
    ts, rs = [], []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        next(f, None)
        for line in f:
            p = line.split(",")
            if len(p) < 2:
                continue
            m = (int(p[0]) - GRID0_MS) // 60_000
            if m >= m1:
                break
            if m >= m0:
                ts.append(m)
                rs.append(float(p[1]))
    return np.asarray(ts, np.int64), np.asarray(rs, np.float64)


class OpenWorld(W.World):
    """World без защиты окна: те же пути, тот же универсум, те же ряды; переопределены только три читателя."""

    def load_pair_raw(self, p, d0, d1):
        oA, cA = load_sap(self.panel_path(p, 0), d0, d1)
        oB, cB = load_sap(self.panel_path(p, 1), d0, d1)
        return oA, cA, oB, cB, None            # крипта: 24/7, сессий нет

    def load_turn(self, p, side, d0, d1):
        ex, sym = p["legs"][side]
        fp = self.turn_path(ex, sym)
        if not os.path.isfile(fp):
            return None
        a = np.load(fp, mmap_mode="r")
        n = (d1 - d0 + 1) * MIN_DAY
        assert len(a) >= n, "боковая панель оборота короче запроса"
        return np.asarray(a[:n], dtype=np.float64)

    def fund_array(self, p, side, d0, d1):
        ex, sym = p["legs"][side]
        ts, rs = load_funding(self.fund_path(ex, sym), d0, d1)
        a = np.zeros((d1 - d0 + 1) * MIN_DAY)
        for m, r in zip(ts, rs):
            a[m - d0 * MIN_DAY] += r
        return a


def make_world(turn_dir=None):
    return OpenWorld(HOME, test=False, turn_dir=turn_dir or os.path.join(OUT, "turn"), gate=False)


def crypto_pairs(world):
    return [p for p in world.universe(check_sha=True) if world.in_main_series(p) and p["cls"] == "crypto"]


def build_turn(world, pairs, d0, d1, outdir):
    """Боковые панели оборота ног пар (float32, 35 суток), как W.build_turnover, но читателем без защиты."""
    seen = set()
    n = 0
    for p in pairs:
        for ex, sym in p["legs"]:
            if (ex, sym) in seen:
                continue
            seen.add((ex, sym))
            src = os.path.join(RAW_ROOT, ex, "candles", W.safe_name(sym) + ".csv.gz")
            dst = os.path.join(outdir, ex, W.safe_name(sym) + ".npy")
            if not os.path.isfile(src) or os.path.isfile(dst):
                continue
            a = load_turnover(src, d0, d1)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            np.save(dst, a.astype(np.float32))
            n += 1
    return n
