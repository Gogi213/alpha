#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TK-025: независимый пересчёт колонок R1 из сырого бинлога суток и сверка с кэшем подходов (КАДР ВЗВОДА, ВСЕ подходы).

    python3 tk025-recompute.py <SYM-день.binlog> <approaches-SYM.csv> [--approach-bps 20] [--json out.json]
                               [--sample K] [--side bid|ask] [--day YYYY-MM-DD] [--vpin-alt] [--h3-floor-lots N]
    python3 tk025-recompute.py --undef-share <approaches.csv> [<approaches2.csv> ...]     (без бинлога)
    python3 tk025-recompute.py --selftest

Момент. t0 строки кэша = `arm_ms` (кадр ВЗВОДА), у ВСЕХ подходов независимо от причины конца (touch / price_left /
level_death) — строки не фильтруются по `touch_start_ms`; сверяются и пустые клетки (пусто = «не определено» по
определению: прогрев, знаменатель ноль, события не было). Кадр взвода = ПОЛНЫЙ кадр монеты: книга после обеих половин
(бид и аск) кадра с `ms == arm_ms`; колонки считаются в конце кадра. Модели «два прохода» / отставания аск-половины на
кадр (Н1) здесь нет. Если кадров с одним `ms` несколько, берётся первый, где совпали лучшая цена своей стороны и размер
стены с `best_own_tick` / `size_at_arm` строки; нет совпадения — последний кадр с этим `ms` (флаг `fallback`); нет кадра
с таким `ms` — последний кадр до него (флаг `no-frame`). В расчёт идёт только то, что случилось не позже кадра взвода
(поток читается по порядку, снимок берётся на кадре; будущего в состоянии нет).

Что делает. Читает бинлог v3 одних суток монеты (кадры zstd — ctypes/libzstd.so.1, запасной путь — внешний `zstd -d -c`),
восстанавливает книгу и ленту сделок тем же порядком, что `FileReplayer`/`replay_symbol` (группа записей с одной
`exch_ts_ns` = один кадр книги; сделка закрывает открытую группу и идёт после неё) и пересчитывает колонки R1 ТОЛЬКО по
определениям из `docs/findings/tk025-design-2026-10-02.md` (Rust-код колонок не читался). Сверка поколоночно, отдельно
по сторонам стены (бид/аск — зеркало): строк, совпало, не совпало, пусто/пусто, пусто/значение (обе стороны).

Колонки состояния трекера уровней (идентичность стены — (сторона, price_tick, birth_ms) из строки кэша): wall_add/front_add
(рост размера на цене стены / между лучшей ценой и стеной от рождения до взвода), best_move (лучшая цена от рождения),
frontrun_levels / frontrun_delta_10s_lots (книга прошлых кадров — второй проход по бинлогу, запросы известны после
первого; это НЕ модель Н1), since_far, cancel — восстанавливаются из бинлога. Приближённо (трекер не восстановим
полностью, расхождения ожидаемы и печатаются): born_shift (соседняя цена умерла в кадре рождения, размеры в 2×),
prev_death (смерть = обнуление цены у предыдущего уровня кэша на той же цене, исход 70/20 по сделкам кадра смерти),
opp_wall (пол H3 = минимальный `size_at_arm` кэша или `--h3-floor-lots`). Где определение неоднозначно, считаются
варианты под именами `alt:колонка@вариант` — «один вариант совпал, другой нет» указывает на расхождение определения.

