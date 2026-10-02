#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TK-025: независимый пересчёт колонок R1 из сырого бинлога суток и сверка с кэшем подходов.

    python3 tk025-recompute.py <SYM-день.binlog> <approaches-SYM.csv> [--approach-bps 20] [--json out.json]

Что делает. Читает бинлог v3 одних суток монеты (кадры zstd — через ctypes/libzstd.so.1, запасной путь —
внешний `zstd -d -c`), восстанавливает книгу и ленту сделок тем же порядком, что `FileReplayer`/`replay_symbol`
(группа записей с одной `exch_ts_ns` = один кадр книги; сделка закрывает открытую группу и идёт после неё),
находит для каждой строки кэша с `touch_start_ms` тот кадр, где уровень стал лучшей ценой своей стороны
(`ms == touch_start_ms`), и в этот момент пересчитывает колонки R1 ТОЛЬКО по определениям из
`docs/findings/tk025-design-2026-10-02.md` (Rust-код колонок не читался). Сравнивает с клетками кэша
поколоночно, отдельно по сторонам стены (бид/аск — проверка зеркала).

Только стандартная библиотека (на деке нет numpy/zstandard). Время — только из данных бинлога.
Где определение неоднозначно, считается несколько вариантов и они печатаются под разными именами
(`колонка@вариант`): расхождение определения и кода видно как «один вариант совпал, другой нет».
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
# Трактовка vpin, совпавшая с кэшем: размер бара — по обороту за час С учётом текущей сделки (после добавления её в минутное
# кольцо), RPI-сделки входят, бары строятся только после прогрева 60 мин (до него сделки в бары не идут). Остальные 11
# комбинаций (оборот до сделки / на последнем кадре; без RPI; бары с начала суток) печатаются в разделе «альтернативные».
VPIN_MAIN = "trade_in/rpi/warm"

# имена 62 колонок R1 по порядку (из шапки кэша берутся фактически; список — только для отчёта о покрытии)


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


class Row:
    __slots__ = ("line", "s", "price", "t0", "birth", "cells", "done", "side", "jt")

    def __init__(self, line, side, price, t0, birth, cells):
        self.line = line
        self.side = side
        self.s = 0 if side == "bid" else 1
        self.price = price
        self.t0 = t0
        self.birth = birth
        self.cells = cells
        self.done = None
        self.jt = None  # индекс кадра, на котором найдено касание

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


class Sim:
    def __init__(self, rows, approach_bps, atomic=False, fr_req=None):
        self.D = approach_bps
        # frontrun_levels: журнал времён кадров (первый проход) и запросы «число занятых цен строго лучше стены на кадре i»
        # (второй проход): {индекс кадра: [(строка CSV, сторона, тик)]}
        self.frame_ms = array("q")
        self.fr_req = fr_req or {}
        self.fr_cnt = {}
        self.atomic = atomic
        self.book = [{}, {}]  # 0 бид, 1 аск: тик -> лоты
        self.best = [None, None]
        self.synced = False
        self.error = None
        self.start_ms = None
        # открытая группа записей (как FileReplayer)
        self.grp = []
        self.grp_snap = False
        self.grp_ts = 0
        self.has_open = False
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
        # VPIN: определение неоднозначно по трём осям — где берётся оборот за час для размера бара
        # (до/после текущей сделки, на последнем кадре), входят ли RPI-сделки, с какого момента строятся бары
        self.vp = {}
        for anchor in ("trade_out", "trade_in", "frame"):
            for rpi in ("rpi", "norpi"):
                for start in ("all", "warm"):
                    self.vp[f"{anchor}/{rpi}/{start}"] = VpinMachine()
        self.frame_N = 0
        # кадры
        self.n_frames = 0
        self.nonmono_frames = 0
        self.last_frame_ms = None
        self.prev = None  # (bb, bsz, ba, asz) после прошлого прохода (состояние «бид новый, аск старый» тоже)
        self.prev_best = [None, None]
        self.ofi_ms = array("q")
        self.ofi_cum = array("q", [0])
        self.flips = [array("q"), array("q")]
        # стеки «последнее наблюдение, где значение ≥ X / ≤ Y» по чужой лучшей цене (обновляются в проходе своей стороны)
        self.mx_n, self.mx_ms = [], []  # макс-стек по лучшему аску (минус значения)
        self.mn_v, self.mn_ms = [], []  # мин-стек по лучшему биду
        # состояние уровней-стен
        self.rows = rows
        self.watched = {(r.s, r.price) for r in rows}
        self.pending = {}
        for r in rows:
            self.pending.setdefault(r.t0, []).append(r)
        self.tr_all = {}
        self.tr_nr = {}
        self.lvev = {}
        self.results = []  # (row, vals)

    # ---- вход записи
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
            if not rpi:
                self.tr_nr[key] = self.tr_nr.get(key, 0) + lots
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
        """flow_1h_lots, как его считает begin_frame последнего кадра: сделки только до кадра, минуты от мс кадра."""
        mf = self.last_frame_ms // 60000
        n = self.frame_N
        idx = bisect.bisect_left(self.t_ms, (mf - FLOW_WINDOW_MIN + 1) * 60000, 0, n)
        return (self.cb_lots[n] + self.cs_lots[n]) - (self.cb_lots[idx] + self.cs_lots[idx])

    def _vpin(self, ms, m, lots, buy, rpi):
        warm_ok = self.start_ms is not None and ms - self.start_ms >= 3_600_000
        frame_ok = self.last_frame_ms is not None
        for phase in ("out", "in"):
            if phase == "in":
                self.mvol[m] += lots
            f_trade = lambda: self._flow_ring(m)
            for name, vm in self.vp.items():
                anchor, rpi_mode, start = name.split("/")
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

    # ---- кадр книги
    # Кадр обрабатывается двумя проходами, как это делает реплей: сначала бид-половина (чужая сторона в книге —
    # состояние прошлого кадра), затем аск-половина (бид уже новый). Снимок касания берётся внутри прохода своей стороны.
    def flush(self):
        snap = self.grp_snap
        ms = self.grp_ts // 1_000_000
        book = self.book
        best = self.best
        watched = self.watched
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
        pbest = (best[0], best[1])
        self.n_frames += 1
        if self.start_ms is None:
            self.start_ms = ms
        if self.last_frame_ms is not None and ms < self.last_frame_ms:
            self.nonmono_frames += 1
        self.last_frame_ms = ms
        self.frame_ms.append(ms)
        befs = [None, None]
        for s in (0, 1):
            d = book[s]
            bef = {}
            if snap:
                for k in watched:
                    if k[0] == s:
                        bef[k] = d.get(k[1], 0)
                d.clear()
                best[s] = None
            need = False
            for px, q in recs[s]:
                if not snap:
                    k = (s, px)
                    if k in watched and k not in bef:
                        bef[k] = d.get(px, 0)
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
            befs[s] = bef
            if not self.atomic:
                self.after_pass(s, ms, bef, pbest)
        if self.atomic:  # контрольный режим: весь кадр применён целиком, затем проходы
            for s in (0, 1):
                self.after_pass(s, ms, befs[s], pbest)
        self.frame_N = len(self.t_ms)
        bb, ba = best
        if bb is not None and ba is not None and bb >= ba:
            self.error = f"книга пересеклась на {ms}"
            return False
        return True

    def after_pass(self, s, ms, bef, pbest):
        book = self.book
        best = self.best
        if self.fr_req:
            reqs = self.fr_req.get(self.n_frames - 1)
            if reqs:
                dd = book[s]
                for line, rs, tick in reqs:
                    if rs == s:
                        # занятых цен строго МЕЖДУ лучшей ценой стороны и стеной: лучше стены минус сама лучшая цена
                        n_better = sum(1 for p in dd if (p > tick if s == 0 else p < tick))
                        self.fr_cnt[line] = n_better - 1 if n_better > 0 else 0
        # отмены на ценах стен этой стороны
        if bef:
            tr_all = self.tr_all
            tr_nr = self.tr_nr
            d = book[s]
            for k, b0 in bef.items():
                drop = b0 - d.get(k[1], 0)
                if drop > 0:
                    ca = max(0, drop - tr_all.get(k, 0))
                    cb = max(0, drop - tr_nr.get(k, 0))
                    if ca or cb:
                        self.lvev.setdefault(k, []).append((ms, ca, cb))
        if s == 1 and self.tr_all:
            self.tr_all.clear()
            self.tr_nr.clear()
        # OFI по лучшим уровням (переход = один проход begin_frame)
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
        # смены лучшей цены своей стороны
        if best[s] is not None and pbest[s] is not None and best[s] != pbest[s]:
            self.flips[s].append(ms)
        # since_far: значение чужой лучшей цены, как её видит этот проход
        opp = best[1 - s]
        if opp is not None:
            if s == 0:  # у бид-стены чужая — аск; хранится со знаком минус (стек суффиксных минимумов)
                v, st, sm = -opp, self.mx_n, self.mx_ms
            else:
                v, st, sm = opp, self.mn_v, self.mn_ms
            while st and st[-1] >= v:
                st.pop()
                sm.pop()
            st.append(v)
            sm.append(ms)
        # касания, стартующие в этом проходе
        pend = self.pending.get(ms)
        if pend:
            for row in list(pend):
                if row.s == s and best[s] == row.price and pbest[s] != row.price:
                    row.jt = self.n_frames - 1
                    self.results.append((row, self.snapshot(row, ms)))
                    pend.remove(row)
                    row.done = True

    # ---- пересчёт колонок на кадре касания
    def tape_window(self, lo_ms):
        idx = bisect.bisect_left(self.t_ms, lo_ms)
        N = len(self.t_ms)
        bl = self.cb_lots[N] - self.cb_lots[idx]
        sl = self.cs_lots[N] - self.cs_lots[idx]
        bn = self.cb_n[N] - self.cb_n[idx]
        sn = self.cs_n[N] - self.cs_n[idx]
        pp = self.cum_pp[N] - self.cum_pp[idx]
        pn = self.cum_pn[N] - self.cum_pn[idx]
        return bl, sl, bn, sn, pp, pn, idx, N

    def top_sum(self, s, n):
        d = self.book[s]
        if s == 0:
            return sum(d[k] for k in heapq.nlargest(n, d))
        return sum(d[k] for k in heapq.nsmallest(n, d))

    def snapshot(self, row, t0):
        s = row.s
        o = {}
        s0 = t0 // 1000
        m0 = t0 // 60000
        warm = t0 - self.start_ms
        UND = None
        # лента
        tw = {}
        for W in (15, 30, 60):
            if warm >= W * 1000:
                bl, sl, bn, sn, pp, pn, idx, N = self.tape_window((s0 - W + 1) * 1000)
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
                    o[f"sign_ac_{W}s_bp"] = tdiv(pp * 10000, pn) if pn > 0 else UND
                if W == 30:
                    o["tape_press_avg_30s_e2"] = tdiv(pl * 100, pnn) if pnn > 0 else UND
                    o["tape_with_avg_30s_e2"] = tdiv(wl * 100, wn) if wn > 0 else UND
        if warm >= 3_600_000:
            bl, sl, _, _, _, _, _, _ = self.tape_window((m0 - FLOW_WINDOW_MIN + 1) * 60000)
            p60, w60 = (sl, bl) if s == 0 else (bl, sl)
            o["tape_press_60m_lots"] = p60
            o["tape_with_60m_lots"] = w60
            for W in (15, 30):
                if W in tw:
                    bl2, sl2 = tw[W][0], tw[W][1]
                    pw, ww = (sl2, bl2) if s == 0 else (bl2, sl2)
                    o[f"tape_burst_press_{W}s_bp"] = tdiv(pw * 3600 * 10000, W * p60) if p60 > 0 else UND
                    o[f"tape_burst_with_{W}s_bp"] = tdiv(ww * 3600 * 10000, W * w60) if w60 > 0 else UND
            for name, vm in self.vp.items():
                if name == VPIN_MAIN:
                    o["vpin_bp"] = vm.value()
                else:
                    o[f"alt:vpin_bp@{name}"] = vm.value()
        # размер сделки за 15 мин
        if warm >= 15 * 60000:
            idx = bisect.bisect_left(self.t_ms, (m0 - 14) * 60000)
            cnt = Counter(self.kb[idx:])
            n = sum(cnt.values())
            for pc in (50, 90):
                if n == 0:
                    o[f"trade_size_p{pc}_15m"] = UND
                else:
                    target = -(-pc * n // 100)
                    acc = 0
                    val = UND
                    for k in sorted(cnt):
                        acc += cnt[k]
                        if acc >= target:
                            val = 1 << k
                            break
                    o[f"trade_size_p{pc}_15m"] = val
        # книга
        for N in (1, 5, 10, 50):
            own = self.top_sum(s, N)
            opp = self.top_sum(1 - s, N)
            o[f"obi{N}_bp"] = tdiv((own - opp) * 10000, own + opp) if own + opp > 0 else UND
        bb, ba = self.best
        if bb is not None and ba is not None:
            bsz = self.book[0][bb]
            asz = self.book[1][ba]
            num = (ba - bb) * (bsz - asz) * 1_000_000
            den = (bsz + asz) * (bb + ba)
            v = tdiv(num, den)
            o["micro_off_cbps"] = v if s == 0 else -v
        sg = 1 if s == 0 else -1
        for W in (10, 60):
            if warm >= W * 1000:
                i0 = bisect.bisect_left(self.ofi_ms, (s0 - W + 1) * 1000)
                o[f"ofi_{W}s_lots"] = sg * (self.ofi_cum[-1] - self.ofi_cum[i0])
        for W in (15, 60):
            if warm >= W * 1000:
                o[f"best_flips_{W}s"] = len(self.flips[s]) - bisect.bisect_left(self.flips[s], (s0 - W + 1) * 1000)
        # состояние уровня
        key = (s, row.price)
        ev = self.lvev.get(key, ())
        birth = row.birth
        for sfx, ix in (("a", 1), ("b", 2)):
            pre = "" if sfx == "a" else "alt:"
            post = "" if sfx == "a" else "@без-RPI-в-вычитании"
            for W in (1, 3):
                lo = max((s0 - W + 1) * 1000, birth + 1)  # переход кадра рождения не считается (уровня до него не было)
                o[f"{pre}cancel_{W}s_lots{post}"] = sum(e[ix] for e in ev if e[0] >= lo) if warm >= W * 1000 else UND
            o[f"{pre}cancel_life_lots{post}"] = sum(e[ix] for e in ev if e[0] > birth)
            if t0 - birth >= 3_600_000 and warm >= 3_600_000:
                lo = max((m0 - FLOW_WINDOW_MIN + 1) * 60000, birth + 1)
                o[f"{pre}cancel_60m_lots{post}"] = sum(e[ix] for e in ev if e[0] >= lo)
            else:
                o[f"{pre}cancel_60m_lots{post}"] = UND
        # форма стека у лучшей цены
        d = self.book[s]
        wall = d.get(row.price, 0)
        if s == 0:
            ticks = range(row.price - 14, row.price + 1)
        else:
            ticks = range(row.price, row.price + 15)
        tot = 0
        nz = 0
        for t in ticks:
            z = d.get(t, 0)
            tot += z
            if z:
                nz += 1
        o["size_share15_bp"] = tdiv(wall * 10000, tot) if tot > 0 else UND
        o["nz_levels15"] = nz
        # чужая сторона: ближайший уровень и «лесенка» перед стеной (чужая — как её видит проход этой стороны)
        opp_best = self.best[1 - s]
        if opp_best is not None:
            o["alt:opp_wall_dist_cbps@лучшая-цена-чужой-стороны"] = abs(opp_best - row.price) * 1_000_000 // row.price
            o["alt:opp_wall_ratio_bp@лучшая-цена-чужой-стороны"] = tdiv(self.book[1 - s][opp_best] * 10000, wall) if wall > 0 else UND
        own_best = self.best[s]
        lo_t, hi_t = (row.price + 1, own_best) if s == 0 else (own_best, row.price - 1)
        o["alt:frontrun_levels@строго-между-на-кадре-касания"] = sum(1 for t in range(lo_t, hi_t + 1) if d.get(t, 0)) if own_best is not None else UND
        # since_far: последнее наблюдение (проход) стороны, где чужая лучшая цена дальше 2D от стены
        thr = (2 * self.D * row.price) // 10000 + 1
        if s == 0:
            c = bisect.bisect_right(self.mx_n, -(row.price + thr))
            last = self.mx_ms[c - 1] if c else None
        else:
            c = bisect.bisect_right(self.mn_v, row.price - thr)
            last = self.mn_ms[c - 1] if c else None
        o["since_far_ms"] = t0 - (max(last, birth) if last is not None else birth)
        return o


# --------------------------------------------------------------------------- сверка


def parse_cell(x):
    if x is None or x == "":
        return None
    try:
        return int(x)
    except ValueError:
        return int(float(x))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("binlog")
    ap.add_argument("cache")
    ap.add_argument("--approach-bps", type=int, default=20)
    ap.add_argument("--json", default=None)
    ap.add_argument("--atomic-frame", action="store_true",
                    help="контроль: снимок книги на полностью применённом кадре (а не «бид-проход видит аск прошлого кадра»)")
    ap.add_argument("--max-diffs", type=int, default=5)
    a = ap.parse_args()
    t_start = time.time()

    with open(a.cache, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        hdr = rd.fieldnames
        r1_start = hdr.index("tape_press_lots_15s")
        r1_cols = hdr[r1_start:]
        rows = []
        n_all = 0
        n_touch = 0
        for i, rec in enumerate(rd):
            n_all += 1
            if rec["touch_start_ms"] == "":
                continue
            n_touch += 1
            rows.append(Row(i + 2, rec["side"], int(rec["price_tick"]), int(rec["touch_start_ms"]),
                            int(rec["birth_ms"]), {c: rec[c] for c in r1_cols}))
    print(f"кэш {a.cache}: строк {n_all}, с касанием {n_touch}, колонок R1 {len(r1_cols)}")

    def replay(sm):
        n = 0
        good = True
        for frame in iter_frames(a.binlog):
            n += len(frame)
            rec = sm.record
            for ev, ts, px, q, at in frame:
                if not rec(ev, ts, px, q, at):
                    good = False
                    break
            if not good:
                break
        if good and sm.has_open:
            sm.flush()
        return n, good

    sim = Sim(rows, a.approach_bps, atomic=a.atomic_frame)
    n_rec, ok = replay(sim)
    print(f"бинлог {a.binlog}: записей {n_rec}, кадров книги {sim.n_frames}, сделок (не блок) {len(sim.t_ms)}, "
          f"блочных {sim.n_block}, RPI {sim.n_rpi}; не монотонных сделок {sim.nonmono_trades}, кадров {sim.nonmono_frames}; "
          f"ошибка={sim.error}; {time.time() - t_start:.1f} с")
    unmatched = [r for r in rows if r.done is None]
    print(f"касаний найдено в потоке {len(sim.results)} из {len(rows)}; не найдено {len(unmatched)}")
    for r in unmatched[:5]:
        print(f"  не найдено: строка {r.line} t0={r.t0} {r.side} {r.price}")

    # frontrun_levels: число занятых цен строго лучше стены на «кадре за 1–2 с до касания» (кадр определяется слотами выборки
    # от рождения уровня: новый слот — на первом кадре, отстоящем от начала слота на ≥ 1 с; читается последнее наблюдение
    # предыдущего слота, если оно не позже t0 − 1 с, иначе первое). Времена кадров известны после первого прохода,
    # книга на нужном кадре — вторым проходом без лент и снимков.
    if sim.results and not sim.error and not a.atomic_frame:
        fm = sim.frame_ms
        by_j0 = {}
        for row, _ in sim.results:
            by_j0.setdefault(bisect.bisect_left(fm, row.birth), []).append(row)
        req = {}
        for j0, rs in by_j0.items():
            jmax = max(r.jt for r in rs)
            starts = [j0]
            cur = j0
            while True:
                nxt = bisect.bisect_left(fm, fm[cur] + 1000, cur + 1, jmax + 1)
                if nxt > jmax:
                    break
                starts.append(nxt)
                cur = nxt
            for r in rs:
                k = bisect.bisect_right(starts, r.jt) - 1
                if k == 0:
                    tgt = starts[0]
                else:
                    last = starts[k] - 1
                    tgt = last if fm[last] + 1000 <= r.t0 else starts[k - 1]
                req.setdefault(tgt, []).append((r.line, r.s, r.price))
        sim2 = Sim([], a.approach_bps, fr_req=req)
        replay(sim2)
        for row, vals in sim.results:
            vals["frontrun_levels"] = sim2.fr_cnt.get(row.line)
        print(f"frontrun_levels: второй проход, запросов кадров {len(req)}, получено {len(sim2.fr_cnt)} из {len(sim.results)}; "
              f"{time.time() - t_start:.1f} с")

    # поколоночная сверка
    stats = {}  # (колонка, сторона) -> счётчики
    diffs = {}

    def st(col, side):
        return stats.setdefault((col, side), {"n": 0, "eq": 0, "ne": 0, "both_undef": 0, "py_undef": 0, "cache_undef": 0})

    for row, vals in sim.results:
        for col, py in vals.items():
            base = col.split("@")[0]
            if base.startswith("alt:"):
                base = base[4:]
            if base not in row.cells:
                continue
            cv = parse_cell(row.cells[base])
            s = st(col, row.side)
            s["n"] += 1
            if py is None and cv is None:
                s["both_undef"] += 1
            elif py is None:
                s["py_undef"] += 1
                diffs.setdefault((col, row.side), []).append((row.line, row.t0, row.price, py, cv))
            elif cv is None:
                s["cache_undef"] += 1
                diffs.setdefault((col, row.side), []).append((row.line, row.t0, row.price, py, cv))
            elif py == cv:
                s["eq"] += 1
            else:
                s["ne"] += 1
                diffs.setdefault((col, row.side), []).append((row.line, row.t0, row.price, py, cv))
    def colkey(c):
        alt = c.startswith("alt:")
        b = c[4:] if alt else c
        return (alt, r1_cols.index(b.split("@")[0]), c)
    cols = sorted({c for c, _ in stats}, key=colkey)
    print()
    hdr_line = f"{'колонка':58} {'сторона':5} {'сравн':>6} {'равно':>6} {'расх':>5} {'обе-пусто':>9} {'py-пусто':>8} {'кэш-пусто':>9}"
    main_cols = [c for c in cols if not c.startswith("alt:")]
    alt_cols = [c for c in cols if c.startswith("alt:")]
    full_ok = []  # колонки, где есть хоть одно содержательное сравнение и ни одного расхождения (обе стороны)
    for title, group in (("ОСНОВНЫЕ КОЛОНКИ", main_cols),
                         ("АЛЬТЕРНАТИВНЫЕ ТРАКТОВКИ / не сверяемое из бинлога (для разбора неоднозначностей)", alt_cols)):
        if not group:
            continue
        print(title)
        print(hdr_line)
        for c in group:
            tot_eq = tot_bad = 0
            for side in ("bid", "ask"):
                s = stats.get((c, side))
                if s:
                    print(f"{c:58} {side:5} {s['n']:6} {s['eq']:6} {s['ne']:5} {s['both_undef']:9} {s['py_undef']:8} {s['cache_undef']:9}")
                    tot_eq += s["eq"]
                    tot_bad += s["ne"] + s["py_undef"] + s["cache_undef"]
            if group is main_cols and tot_eq > 0 and tot_bad == 0:
                full_ok.append(c)
        print()
    print(f"колонок без единого расхождения (основные, есть сравнения значений): {len(full_ok)}")
    print("   " + ", ".join(full_ok))
    only_undef = [c for c in main_cols if all(stats.get((c, sd), {"eq": 0, "ne": 0, "py_undef": 0, "cache_undef": 0})["eq"] == 0
                                              and stats.get((c, sd), {"ne": 0})["ne"] == 0 for sd in ("bid", "ask"))]
    if only_undef:
        print("колонки, где сравнение пришлось только на «обе пусто» (не информативны на этих сутках): " + ", ".join(only_undef))
    print()
    for c in cols:
        lim = a.max_diffs if not c.startswith("alt:") else 2
        for side in ("bid", "ask"):
            d = diffs.get((c, side))
            if d:
                print(f"расхождения {c} [{side}] — первые {min(len(d), lim)} из {len(d)}  (строка CSV, t0, цена, python, кэш):")
                for x in d[:lim]:
                    print("   ", x)
    if a.json:
        out = {"binlog": a.binlog, "cache": a.cache, "rows_touch": len(rows), "found": len(sim.results),
               "stats": {f"{c}|{s}": v for (c, s), v in stats.items()}}
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