Только стандартная библиотека (на деке/VPS нет numpy). Время — только из данных бинлога.
"""
import argparse
import bisect
import csv
import ctypes
import ctypes.util
import heapq
import json
import subprocess
import sys
import time
from array import array
from collections import deque, Counter

EV_LOCAL = 1 << 30
EV_BUY = 1 << 29
EV_SELL = 1 << 28
EV_BID_DEPTH = 1 | EV_BUY | EV_LOCAL
EV_ASK_DEPTH = 1 | EV_SELL | EV_LOCAL
EV_BID_SNAP = 4 | EV_BUY | EV_LOCAL
EV_ASK_SNAP = 4 | EV_SELL | EV_LOCAL
EV_BUY_TRADE = 2 | EV_LOCAL | EV_BUY
EV_SELL_TRADE = 2 | EV_LOCAL | EV_SELL

FLOW_WINDOW_MIN = 60  # окно «оборот за час» (flow_1h_lots) в минутах
VPIN_BARS = 50
# Трактовка vpin, совпавшая с кэшем в прошлом гейте: размер бара — по обороту за час С учётом текущей сделки (после добавления
# её в минутное кольцо), RPI-сделки входят, бары строятся только после прогрева 60 мин. Остальные 11 комбинаций — `--vpin-alt`.
VPIN_MAIN = "trade_in/rpi/warm"
BASE_AGE_MS = 2_700_000  # «подходы базы»: возраст стены (arm_ms − birth_ms) ≥ 2700 с, сторона bid
RING_DEATHS = 256  # кольцо смертей монеты (prev_death)

# 61 колонка R1 по группам договора Судьи (C) — порядок и состав фактически берутся из шапки кэша; список — для отчёта о покрытии
R1_GROUPS = [
    ("tape", [f"tape_{k}_{u}_{w}s" for w in (15, 30, 60) for k in ("press", "with", "all") for u in ("lots", "n")]
     + ["tape_press_60m_lots", "tape_with_60m_lots"]),
    ("burst/avg", ["tape_burst_press_15s_bp", "tape_burst_with_15s_bp", "tape_burst_press_30s_bp", "tape_burst_with_30s_bp",
                   "tape_press_avg_30s_e2", "tape_with_avg_30s_e2"]),
    ("sign_ac", ["sign_ac_15s_bp", "sign_ac_60s_bp"]),
    ("vpin", ["vpin_bp"]),
    ("trade_size", ["trade_size_p50_15m", "trade_size_p90_15m"]),
    ("obi", ["obi1_bp", "obi5_bp", "obi10_bp", "obi50_bp", "micro_off_cbps"]),
    ("ofi", ["ofi_10s_lots", "ofi_60s_lots"]),
    ("best_flips", ["best_flips_15s", "best_flips_60s"]),
    ("cancel", ["cancel_1s_lots", "cancel_3s_lots", "cancel_60m_lots", "cancel_life_lots"]),
    ("wall_add", ["wall_add_max_lots", "wall_add_max_age_ms", "front_add_max_lots", "front_add_max_age_ms"]),
    ("born_shift", ["born_shift_cbps", "born_shift_lots"]),
    ("prev_death", ["prev_death_gap_ms", "prev_death_outcome"]),
    ("stack_shape", ["size_share15_bp", "nz_levels15"]),
    ("best_move", ["best_move_1s_cbps", "best_move_10s_cbps"]),
    ("opp_wall", ["opp_wall_dist_cbps", "opp_wall_ratio_bp"]),
    ("frontrun", ["frontrun_delta_10s_lots", "frontrun_levels"]),
    ("since_far", ["since_far_ms"]),
]
R1_NAMES = [c for _, cols in R1_GROUPS for c in cols]
GROUP_OF = {c: g for g, cols in R1_GROUPS for c in cols}
CALIB_COLS = ["frontrun_lots_at_arm"]  # существующая колонка кэша: калибровка правила «кадр за 1–2 с до t0» и суммы лотов впереди


def tdiv(a, b):
    """Целочисленное деление с усечением к нулю (b > 0)."""
    q = abs(a) // b
    return q if a >= 0 else -q


# --------------------------------------------------------------------------- чтение бинлога


class Zstd:
    def __init__(self):
        self.lib = None
        try:
            name = ctypes.util.find_library("zstd") or "libzstd.so.1"
            lib = ctypes.CDLL(name)
            lib.ZSTD_getFrameContentSize.restype = ctypes.c_ulonglong
            lib.ZSTD_getFrameContentSize.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
            lib.ZSTD_decompress.restype = ctypes.c_size_t
            lib.ZSTD_decompress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t]
            lib.ZSTD_isError.restype = ctypes.c_uint
            lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
            self.lib = lib
        except OSError:
            self.lib = None

    def decompress(self, raw):
        lib = self.lib
        if lib is not None:
            n = lib.ZSTD_getFrameContentSize(raw, len(raw))
            if n < (1 << 40):
                buf = ctypes.create_string_buffer(n)
                r = lib.ZSTD_decompress(buf, n, raw, len(raw))
                if not lib.ZSTD_isError(r):
                    return ctypes.string_at(buf, r)
        return subprocess.run(["zstd", "-d", "-c"], input=raw, capture_output=True, check=True).stdout


def decode_frame(pl):
    """Тело кадра v3 -> список (ev, exch_ts_ns, price_ticks, qty_lots, attrs). Формат — src/binlog/mod.rs."""
    epoch = int.from_bytes(pl[:8], "little", signed=True)
    p = 8
    n = len(pl)
    px = 0
    q = 0
    out = []
    ap = out.append
    while p < n:
        b = pl[p]
        p += 1
        if b < 128:
            ev = b
        else:
            ev = b & 127
            s = 7
            while True:
                b = pl[p]
                p += 1
                ev |= (b & 127) << s
                if b < 128:
                    break
                s += 7
        # exch_dt (zigzag)
        b = pl[p]
        p += 1
        if b < 128:
            v = b
        else:
            v = b & 127
            s = 7
            while True:
                b = pl[p]
                p += 1
                v |= (b & 127) << s
                if b < 128:
                    break
                s += 7
        ts = epoch + ((v >> 1) ^ -(v & 1))
        # local_dt (zigzag) — не нужен, но читается
        b = pl[p]
        p += 1
        if b >= 128:
            while True:
                b = pl[p]
                p += 1
                if b < 128:
                    break
        # attrs (uvarint)
        b = pl[p]
        p += 1
        if b < 128:
            attrs = b
        else:
            attrs = b & 127
            s = 7
            while True:
                b = pl[p]
                p += 1
                attrs |= (b & 127) << s
                if b < 128:
                    break
                s += 7
        # count (uvarint)
        b = pl[p]
        p += 1
        if b < 128:
            cnt = b
        else:
            cnt = b & 127
            s = 7
            while True:
                b = pl[p]
                p += 1
                cnt |= (b & 127) << s
                if b < 128:
                    break
                s += 7
        for _ in range(cnt):
            b = pl[p]
            p += 1
            if b < 128:
                v = b
            else:
                v = b & 127
                s = 7
                while True:
                    b = pl[p]
                    p += 1
                    v |= (b & 127) << s
                    if b < 128:
                        break
                    s += 7
            px += (v >> 1) ^ -(v & 1)
            b = pl[p]
            p += 1
            if b < 128:
                v = b
            else:
                v = b & 127
                s = 7
                while True:
                    b = pl[p]
                    p += 1
                    v |= (b & 127) << s
                    if b < 128:
                        break
                    s += 7
            q += (v >> 1) ^ -(v & 1)
            ap((ev, ts, px, q, attrs))
    return out


def iter_frames(path):
    data = open(path, "rb").read()
    if data[:4] != b"ABLG":
        raise SystemExit("не бинлог ABLG (контейнер .zst не поддержан)")
    if data[4] != 3:
        raise SystemExit(f"версия бинлога {data[4]} (поддержана только v3)")
    z = Zstd()
    p = 25
    n = len(data)
    while p + 4 <= n:
        ln = int.from_bytes(data[p:p + 4], "little")
        p += 4
        if p + ln > n:
            break
        yield decode_frame(z.decompress(data[p:p + ln]))
        p += ln


# --------------------------------------------------------------------------- моделирование


def mv_cbps(sg, b0, b1, base):
    """Ход цены (b1 − b0)·sg в cbps от базы (1 cbps = 1 ppm), усечение к нулю."""
    return tdiv((b1 - b0) * sg * 1_000_000, base) if base else None


def parse_cell(x):
    if x is None or x == "":
        return None
    try:
        return int(x)
    except ValueError:
        return int(float(x))


class Level:
    """Уровень-стена (сторона, price_tick, birth_ms) — общий у строк-подходов с одной идентичностью."""
    __slots__ = ("s", "price", "birth", "rows_left", "dead", "death_ms", "death_before", "death_traded", "wa", "wa1", "wa2",
                 "fa", "fa1", "fa2", "fi", "fi1", "size_max", "traded", "bs", "prev", "starts")

    def __init__(self, s, price, birth):
        self.s = s
        self.price = price
        self.birth = birth
        self.rows_left = 0
        self.dead = False
        self.death_ms = None
        self.death_before = 0
        self.death_traded = 0
        self.wa = self.wa1 = self.wa2 = 0  # max прирост на цене стены, момент первого / последнего такого
        self.fa = self.fa1 = self.fa2 = 0  # то же для цен строго между лучшей и стеной
        self.fi = self.fi1 = 0  # то же, лучшая цена включена (вариант)
        self.size_max = 0
        self.traded = 0
        self.bs = None  # born_shift: (cbps от старой цены, cbps от новой, лоты)
        self.prev = None
        self.starts = []  # индексы кадров — начала секундных слотов от рождения (frontrun_before)


class Row:
    __slots__ = ("line", "side", "s", "price", "t0", "birth", "cells", "size_at_arm", "best_own", "best_opp", "fr_arm", "lvl",
                 "vals", "flag", "opp_ok", "frres", "d10_ms", "fb_ms")

    def __init__(self, line, side, price, t0, birth, cells, size_at_arm=None, best_own=None, best_opp=None, fr_arm=None):
        self.line = line
        self.side = side
        self.s = 0 if side == "bid" else 1
        self.price = price
        self.t0 = t0
        self.birth = birth
        self.cells = cells
        self.size_at_arm = size_at_arm
        self.best_own = best_own
        self.best_opp = best_opp
        self.fr_arm = fr_arm
        self.lvl = None
        self.vals = None
        self.flag = None
        self.opp_ok = None
        self.frres = {}
        self.d10_ms = None
        self.fb_ms = None


def build_levels(rows):
    """Уровни по идентичности строк; prev — предыдущий (по рождению) уровень на той же цене."""
    lv = {}
    for r in rows:
        k = (r.s, r.price, r.birth)
        L = lv.get(k)
        if L is None:
            L = lv[k] = Level(r.s, r.price, r.birth)
        r.lvl = L
        L.rows_left += 1
    lst = sorted(lv.values(), key=lambda L: (L.birth, L.s, L.price))
    last = {}
    for L in lst:
        L.prev = last.get((L.s, L.price))
        last[(L.s, L.price)] = L
    return lst


class VpinMachine:
    """Объёмные бары VPIN: размер бара = max(1, flow_1h/50) на открытии; сделка делится между барами."""

    def __init__(self):
        self.bars = deque(maxlen=VPIN_BARS)
        self.size = 0
        self.filled = 0
        self.buy = 0
        self.sell = 0

    def add(self, lots, buy, flow_fn):
        rem = lots
        while rem > 0:
            if self.size == 0:
                self.size = max(1, flow_fn() // VPIN_BARS)
                self.filled = self.buy = self.sell = 0
            take = min(rem, self.size - self.filled)
            self.filled += take
            if buy:
                self.buy += take
            else:
                self.sell += take
            rem -= take
            if self.filled == self.size:
                self.bars.append((abs(self.buy - self.sell), self.size))
                self.size = 0

    def value(self):
        if len(self.bars) < VPIN_BARS:
            return None
        tot = sum(b[1] for b in self.bars)
        if tot <= 0:
            return None
        return tdiv(sum(b[0] for b in self.bars) * 10000, tot)


class BookStream:
    """Кадры книги: группа записей с одной exch_ts_ns = кадр; бид- и аск-половины применяются ВМЕСТЕ (кадр монеты целиком)."""

    def __init__(self):
        self.book = [{}, {}]  # 0 бид, 1 аск: тик -> лоты
        self.best = [None, None]
        self.synced = False
        self.error = None
        self.start_ms = None
        self.grp = []
        self.grp_snap = False
        self.grp_ts = 0
        self.has_open = False
        self.n_frames = 0
        self.nonmono_frames = 0
        self.last_frame_ms = None
        self.frame_ms = array("q")
        self.fbb = array("q")  # лучшая цена бид/аск на конце кадра (−1 — нет)
        self.fba = array("q")
        self.watched = frozenset()
        self.want_chg = False

    # хуки
    def trade(self, ts_ns, px, lots, buy, block, rpi):
        pass

    def pre_frame(self, ms):
        pass

    def post_frame(self, ms, snap, pbest, bef, chg, incs):
        pass

    def record(self, ev, ts, px, qty, attrs):
        if ev == EV_BUY_TRADE or ev == EV_SELL_TRADE:
            if self.has_open and not self.flush():
                return False
            self.trade(ts, px, qty, ev == EV_BUY_TRADE, attrs & 1, attrs & 2)
            return True
        if ev == EV_BID_DEPTH:
            s, snap = 0, False
        elif ev == EV_ASK_DEPTH:
            s, snap = 1, False
        elif ev == EV_BID_SNAP:
            s, snap = 0, True
        elif ev == EV_ASK_SNAP:
            s, snap = 1, True
        else:
            return True
        if self.has_open and (snap != self.grp_snap or ts != self.grp_ts):
            if not self.flush():
                return False
        if not self.has_open:
            self.grp_snap = snap
            self.grp_ts = ts
            self.has_open = True
        self.grp.append((s, px, qty))
        return True

    def flush(self):
        snap = self.grp_snap
        ms = self.grp_ts // 1_000_000
        if snap:
            self.synced = True
        elif not self.synced:
            self.error = "дельта до снапшота"
            return False
        recs = ([], [])
        for s, px, q in self.grp:
            recs[s].append((px, q))
        self.grp = []
        self.has_open = False
        self.pre_frame(ms)
        book = self.book
        best = self.best
        watched = self.watched
        want = self.want_chg
        pbest = (best[0], best[1])
        self.n_frames += 1
        if self.start_ms is None:
            self.start_ms = ms
        if self.last_frame_ms is not None and ms < self.last_frame_ms:
            self.nonmono_frames += 1
        self.last_frame_ms = ms
        bef = {}
        chg = ({}, {}) if want else None
        incs = ([], []) if want else None
        for s in (0, 1):
            d = book[s]
            if snap:
                for k in watched:
                    if k[0] == s:
                        bef[k] = d.get(k[1], 0)
                d.clear()
                best[s] = None
            need = False
            for px, q in recs[s]:
                if not snap:
                    old = d.get(px, 0)
                    k = (s, px)
                    if k in watched and k not in bef:
                        bef[k] = old
                    if want:
                        if px not in chg[s]:
                            chg[s][px] = old
                        if q > old:
                            incs[s].append((px, q - old))
                if q == 0:
                    if px in d:
                        del d[px]
                        if px == best[s]:
                            need = True
                else:
                    d[px] = q
                    if not need:
                        b = best[s]
                        if b is None or (px > b if s == 0 else px < b):
                            best[s] = px
            if need:
                best[s] = (max(d) if s == 0 else min(d)) if d else None
        self.frame_ms.append(ms)
        self.fbb.append(best[0] if best[0] is not None else -1)
        self.fba.append(best[1] if best[1] is not None else -1)
        bb, ba = best
        if bb is not None and ba is not None and bb >= ba:
            self.error = f"книга пересеклась на {ms}"
            return False
        self.post_frame(ms, snap, pbest, bef, chg, incs)
        return True


class Pass2(BookStream):
    """Второй проход: книга на заданных кадрах прошлого (frontrun_*): {индекс кадра: [(строка, метка)]}."""

    def __init__(self, req):
        super().__init__()
        self.req = req

    def post_frame(self, ms, snap, pbest, bef, chg, incs):
        r = self.req.get(self.n_frames - 1)
        if not r:
            return
        for row, tag in r:
            s = row.s
            tick = row.price
            n = 0
            lots = 0
            for p, q in self.book[s].items():
                if (p > tick) if s == 0 else (p < tick):
                    n += 1
                    lots += q
            row.frres[tag] = (n, lots)


class Sim(BookStream):
    def __init__(self, rows, approach_bps, vpin_alt=False, h3_floor=None):
        super().__init__()
        self.D = approach_bps
        self.h3_floor = h3_floor
        self.rows = sorted(rows, key=lambda r: r.t0)
        self.pi = 0
        self.lvs = build_levels(self.rows)
        self.lp = 0
        self.key_levels = {}
        self.alive = [[], []]
        self.fa_floor = [0, 0]
        self.fa_dirty = [False, False]
        self.dead_ms = []
        self.watched = frozenset((r.s, r.price) for r in self.rows)
        self.want_chg = True
        self.fb = []  # строки, у которых на кадре с этим ms не совпали лучшая цена/размер (кандидаты в fallback)
        self.fb_ms = None
        self.req = {}
        self.flags = Counter()
        self.n_birth_missed = 0
        # лента сделок (не-блочные, лоты > 0, RPI входит — как flow_ring)
        self.t_ms = array("q")
        self.cb_lots = array("q", [0])
        self.cs_lots = array("q", [0])
        self.cb_n = array("q", [0])
        self.cs_n = array("q", [0])
        self.cum_pp = array("q", [0])
        self.cum_pn = array("q", [0])
        self.kb = array("b")
        self.prev_sign = 0
        self.mvol = Counter()
        self.nonmono_trades = 0
        self.n_rpi = 0
        self.n_block = 0
        self.frame_N = 0  # число сделок, пришедших до последнего кадра
        # VPIN: основная трактовка; все 12 — по флагу
        names = [VPIN_MAIN]
        if vpin_alt:
            names = [f"{a}/{r}/{st}" for a in ("trade_out", "trade_in", "frame") for r in ("rpi", "norpi") for st in ("all", "warm")]
        self.vp = [(n, VpinMachine()) + tuple(n.split("/")) for n in names]
        self.last_trade_ms = None
        self.prev = None
        self.ofi_ms = array("q")
        self.ofi_cum = array("q", [0])
        self.flips = [array("q"), array("q")]
        self.mx_n, self.mx_ms = [], []  # since_far: стек по лучшему аску (минус значения) — для бид-стен
        self.mn_v, self.mn_ms = [], []  # по лучшему биду — для аск-стен
        self.tr_all = {}  # сделки на ценах сторожимых уровней с прошлого кадра: (сторона, тик) -> лоты
        self.lvev = {}  # (сторона, тик) -> ([мс], [накопленные отмены, с нулём впереди])

    # ---- сделка
    def trade(self, ts_ns, px, lots, buy, block, rpi):
        if block:
            self.n_block += 1
            return
        if lots <= 0:
            return
        if rpi:
            self.n_rpi += 1
        ms = ts_ns // 1_000_000
        m = ms // 60000
        if self.t_ms and ms < self.t_ms[-1]:
            self.nonmono_trades += 1
        key = (1 if buy else 0, px)
        if key in self.watched:
            self.tr_all[key] = self.tr_all.get(key, 0) + lots
            for lvl in self.key_levels.get(key, ()):
                if not lvl.dead:
                    lvl.traded += lots
        sgn = 1 if buy else -1
        self.t_ms.append(ms)
        self.cb_lots.append(self.cb_lots[-1] + (lots if buy else 0))
        self.cs_lots.append(self.cs_lots[-1] + (0 if buy else lots))
        self.cb_n.append(self.cb_n[-1] + (1 if buy else 0))
        self.cs_n.append(self.cs_n[-1] + (0 if buy else 1))
        if self.prev_sign:
            self.cum_pp.append(self.cum_pp[-1] + sgn * self.prev_sign)
            self.cum_pn.append(self.cum_pn[-1] + 1)
        else:
            self.cum_pp.append(self.cum_pp[-1])
            self.cum_pn.append(self.cum_pn[-1])
        self.prev_sign = sgn
        self.kb.append(lots.bit_length() - 1)
        self._vpin(ms, m, lots, buy, rpi)

    def _flow_ring(self, m):
        mv = self.mvol
        return sum(mv.get(x, 0) for x in range(m - FLOW_WINDOW_MIN + 1, m + 1))

    def _flow_at_frame(self):
        """flow_1h_lots на последнем кадре: сделки только до кадра, минуты от мс кадра."""
        mf = self.last_frame_ms // 60000
        n = self.frame_N
        idx = bisect.bisect_left(self.t_ms, (mf - FLOW_WINDOW_MIN + 1) * 60000, 0, n)
        return (self.cb_lots[n] + self.cs_lots[n]) - (self.cb_lots[idx] + self.cs_lots[idx])

    def _vpin(self, ms, m, lots, buy, rpi):
        warm_ok = self.start_ms is not None and ms - self.start_ms >= 3_600_000
        frame_ok = self.last_frame_ms is not None
        f_trade = lambda: self._flow_ring(m)
        for phase in ("out", "in"):
            if phase == "in":
                self.mvol[m] += lots
            for name, vm, anchor, rpi_mode, start in self.vp:
                if anchor == "frame":
                    if phase != "out":
                        continue
                    fn = self._flow_at_frame if frame_ok else f_trade
                elif anchor == "trade_out":
                    if phase != "out":
                        continue
                    fn = f_trade
                else:
                    if phase != "in":
                        continue
                    fn = f_trade
                if rpi_mode == "norpi" and rpi:
                    continue
                if start == "warm" and not warm_ok:
                    continue
                vm.add(lots, buy, fn)

    # ---- кадр
    def pre_frame(self, ms):
        # кандидаты «не совпал» на кадре с другим ms: больше кадров с их t0 не будет
        if self.fb and self.fb_ms != ms:
            for row in self.fb:
                self.finalize(row, "fallback")
            self.fb = []
        rows = self.rows
        n = len(rows)
        # строки, у которых кадра с ms == t0 не было: состояние конца последнего кадра ≤ t0 (флаг no-frame)
        while self.pi < n and rows[self.pi].t0 < ms:
            row = rows[self.pi]
            self.pi += 1
            if self.n_frames == 0:
                row.flag = "before-stream"
                self.flags[row.flag] += 1
                row.lvl.rows_left -= 1
                continue
            row.vals = self.snapshot(row)
            self.finalize(row, "no-frame")

    def post_frame(self, ms, snap, pbest, bef, chg, incs):
        book = self.book
        best = self.best
        self.frame_N = len(self.t_ms)
        tr_all = self.tr_all
        kl = self.key_levels
        # 1. переходы размера на ценах сторожимых уровней: отмены, прирост на стене, смерть
        for k, b0 in bef.items():
            new = book[k[0]].get(k[1], 0)
            drop = b0 - new
            if drop > 0:
                ca = drop - tr_all.get(k, 0)
                if ca > 0:
                    T, C = self.lvev.setdefault(k, ([], [0]))
                    T.append(ms)
                    C.append(C[-1] + ca)
            lvls = kl.get(k)
            if lvls:
                for lvl in lvls:
                    if lvl.dead or ms <= lvl.birth:
                        continue
                    if new > b0:
                        dl = new - b0
                        if dl > lvl.wa:
                            lvl.wa = dl
                            lvl.wa1 = lvl.wa2 = ms
                        elif dl == lvl.wa:
                            lvl.wa2 = ms
                    if new > lvl.size_max:
                        lvl.size_max = new
                    if b0 > 0 and new == 0:
                        lvl.dead = True
                        lvl.death_ms = ms
                        lvl.death_before = b0
                        lvl.death_traded = tr_all.get(k, 0)
                        self.dead_ms.append(ms)
                        self.fa_dirty[lvl.s] = True
        # 2. прирост на ценах строго между лучшей ценой своей стороны и стеной (и вариант «лучшая включена»)
        for s in (0, 1):
            al = self.alive[s]
            inc = incs[s]
            if not al or not inc or best[s] is None:
                continue
            if self.fa_dirty[s]:
                al = self.alive[s] = [L for L in al if not L.dead and L.rows_left > 0]
                self.fa_floor[s] = min((L.fa for L in al), default=0)
                self.fa_dirty[s] = False
                if not al:
                    continue
            floor = self.fa_floor[s]
            ev = [(px, dl) for px, dl in inc if dl >= floor]
            if not ev:
                continue
            b = best[s]
            changed = False
            for lvl in al:
                if ms <= lvl.birth:
                    continue
                P = lvl.price
                for px, dl in ev:
                    if s == 0:
                        beyond = P < px
                        excl = px < b
                    else:
                        beyond = px < P
                        excl = px > b
                    if not beyond:
                        continue
                    if dl > lvl.fi:
                        lvl.fi = dl
                        lvl.fi1 = ms
                    if excl:
                        if dl > lvl.fa:
                            lvl.fa = dl
                            lvl.fa1 = lvl.fa2 = ms
                            changed = True
                        elif dl == lvl.fa:
                            lvl.fa2 = ms
            if changed:
                self.fa_dirty[s] = True
        # 3. рождение уровней (кадр с ms == birth): born_shift, размер, постановка под наблюдение
        lv = self.lvs
        nl = len(lv)
        while self.lp < nl and lv[self.lp].birth <= ms:
            L = lv[self.lp]
            self.lp += 1
            d = book[L.s]
            sz = d.get(L.price, 0)
            L.size_max = sz
            if L.birth == ms and not snap:
                c = chg[L.s]
                cands = []
                for P in (L.price - 1, L.price + 1):
                    old = c.get(P)
                    if old and d.get(P, 0) == 0 and sz > 0 and max(old, sz) <= 2 * min(old, sz):
                        cands.append((abs(sz - old), P, old))
                if cands:
                    cands.sort()
                    _, P, old = cands[0]
                    ticks = (P - L.price) if L.s == 0 else (L.price - P)  # плюс — от цены (рынка)
                    L.bs = (tdiv(ticks * 1_000_000, P), tdiv(ticks * 1_000_000, L.price), sz - old)
            elif L.birth != ms:
                self.n_birth_missed += 1
            self.key_levels.setdefault((L.s, L.price), []).append(L)
            self.alive[L.s].append(L)
            self.fa_dirty[L.s] = True
        # 4. поток монеты по кадру: OFI, смены лучшей цены, стеки since_far
        bb, ba = best
        if bb is not None and ba is not None:
            bsz = book[0][bb]
            asz = book[1][ba]
            if self.prev is not None:
                pbb, pbsz, pba, pasz = self.prev
                e = 0
                if bb >= pbb:
                    e += bsz
                if bb <= pbb:
                    e -= pbsz
                if ba <= pba:
                    e -= asz
                if ba >= pba:
                    e += pasz
                if e:
                    self.ofi_ms.append(ms)
                    self.ofi_cum.append(self.ofi_cum[-1] + e)
            self.prev = (bb, bsz, ba, asz)
        else:
            self.prev = None
        for s in (0, 1):
            if best[s] is not None and pbest[s] is not None and best[s] != pbest[s]:
                self.flips[s].append(ms)
        for v, st, sm in ((-ba if ba is not None else None, self.mx_n, self.mx_ms), (bb, self.mn_v, self.mn_ms)):
            if v is None:
                continue
            while st and st[-1] >= v:
                st.pop()
                sm.pop()
            st.append(v)
            sm.append(ms)
        tr_all.clear()
        # 5. строки с t0 == ms (и повтор для кандидатов на втором кадре с тем же ms)
        if self.fb and self.fb_ms == ms:
            old_fb = self.fb
            self.fb = []
            for row in old_fb:
                self.try_row(row, ms)
        rows = self.rows
        n = len(rows)
        while self.pi < n and rows[self.pi].t0 == ms:
            row = rows[self.pi]
            self.pi += 1
            self.try_row(row, ms)

    def matches(self, row):
        s = row.s
        if row.best_own is not None and self.best[s] != row.best_own:
            return False
        if row.size_at_arm is not None and self.book[s].get(row.price, 0) != row.size_at_arm:
            return False
        return True

    def try_row(self, row, ms):
        ok = self.matches(row)
        row.vals = self.snapshot(row)
        row.opp_ok = (row.best_opp is None) or (self.best[1 - row.s] == row.best_opp)
        if ok:
            self.finalize(row, "arm-frame")
        else:
            self.fb.append(row)
            self.fb_ms = ms

    def finalize(self, row, flag):
        row.flag = flag
        self.flags[flag] += 1
        row.lvl.rows_left -= 1
        if row.lvl.rows_left <= 0:
            self.fa_dirty[row.s] = True
        self.fr_register(row, self.n_frames - 1)

    def finish(self):
        """Конец потока: кандидаты «не совпал» -> fallback; строки без кадра в потоке не сверяются."""
        for row in self.fb:
            self.finalize(row, "fallback")
        self.fb = []
        while self.pi < len(self.rows):
            row = self.rows[self.pi]
            self.pi += 1
            row.flag = "after-stream"
            self.flags[row.flag] += 1

    # ---- frontrun_* (книга прошлых кадров — второй проход)
    def fr_register(self, row, jt):
        fm = self.frame_ms
        lvl = row.lvl
        t0 = row.t0
        st = lvl.starts
        if not st:
            j0 = bisect.bisect_left(fm, lvl.birth, 0, jt + 1)
            if j0 <= jt:
                st.append(j0)
        if st:
            cur = st[-1]
            while True:
                nxt = bisect.bisect_left(fm, fm[cur] + 1000, cur + 1, jt + 1)
                if nxt > jt:
                    break
                st.append(nxt)
                cur = nxt
            # frontrun_before: слоты по 1 с от рождения; читается последний кадр предыдущего слота, если он не позже t0 − 1 с,
            # иначе первый кадр предыдущего слота (в самом первом слоте — кадр рождения)
            k = bisect.bisect_right(st, jt) - 1
            if k >= 0:
                if k == 0:
                    tgt = st[0]
                else:
                    last = st[k] - 1
                    tgt = last if fm[last] + 1000 <= t0 else st[k - 1]
                self.req.setdefault(tgt, []).append((row, "fb"))
        i = bisect.bisect_right(fm, t0 - 10_000, 0, jt + 1) - 1
        if i >= 0:
            row.d10_ms = fm[i]
            self.req.setdefault(i, []).append((row, "d10"))

    def finish_frontrun(self, row):
        v = row.vals
        if v is None:
            return
        fb = row.frres.get("fb")
        d10 = row.frres.get("d10")
        if fb is not None:
            n, lots = fb
            v["frontrun_levels"] = n - 1 if n > 0 else 0
            v["frontrun_lots_at_arm"] = lots
        if d10 is not None and row.d10_ms is not None:
            lots10 = d10[1]
            if row.d10_ms >= row.birth:
                ref = row.fr_arm if row.fr_arm is not None else (fb[1] if fb is not None else None)
                v["frontrun_delta_10s_lots"] = (ref - lots10) if ref is not None else None
                if fb is not None:
                    v["alt:frontrun_delta_10s_lots@frontrun-из-python"] = fb[1] - lots10
            if row.d10_ms > row.birth:
                ref = row.fr_arm if row.fr_arm is not None else (fb[1] if fb is not None else None)
                v["alt:frontrun_delta_10s_lots@после-рождения"] = (ref - lots10) if ref is not None else None

    # ---- пересчёт колонок на кадре взвода
    def tape_window(self, lo_ms, N):
        idx = bisect.bisect_left(self.t_ms, lo_ms, 0, N)
        bl = self.cb_lots[N] - self.cb_lots[idx]
        sl = self.cs_lots[N] - self.cs_lots[idx]
        bn = self.cb_n[N] - self.cb_n[idx]
        sn = self.cs_n[N] - self.cs_n[idx]
        pp = self.cum_pp[N] - self.cum_pp[idx]
        pn = self.cum_pn[N] - self.cum_pn[idx]
        return bl, sl, bn, sn, pp, pn, idx

    def top_sum(self, s, n):
        d = self.book[s]
        if s == 0:
            return sum(d[k] for k in heapq.nlargest(n, d))
        return sum(d[k] for k in heapq.nsmallest(n, d))

    def cancel_sum(self, key, lo):
        e = self.lvev.get(key)
        if not e:
            return 0
        T, C = e
        return C[-1] - C[bisect.bisect_left(T, lo)]

    def best_move(self, row, h, mode):
        s = row.s
        birth = row.birth
        if row.t0 - birth < h:
            return None
        jt = self.n_frames - 1
        fm = self.frame_ms
        arr = self.fbb if s == 0 else self.fba
        jb = bisect.bisect_left(fm, birth, 0, jt + 1)
        if mode == "last":
            jh = bisect.bisect_right(fm, birth + h, 0, jt + 1) - 1
        else:
            jh = bisect.bisect_left(fm, birth + h, 0, jt + 1)
        if jb > jt or jh > jt or jh < 0:
            return None
        b0, b1 = arr[jb], arr[jh]
        if b0 < 0 or b1 < 0:
            return None
        sg = 1 if s == 0 else -1  # плюс — от стены: у бид-стены вверх, у аск-стены вниз
        return mv_cbps(sg, b0, b1, row.price if mode == "wall" else b0)

    def snapshot(self, row):
        s = row.s
        t0 = row.t0
        lvl = row.lvl
        N = self.frame_N
        o = {}
        s0 = t0 // 1000
        m0 = t0 // 60000
        warm = t0 - self.start_ms
        birth = row.birth
        # лента
        tw = {}
        for W in (15, 30, 60):
            if warm >= W * 1000:
                bl, sl, bn, sn, pp, pn, _ = self.tape_window((s0 - W + 1) * 1000, N)
                tw[W] = (bl, sl, bn, sn)
                if s == 0:  # у бид-стены против неё — продают
                    pl, wl, pnn, wn = sl, bl, sn, bn
                else:
                    pl, wl, pnn, wn = bl, sl, bn, sn
                o[f"tape_press_lots_{W}s"] = pl
                o[f"tape_with_lots_{W}s"] = wl
                o[f"tape_all_lots_{W}s"] = pl + wl
                o[f"tape_press_n_{W}s"] = pnn
                o[f"tape_with_n_{W}s"] = wn
                o[f"tape_all_n_{W}s"] = pnn + wn
                if W in (15, 60):
                    o[f"sign_ac_{W}s_bp"] = tdiv(pp * 10000, pn) if pn > 0 else None
                if W == 30:
                    o["tape_press_avg_30s_e2"] = tdiv(pl * 100, pnn) if pnn > 0 else None
                    o["tape_with_avg_30s_e2"] = tdiv(wl * 100, wn) if wn > 0 else None
        if warm >= 3_600_000:
            bl, sl, _, _, _, _, _ = self.tape_window((m0 - FLOW_WINDOW_MIN + 1) * 60000, N)
            p60, w60 = (sl, bl) if s == 0 else (bl, sl)
            o["tape_press_60m_lots"] = p60
            o["tape_with_60m_lots"] = w60
            for W in (15, 30):
                if W in tw:
                    bl2, sl2 = tw[W][0], tw[W][1]
                    pw, ww = (sl2, bl2) if s == 0 else (bl2, sl2)
                    o[f"tape_burst_press_{W}s_bp"] = tdiv(pw * 3600 * 10000, W * p60) if p60 > 0 else None
                    o[f"tape_burst_with_{W}s_bp"] = tdiv(ww * 3600 * 10000, W * w60) if w60 > 0 else None
            for name, vm, _a, _r, _s in self.vp:
                o["vpin_bp" if name == VPIN_MAIN else f"alt:vpin_bp@{name}"] = vm.value()
        for name, _vm, _a, _r, _s in self.vp:
            if name != VPIN_MAIN:
                o.setdefault(f"alt:vpin_bp@{name}", None)
        # размер сделки за 15 мин
        if warm >= 15 * 60000:
            idx = bisect.bisect_left(self.t_ms, (m0 - 14) * 60000, 0, N)
            cnt = Counter(self.kb[idx:N])
            n = sum(cnt.values())
            for pc in (50, 90):
                val = None
                if n > 0:
                    target = -(-pc * n // 100)
                    acc = 0
                    for k in sorted(cnt):
                        acc += cnt[k]
                        if acc >= target:
                            val = 1 << k
                            break
                o[f"trade_size_p{pc}_15m"] = val
        # книга
        for Nn in (1, 5, 10, 50):
            own = self.top_sum(s, Nn)
            opp = self.top_sum(1 - s, Nn)
            o[f"obi{Nn}_bp"] = tdiv((own - opp) * 10000, own + opp) if own + opp > 0 else None
        bb, ba = self.best
        if bb is not None and ba is not None:
            bsz = self.book[0][bb]
            asz = self.book[1][ba]
            v = tdiv((ba - bb) * (bsz - asz) * 1_000_000, (bsz + asz) * (bb + ba))
            o["micro_off_cbps"] = v if s == 0 else -v
        sg = 1 if s == 0 else -1
        for W in (10, 60):
            if warm >= W * 1000:
                i0 = bisect.bisect_left(self.ofi_ms, (s0 - W + 1) * 1000)
                o[f"ofi_{W}s_lots"] = sg * (self.ofi_cum[-1] - self.ofi_cum[i0])
        for W in (15, 60):
            if warm >= W * 1000:
                o[f"best_flips_{W}s"] = len(self.flips[s]) - bisect.bisect_left(self.flips[s], (s0 - W + 1) * 1000)
        # отмены на цене стены (переход кадра рождения не считается; сделки на цене в кадре вычитаются)
        key = (s, row.price)
        for W in (1, 3):
            lo = max((s0 - W + 1) * 1000, birth + 1)
            o[f"cancel_{W}s_lots"] = self.cancel_sum(key, lo) if warm >= W * 1000 else None
        o["cancel_life_lots"] = self.cancel_sum(key, birth + 1)
        if t0 - birth >= 3_600_000 and warm >= 3_600_000:
            o["cancel_60m_lots"] = self.cancel_sum(key, max((m0 - FLOW_WINDOW_MIN + 1) * 60000, birth + 1))
        else:
            o["cancel_60m_lots"] = None
        # прирост размера: на стене / между лучшей ценой и стеной — от рождения до кадра взвода
        o["wall_add_max_lots"] = lvl.wa
        o["wall_add_max_age_ms"] = (t0 - lvl.wa1) if lvl.wa > 0 else None
        o["alt:wall_add_max_age_ms@последний-при-равенстве"] = (t0 - lvl.wa2) if lvl.wa > 0 else None
        o["front_add_max_lots"] = lvl.fa
        o["front_add_max_age_ms"] = (t0 - lvl.fa1) if lvl.fa > 0 else None
        o["alt:front_add_max_age_ms@последний-при-равенстве"] = (t0 - lvl.fa2) if lvl.fa > 0 else None
        o["alt:front_add_max_lots@лучшая-цена-включена"] = lvl.fi
        o["alt:front_add_max_age_ms@лучшая-цена-включена"] = (t0 - lvl.fi1) if lvl.fi > 0 else None
        # рождение переустановкой (приближённо: соседняя цена умерла в кадре рождения, размеры в 2×)
        if lvl.bs is not None:
            o["born_shift_cbps"] = lvl.bs[0]
            o["alt:born_shift_cbps@база-новая-цена"] = lvl.bs[1]
            o["born_shift_lots"] = lvl.bs[2]
        else:
            o["born_shift_cbps"] = o["born_shift_lots"] = None
        # прошлая смерть уровня на этой цене (приближённо: обнуление цены у предыдущего уровня кэша)
        p = lvl.prev
        while p is not None and not p.dead:
            p = p.prev
        o["prev_death_gap_ms"] = o["prev_death_outcome"] = None
        if p is not None and p.death_ms is not None and p.death_ms <= t0:
            later = bisect.bisect_right(self.dead_ms, t0) - bisect.bisect_right(self.dead_ms, p.death_ms)
            if later < RING_DEATHS:
                o["prev_death_gap_ms"] = t0 - p.death_ms
                bf = p.death_before
                tr = p.death_traded
                o["prev_death_outcome"] = 1 if tr * 100 >= 70 * bf else (0 if tr * 100 <= 20 * bf else 2)
                mx = p.size_max
                o["alt:prev_death_outcome@сделки-за-жизнь"] = (
                    1 if p.traded * 100 >= 70 * mx else (0 if p.traded * 100 <= 20 * mx else 2)) if mx > 0 else None
        # форма стека: 15 ближайших тиков от лучшей цены стороны; стена вне них -> UNDEF
        d = self.book[s]
        wall = d.get(row.price, 0)
        bs_own = self.best[s]
        o["size_share15_bp"] = o["nz_levels15"] = None
        if bs_own is not None:
            rng = range(bs_own - 14, bs_own + 1) if s == 0 else range(bs_own, bs_own + 15)
            tot = nz = 0
            for t in rng:
                z = d.get(t, 0)
                tot += z
                if z:
                    nz += 1
            inside = row.price in rng or (row.price > bs_own if s == 0 else row.price < bs_own)
            if inside:
                o["size_share15_bp"] = tdiv(wall * 10000, tot) if tot > 0 else None
                o["nz_levels15"] = nz
            o["alt:nz_levels15@без-UNDEF-вне-15"] = nz
        rng_old = range(row.price - 14, row.price + 1) if s == 0 else range(row.price, row.price + 15)
        tot = nz = 0
        for t in rng_old:
            z = d.get(t, 0)
            tot += z
            if z:
                nz += 1
        o["alt:size_share15_bp@15-тиков-от-стены"] = tdiv(wall * 10000, tot) if tot > 0 else None
        o["alt:nz_levels15@15-тиков-от-стены"] = nz
        # ход лучшей цены своей стороны после рождения стены
        for h, nm in ((1000, "1s"), (10000, "10s")):
            o[f"best_move_{nm}_cbps"] = self.best_move(row, h, "first")
            o[f"alt:best_move_{nm}_cbps@последний-кадр-≤"] = self.best_move(row, h, "last")
            o[f"alt:best_move_{nm}_cbps@база-стена"] = self.best_move(row, h, "wall")
        # ближайший «отобранный» уровень на чужой стороне (пол H3 — оценка: трекер недоступен)
        F = self.h3_floor
        o["opp_wall_dist_cbps"] = o["opp_wall_ratio_bp"] = None
        if F:
            do = self.book[1 - s]
            pp_ = (min((p_ for p_, q in do.items() if q >= F), default=None) if s == 0
                   else max((p_ for p_, q in do.items() if q >= F), default=None))
            if pp_ is not None:
                o["opp_wall_dist_cbps"] = tdiv(abs(pp_ - row.price) * 1_000_000, row.price)
                o["opp_wall_ratio_bp"] = tdiv(do[pp_] * 10000, wall) if wall > 0 else None
        # since_far: t0 − max(последний кадр, где чужая лучшая цена дальше 2·D от стены, рождение)
        thr = (2 * self.D * row.price) // 10000 + 1
        if s == 0:
            c = bisect.bisect_right(self.mx_n, -(row.price + thr))
            last = self.mx_ms[c - 1] if c else None
        else:
            c = bisect.bisect_right(self.mn_v, row.price - thr)
            last = self.mn_ms[c - 1] if c else None
        o["since_far_ms"] = t0 - (max(last, birth) if last is not None else birth)
        for c_ in R1_NAMES:
            o.setdefault(c_, None)
        return o


# --------------------------------------------------------------------------- кэш, сверка, отчёты

SIDES = ("bid", "ask")
FRAME_FLAGS = ("arm-frame", "fallback", "no-frame")
# колонки, которые без трекера уровней восстанавливаются только приближённо (расхождения ожидаемы и печатаются)
APPROX = {
    "born_shift_cbps": "рождение переустановкой: трекер знает прежнюю цену стены; здесь — соседняя цена, умершая в кадре рождения, размеры в 2×",
    "born_shift_lots": "то же: размер новой минус размер умершей соседней цены",
    "prev_death_gap_ms": "кольцо смертей трекера не восстановить: здесь смерть = обнуление цены у предыдущего уровня кэша на той же цене",
    "prev_death_outcome": "исход 70/20 по сделкам кадра смерти предыдущего уровня кэша",
    "opp_wall_dist_cbps": "множество «отобранных» стен чужой стороны — состояние трекера; здесь пол H3 = min size_at_arm кэша или --h3-floor-lots",
    "opp_wall_ratio_bp": "то же: отношение размера ближайшей стены чужой стороны к стене",
}


def r1_columns(hdr):
    """Колонки R1 кэша — всё от первой колонки пакета до конца шапки (кроме калибровочных)."""
    start = hdr.index("tape_press_lots_15s") if "tape_press_lots_15s" in hdr else None
    if start is None:
        for i, c in enumerate(hdr):
            if c in GROUP_OF:
                start = i
                break
    if start is None:
        raise SystemExit("в шапке кэша нет колонок R1 (ни одной из договора)")
    return [c for c in hdr[start:] if c not in CALIB_COLS]


def utc_day(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms // 1000))


def opt_int(x):
    return None if x is None or x == "" else parse_cell(x)


def read_cache(path, side=None, day=None, sample=0):
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        hdr = list(rd.fieldnames or [])
        miss = [c for c in ("side", "price_tick", "birth_ms", "arm_ms") if c not in hdr]
        if miss:
            raise SystemExit(f"в кэше нет колонок {miss}")
        r1_cols = r1_columns(hdr)
        calib = [c for c in CALIB_COLS if c in hdr]
        keep = r1_cols + calib
        rows = []
        n_all = n_noarm = 0
        for i, rec in enumerate(rd):
            n_all += 1
            if rec["arm_ms"] == "":
                n_noarm += 1
                continue
            arm = int(rec["arm_ms"])
            if side and rec["side"] != side:
                continue
            if day and utc_day(arm) != day:
                continue
            rows.append(Row(i + 2, rec["side"], int(rec["price_tick"]), arm, int(rec["birth_ms"]), {c: rec[c] for c in keep},
                            opt_int(rec.get("size_at_arm")), opt_int(rec.get("best_own_tick")), opt_int(rec.get("best_opp_tick")),
                            opt_int(rec.get("frontrun_lots_at_arm"))))
    if sample and sample > 1:
        rows = rows[::sample]
    return hdr, r1_cols, calib, rows, n_all, n_noarm


def replay_records(sm, recs):
    for ev, ts, px, q, at in recs:
        if not sm.record(ev, ts, px, q, at):
            return False
    if sm.has_open:
        return sm.flush()
    return True


def replay_file(path, sm):
    n = 0
    good = True
    for frame in iter_frames(path):
        n += len(frame)
        rec = sm.record
        for ev, ts, px, q, at in frame:
            if not rec(ev, ts, px, q, at):
                good = False
                break
        if not good:
            break
    if good and sm.has_open:
        good = sm.flush()
    return n, good


def compare(rows, max_diffs):
    stats = {}
    diffs = {}
    bad_rows = Counter()
    n_rows = Counter()
    for r in rows:
        if r.vals is None or r.flag not in FRAME_FLAGS:
            continue
        n_rows[r.flag] += 1
        row_bad = False
        for col, py in r.vals.items():
            base = col[4:].split("@")[0] if col.startswith("alt:") else col
            if base not in r.cells:
                continue
            cv = parse_cell(r.cells[base])
            s = stats.get((col, r.side))
            if s is None:
                s = stats[(col, r.side)] = {"n": 0, "eq": 0, "ne": 0, "both_undef": 0, "py_undef": 0, "cache_undef": 0}
            s["n"] += 1
            if py is None and cv is None:
                s["both_undef"] += 1
                continue
            if py is None:
                kind = "py_undef"
            elif cv is None:
                kind = "cache_undef"
            elif py == cv:
                s["eq"] += 1
                continue
            else:
                kind = "ne"
            s[kind] += 1
            if not col.startswith("alt:"):
                row_bad = True
            lim = max_diffs if not col.startswith("alt:") else min(2, max_diffs)
            d = diffs.setdefault((col, r.side), [])
            if len(d) < lim:
                d.append((r.line, r.t0, r.price, py, cv, r.flag))
        if row_bad:
            bad_rows[r.flag] += 1
    return stats, diffs, n_rows, bad_rows


def coverage(stats, cache_cols):
    res = []
    for g, cols in R1_GROUPS:
        in_cache = [c for c in cols if c in cache_cols]
        cmp_cols = []
        clean = []
        for c in in_cache:
            ss = [stats[(c, sd)] for sd in SIDES if (c, sd) in stats]
            if not ss:
                continue
            cmp_cols.append(c)
            eq = sum(s["eq"] for s in ss)
            bad = sum(s["ne"] + s["py_undef"] + s["cache_undef"] for s in ss)
            if eq > 0 and bad == 0:
                clean.append(c)
        res.append({"group": g, "listed": cols, "in_cache": in_cache, "compared": cmp_cols, "clean": clean})
    return res


def print_report(rows, r1_cols, stats, diffs, n_rows, bad_rows, max_diffs):
    cols = sorted({c for c, _ in stats}, key=lambda c: ((c.startswith("alt:")),
                                                         R1_NAMES.index(c[4:].split("@")[0] if c.startswith("alt:") else c)
                                                         if (c[4:].split("@")[0] if c.startswith("alt:") else c) in R1_NAMES else 999, c))
    hdr_line = (f"{'колонка':58} {'сторона':5} {'сравн':>6} {'равно':>6} {'расх':>5} {'пусто/пусто':>11} "
                f"{'py-пусто/кэш-знач':>17} {'py-знач/кэш-пусто':>17}")
    main_cols = [c for c in cols if not c.startswith("alt:")]
    alt_cols = [c for c in cols if c.startswith("alt:")]
    for title, group in (("ОСНОВНЫЕ КОЛОНКИ (строк, равно, расхождение значений, пусто/пусто, пусто/значение)", main_cols),
                         ("АЛЬТЕРНАТИВНЫЕ ТРАКТОВКИ / не сверяемое точно (для разбора неоднозначностей определения)", alt_cols)):
        if not group:
            continue
        print(title)
        print(hdr_line)
        for c in group:
            for sd in SIDES:
                s = stats.get((c, sd))
                if s:
                    print(f"{c:58} {sd:5} {s['n']:6} {s['eq']:6} {s['ne']:5} {s['both_undef']:11} {s['py_undef']:17} {s['cache_undef']:17}")
        print()
    cov = coverage(stats, set(r1_cols))
    print("ПОКРЫТИЕ ПО ГРУППАМ (договор Судьи, п. C): колонок в кэше / сверено / без расхождений на обеих сторонах")
    for g in cov:
        mark = "есть чистая колонка" if g["clean"] else ("расхождения по всем сверенным" if g["compared"] else "НЕ СВЕРЕНО")
        print(f"  {g['group']:12} {len(g['in_cache'])}/{len(g['listed'])} в кэше, сверено {len(g['compared'])}, чисто {len(g['clean'])}: {mark}"
              + (f"  [чисто: {', '.join(g['clean'])}]" if g["clean"] else ""))
        no_cache = [c for c in g["listed"] if c not in g["in_cache"]]
        if no_cache:
            print(f"      нет в шапке кэша: {', '.join(no_cache)}")
    extra = [c for c in r1_cols if c not in GROUP_OF and c not in CALIB_COLS]
    if extra:
        print(f"  колонки кэша вне списка скрипта (скриптом не покрыты): {', '.join(extra)}")
    approx_in = [c for c in APPROX if c in set(r1_cols)]
    if approx_in:
        print("  Приближённо — трекер уровней не восстановить из бинлога, расхождения ожидаемы:")
        for c in approx_in:
            print(f"    {c}: {APPROX[c]}")
    print()
    print("калибровочная колонка frontrun_lots_at_arm (сумма лотов впереди на кадре «за 1–2 с до t0») — не из договора, проверка правила слотов")
    print(f"строк по флагу кадра (сверено / из них с расхождением в основных колонках): "
          + ", ".join(f"{f} {n_rows.get(f, 0)}/{bad_rows.get(f, 0)}" for f in FRAME_FLAGS))
    ofl = [r for r in rows if r.flag == "arm-frame" and r.opp_ok is False]
    print(f"справочно: строк arm-frame, где чужая лучшая цена кэша ≠ книге на кадре: {len(ofl)} из {n_rows.get('arm-frame', 0)}")
    only_undef = [c for c in main_cols if not any(stats.get((c, sd), {"eq": 0, "ne": 0})["eq"] + stats.get((c, sd), {"eq": 0, "ne": 0})["ne"]
                                                  for sd in SIDES)]
    if only_undef:
        print("колонки, где сравнение пришлось только на «пусто/пусто» и расхождения пустоты (значений нет): " + ", ".join(only_undef))
    print()
    for c in cols:
        for sd in SIDES:
            d = diffs.get((c, sd))
            if d:
                s = stats[(c, sd)]
                tot = s["ne"] + s["py_undef"] + s["cache_undef"]
                print(f"расхождения {c} [{sd}] — первые {len(d)} из {tot}  (строка CSV, t0, цена, python, кэш, флаг кадра):")
                for x in d:
                    print("   ", x)


def undef_share(paths):
    """Доля UNDEF (пустых клеток) по колонкам R1: по всем подходам и по базе (side=bid, возраст стены arm−birth ≥ 2700 с)."""
    order = []
    cnt = {}
    n_files = n_rows = n_base = 0
    for p in paths:
        with open(p, newline="", encoding="utf-8") as f:
            rd = csv.DictReader(f)
            hdr = list(rd.fieldnames or [])
            miss = [c for c in ("side", "birth_ms", "arm_ms") if c not in hdr]
            if miss:
                raise SystemExit(f"{p}: в кэше нет колонок {miss}")
            cols = r1_columns(hdr)
            for c in cols:
                if c not in cnt:
                    cnt[c] = [0, 0, 0, 0]  # n, пусто (все), n база, пусто (база)
                    order.append(c)
            n_files += 1
            for rec in rd:
                n_rows += 1
                base = rec["side"] == "bid" and rec["arm_ms"] != "" and rec["birth_ms"] != "" \
                    and int(rec["arm_ms"]) - int(rec["birth_ms"]) >= BASE_AGE_MS
                if base:
                    n_base += 1
                for c in cols:
                    e = rec[c] == ""
                    k = cnt[c]
                    k[0] += 1
                    k[1] += e
                    if base:
                        k[2] += 1
                        k[3] += e
    return order, cnt, n_files, n_rows, n_base


def print_undef_share(order, cnt, n_files, n_rows, n_base):
    print(f"файлов {n_files}, подходов {n_rows}, из них база (side=bid, возраст ≥ {BASE_AGE_MS // 1000} с) {n_base}")
    print(f"{'колонка':32} {'n':>9} {'доля UNDEF (все)':>18} {'доля UNDEF (база)':>19} {'n база':>9}")
    for c in order:
        n, u, nb, ub = cnt[c]
        a = f"{100 * u / n:.1f} %" if n else "—"
        b = f"{100 * ub / nb:.1f} %" if nb else "—"
        print(f"{c:32} {n:9} {a:>18} {b:>19} {nb:9}")


# --------------------------------------------------------------------------- самопроверка на ручных числах


def _scenario_events(mirror):
    """Ручной сценарий (цены в тиках, мс): бид-стена 98, рождена снапшотом 1000; зеркало — аск-стена 102 (цена ↔ 200 − цена,
    бид ↔ аск, покупка ↔ продажа): все зеркало-инвариантные колонки обязаны совпасть."""
    def M(p):
        return 200 - p if mirror else p

    def dep(ts, s, px, q, snap=False):
        s2 = (1 - s) if mirror else s
        ev = (EV_BID_SNAP if s2 == 0 else EV_ASK_SNAP) if snap else (EV_BID_DEPTH if s2 == 0 else EV_ASK_DEPTH)
        return (ev, ts * 1_000_000, M(px), q, 0)

    def trd(ts, buy, px, lots):
        b2 = (not buy) if mirror else buy
        return ((EV_BUY_TRADE if b2 else EV_SELL_TRADE), ts * 1_000_000, M(px), lots, 0)

    ev = []
    for px, q in ((100, 5), (99, 3), (98, 10)):
        ev.append(dep(1000, 0, px, q, True))
    for px, q in ((101, 4), (102, 6)):
        ev.append(dep(1000, 1, px, q, True))
    ev.append(dep(2000, 0, 99, 8))
    ev.append(dep(3000, 0, 100, 12))
    ev.append(dep(4000, 0, 98, 14))
    ev.append(trd(4500, False, 98, 3))
    ev.append(dep(5000, 0, 98, 6))
    ev.append(trd(40000, True, 101, 2))
    ev.append(trd(50000, False, 100, 4))
    ev.append(trd(58000, False, 100, 1))
    ev.append(dep(64000, 0, 100, 0))
    ev.append(dep(65000, 0, 100, 12))
    ev.append(trd(66000, True, 101, 3))
    ev.append(trd(69500, False, 100, 2))
    ev.append(dep(70000, 1, 101, 5))
    return ev


def _selftest_run(mirror, approach_bps=20, floor=5):
    side = "ask" if mirror else "bid"
    price = 102 if mirror else 98
    best_opp = 99 if mirror else 101
    rows = [Row(2, side, price, 70000, 1000, {}, 6, 100, best_opp, 25),     # кадр взвода, совпал
            Row(3, side, price, 70000, 1000, {}, 7, 100, best_opp, None),   # размер не совпал -> fallback
            Row(4, side, price, 69800, 1000, {}, None, None, None, None)]  # кадра с таким ms нет -> no-frame
    ev = _scenario_events(mirror)
    sim = Sim(rows, approach_bps, h3_floor=floor)
    ok = replay_records(sim, ev)
    sim.finish()
    p2 = Pass2(sim.req)
    ok2 = replay_records(p2, ev)
    for r in rows:
        sim.finish_frontrun(r)
    return sim, rows, ok and ok2


def selftest():
    fails = []
    n_ok = [0]

    def chk(name, got, exp):
        if got == exp:
            n_ok[0] += 1
        else:
            fails.append(f"{name}: получено {got!r}, ожидалось {exp!r}")

    # целочисленные помощники
    chk("tdiv(7,2)", tdiv(7, 2), 3)
    chk("tdiv(-7,2)", tdiv(-7, 2), -3)
    chk("mv_cbps вниз на 1 тик от 100", mv_cbps(1, 100, 99, 100), -10000)
    chk("mv_cbps ask-стена вниз", mv_cbps(-1, 100, 99, 100), 10000)

    # декодер кадра v3: две записи глубины бид-снапшота (цена 100 -> 99, лоты 5 -> 3)
    def uv(n):
        out = bytearray()
        while True:
            b = n & 127
            n >>= 7
            if n:
                out.append(b | 128)
            else:
                out.append(b)
                return bytes(out)

    def zz(n):
        return uv((n << 1) ^ (n >> 63))
    pl = ((5_000_000_000).to_bytes(8, "little", signed=True) + uv(EV_BID_SNAP) + zz(0) + zz(7) + uv(0) + uv(2)
          + zz(100) + zz(5) + zz(-1) + zz(-2))
    chk("decode_frame", decode_frame(pl), [(EV_BID_SNAP, 5_000_000_000, 100, 5, 0), (EV_BID_SNAP, 5_000_000_000, 99, 3, 0)])

    # VPIN: размер бара = flow/50 = 2 лота; уравновешенные бары -> 0; односторонние -> 10000; до 50 баров — пусто
    vm = VpinMachine()
    for i in range(100):
        vm.add(1, i % 2 == 0, lambda: 100)
    chk("vpin уравновешенный", vm.value(), 0)
    vm = VpinMachine()
    for _ in range(100):
        vm.add(1, True, lambda: 100)
    chk("vpin односторонний", vm.value(), 10000)
    vm = VpinMachine()
    for _ in range(98):
        vm.add(1, True, lambda: 100)
    chk("vpin до 50 баров", vm.value(), None)

    # сценарий: бид-стена 98 (рождена в снапшоте 1000), кадр взвода 70000; пять колонок на ленту/окна и т.д. — руками
    sim, rows, ok = _selftest_run(False)
    chk("сценарий: поток прошёл", ok and sim.error is None, True)
    chk("сценарий: кадров книги", sim.n_frames, 8)
    r0, r1, r2 = rows
    chk("флаг кадра: arm-frame", r0.flag, "arm-frame")
    chk("флаг кадра: fallback (размер стены не совпал)", r1.flag, "fallback")
    chk("флаг кадра: no-frame (кадра с таким ms нет)", r2.flag, "no-frame")
    exp = {
        "tape_press_lots_15s": 3, "tape_with_lots_15s": 3, "tape_all_lots_15s": 6,
        "tape_press_n_15s": 2, "tape_with_n_15s": 1, "tape_all_n_15s": 3,
        "tape_press_lots_30s": 7, "tape_with_lots_30s": 3, "tape_all_lots_30s": 10,
        "tape_press_n_30s": 3, "tape_with_n_30s": 1, "tape_all_n_30s": 4,
        "tape_press_lots_60s": 7, "tape_with_lots_60s": 5, "tape_all_lots_60s": 12,
        "tape_press_n_60s": 3, "tape_with_n_60s": 2, "tape_all_n_60s": 5,
        "tape_press_avg_30s_e2": 233, "tape_with_avg_30s_e2": 300,
        "sign_ac_15s_bp": -3333, "sign_ac_60s_bp": -6000,
        # прогрев 60 мин / 15 мин не пройден -> пусто
        "tape_press_60m_lots": None, "tape_with_60m_lots": None, "tape_burst_press_15s_bp": None,
        "tape_burst_with_30s_bp": None, "vpin_bp": None, "trade_size_p50_15m": None, "trade_size_p90_15m": None,
        "obi1_bp": 4117, "obi5_bp": 4054, "obi10_bp": 4054, "obi50_bp": 4054, "micro_off_cbps": 2048,
        "ofi_10s_lots": -1, "ofi_60s_lots": -1, "best_flips_15s": 2, "best_flips_60s": 2,
        "cancel_1s_lots": 0, "cancel_3s_lots": 0, "cancel_life_lots": 5, "cancel_60m_lots": None,
        "wall_add_max_lots": 4, "wall_add_max_age_ms": 66000, "front_add_max_lots": 5, "front_add_max_age_ms": 68000,
        "alt:front_add_max_lots@лучшая-цена-включена": 12, "alt:front_add_max_age_ms@лучшая-цена-включена": 5000,
        "born_shift_cbps": None, "born_shift_lots": None, "prev_death_gap_ms": None, "prev_death_outcome": None,
        "size_share15_bp": 2307, "nz_levels15": 3,
        "best_move_1s_cbps": 0, "best_move_10s_cbps": -10000, "alt:best_move_10s_cbps@последний-кадр-≤": 0,
        "alt:best_move_10s_cbps@база-стена": -10204,
        "opp_wall_dist_cbps": 30612, "opp_wall_ratio_bp": 8333,
        "frontrun_levels": 1, "frontrun_lots_at_arm": 20, "frontrun_delta_10s_lots": 5,
        "since_far_ms": 0,
    }
    for c, v in exp.items():
        chk(f"бид-сценарий {c}", r0.vals.get(c, "нет колонки"), v)
    chk("no-frame: tape_press_lots_15s (только сделки до последнего кадра)", r2.vals.get("tape_press_lots_15s"), 1)
    chk("no-frame: tape_with_lots_15s", r2.vals.get("tape_with_lots_15s"), 0)
    # since_far: при D = 1000 bps чужая цена никогда не «дальше 2·D» -> t0 − рождение
    sim_d, rows_d, _ = _selftest_run(False, approach_bps=1000)
    chk("since_far при D=1000 bps = t0 − рождение", rows_d[0].vals["since_far_ms"], 69000)

    # зеркало: аск-стена 102 — все не зависящие от цены колонки совпадают с бид-сценарием
    sim_m, rows_m, ok_m = _selftest_run(True)
    chk("зеркало: поток прошёл", ok_m and sim_m.error is None, True)
    chk("зеркало: флаг кадра", rows_m[0].flag, "arm-frame")
    price_dep = {"micro_off_cbps", "opp_wall_dist_cbps"}
    for c in R1_NAMES:
        if c in price_dep:
            continue
        chk(f"зеркало {c}", rows_m[0].vals.get(c), r0.vals.get(c))
    chk("зеркало micro_off_cbps", rows_m[0].vals.get("micro_off_cbps"), 2069)
    chk("зеркало opp_wall_dist_cbps", rows_m[0].vals.get("opp_wall_dist_cbps"), 29411)

    # --undef-share на малом файле: все 4 подхода, база = bid и возраст ≥ 2700 с (строки 1 и 4)
    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    try:
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            f.write("side,birth_ms,arm_ms,tape_press_lots_15s,vpin_bp\n"
                    "bid,0,3000000,1,\n"
                    "bid,0,1000000,,\n"
                    "ask,0,4000000,,7\n"
                    "bid,100,2800000,2,\n")
        order, cnt, nf, nr, nb = undef_share([tmp])
    finally:
        os.remove(tmp)
    chk("undef-share: колонки", order, ["tape_press_lots_15s", "vpin_bp"])
    chk("undef-share: файлов/строк/база", (nf, nr, nb), (1, 4, 2))
    chk("undef-share: tape (n, пусто, n база, пусто база)", cnt["tape_press_lots_15s"], [4, 2, 2, 0])
    chk("undef-share: vpin", cnt["vpin_bp"], [4, 3, 2, 2])

    if fails:
        print(f"SELFTEST: провалено {len(fails)}, пройдено {n_ok[0]}")
        for x in fails:
            print("  ПРОВАЛ", x)
        return False
    print(f"SELFTEST: все проверки пройдены ({n_ok[0]})")
    return True


# --------------------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(description="TK-025: независимый пересчёт колонок R1 из бинлога суток, кадр взвода, все подходы")
    ap.add_argument("binlog", nargs="?", help="бинлог v3 суток монеты")
    ap.add_argument("cache", nargs="?", help="approaches-<SYM>.csv с колонками R1 (кэш подходов)")
    ap.add_argument("--approach-bps", type=int, default=20, help="D, bps (для since_far: «дальше 2·D»)")
    ap.add_argument("--json", default=None, help="сводка сверки (или таблица --undef-share) в JSON")
    ap.add_argument("--sample", type=int, default=0, help="брать каждую K-ю строку кэша (ускорение; состояние уровней от этого не зависит)")
    ap.add_argument("--side", choices=SIDES, default=None, help="только стены этой стороны")
    ap.add_argument("--day", default=None, help="UTC-день YYYY-MM-DD: брать строки кэша с arm_ms в этих сутках (по умолчанию — из имени бинлога)")
    ap.add_argument("--vpin-alt", action="store_true", help="считать все 12 трактовок vpin (колонки alt:vpin_bp@...)")
    ap.add_argument("--h3-floor-lots", type=int, default=None, help="пол H3 в лотах для opp_wall (по умолчанию — min size_at_arm кэша)")
    ap.add_argument("--max-diffs", type=int, default=5, help="сколько первых расхождений печатать на колонку и сторону")
    ap.add_argument("--undef-share", nargs="+", metavar="CSV", default=None,
                    help="без бинлога: доля UNDEF по колонкам R1 (все подходы / база: bid и возраст стены ≥ 2700 с)")
    ap.add_argument("--selftest", action="store_true", help="проверки на ручных числах (без бинлога и кэша)")
    a = ap.parse_args()

    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if a.undef_share:
        order, cnt, nf, nr, nb = undef_share(a.undef_share)
        print_undef_share(order, cnt, nf, nr, nb)
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump({"files": a.undef_share, "rows": nr, "base_rows": nb,
                           "columns": {c: {"n": k[0], "undef_all": k[1], "n_base": k[2], "undef_base": k[3]} for c, k in cnt.items()}},
                          f, ensure_ascii=False, indent=1)
        return
    if not a.binlog or not a.cache:
        ap.error("нужны binlog и cache (или --undef-share / --selftest)")

    t_start = time.time()
    day = a.day
    if day is None:
        import re
        m = re.search(r"\d{4}-\d{2}-\d{2}", a.binlog.replace("\\", "/").split("/")[-1])
        day = m.group(0) if m else None
    hdr, r1_cols, calib, rows, n_all, n_noarm = read_cache(a.cache, a.side, day, a.sample)
    print(f"кэш {a.cache}: строк {n_all} (без arm_ms {n_noarm}), в расчёте {len(rows)} "
          f"[сторона={a.side or 'обе'}, день={day or 'все'}, выборка={'1/' + str(a.sample) if a.sample > 1 else 'все'}]; "
          f"колонок R1 в кэше {len(r1_cols)}" + (f" (+ калибровка {', '.join(calib)})" if calib else ""))
    if not rows:
        raise SystemExit("нет строк для сверки")
    floor = a.h3_floor_lots
    if floor is None:
        sz = [r.size_at_arm for r in rows if r.size_at_arm]
        floor = min(sz) if sz else None
        print(f"пол H3 для opp_wall (оценка): min size_at_arm = {floor}")

    sim = Sim(rows, a.approach_bps, vpin_alt=a.vpin_alt, h3_floor=floor)
    n_rec, ok = replay_file(a.binlog, sim)
    sim.finish()
    print(f"бинлог {a.binlog}: записей {n_rec}, кадров книги {sim.n_frames}, сделок (не блок) {len(sim.t_ms)}, блочных {sim.n_block}, "
          f"RPI {sim.n_rpi}; не монотонных сделок {sim.nonmono_trades}, кадров {sim.nonmono_frames}; уровней, рождённых не на своём кадре "
          f"{sim.n_birth_missed}; ошибка={sim.error}; {time.time() - t_start:.1f} с")
    print("строк по флагу кадра: " + ", ".join(f"{k} {v}" for k, v in sorted(sim.flags.items())))
    early = sum(1 for r in rows if sim.start_ms is not None and r.birth < sim.start_ms)
    if early:
        print(f"строк, где стена рождена до начала потока бинлога (состояние трекера недоступно): {early} из {len(rows)}")

    if sim.req and not sim.error:
        p2 = Pass2(sim.req)
        _, ok2 = replay_file(a.binlog, p2)
        for r in rows:
            sim.finish_frontrun(r)
        print(f"frontrun_*: второй проход по бинлогу, запросов кадров {len(sim.req)}, ошибка={p2.error}; {time.time() - t_start:.1f} с")

    stats, diffs, n_rows, bad_rows = compare(rows, a.max_diffs)
    print()
    print_report(rows, r1_cols + calib, stats, diffs, n_rows, bad_rows, a.max_diffs)
    if a.json:
        out = {"binlog": a.binlog, "cache": a.cache, "day": day, "rows": len(rows), "flags": dict(sim.flags),
               "stats": {f"{c}|{s}": v for (c, s), v in stats.items()},
               "coverage": coverage(stats, set(r1_cols + calib)), "error": sim.error}
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
