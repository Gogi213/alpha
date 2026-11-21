#!/usr/bin/env python3
"""П-42 §13.66 (TK-666): признаки входа F (фандинг Binance), P (премия `premiumIndexKlines`), R (круглое число), C (уровень слит на 60 и 240 мин) на кандидатах баз h775. НЕ читает P&L.
  python3 tk666_feat.py need               — нужные монето-месяцы фандинга и монето-сутки премии по ключам кандидатов (sigsx баз) → need-*.tsv
  python3 tk666_feat.py dl [--par 24]      — докачка месячных zip фандинга 07–10 и суточных zip премии (curl -Z, sha256 по .CHECKSUM; готовые пропускаются) → miss.tsv
  python3 tk666_feat.py rest [--pause 1.1] — REST `fapi/v1/fundingRate` за 01–13.11 по монетам с кандидатами ноября (≤ 1 запрос/с) → funding/<СИМВОЛ>/rest-2026-11.json
  python3 tk666_feat.py build              — таблицы признаков `feat/<окно>-<сторона>.csv` (нужны дампы уровней `levels/…`: tk666_run.py levels)
Дни ≥ 14.11 не запрашиваются и не читаются (охрана `check_ms`). Определения — протокол §13.66 п. 3 (PS-936)."""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tk604_stats as st  # noqa: E402

ROOT = "/data/p42-tk666"
BASE_DIR = "/data/p42-tk625/h775/base"
FUND_OLD, PREM_OLD = "/data/p42-metrics/funding", "/data/p42-premium"
FUND_NEW, PREM_NEW = os.path.join(ROOT, "funding"), os.path.join(ROOT, "premium")
VID = {"L": "L_base", "S": "S_base"}
ENG = {"L": "long", "S": "short"}
VBASE = "https://data.binance.vision/data/futures/um"
REST = "https://fapi.binance.com/fapi/v1/fundingRate"
MONTHS = ("2026-07", "2026-08", "2026-09", "2026-10")
LAST_MS = int(__import__("datetime").datetime(2026, 11, 14, tzinfo=__import__("datetime").timezone.utc).timestamp() * 1000)      # первая мс 14.11: позже читать нельзя
NOV_MS = int(__import__("datetime").datetime(2026, 11, 1, tzinfo=__import__("datetime").timezone.utc).timestamp() * 1000)
PREM_MAX_AGE_S = 300                              # п. 3: свеча старше 5 мин → пусто
FUND_GAP_X = 2.0                                  # п. 3: последняя выплата старше 2 интервалов → пусто
TAUS = (0.001, 0.002, 0.004)                      # п. 3: τ_c 0,1 / 0,2 / 0,4 %
INTERVALS_H = (1.0, 2.0, 4.0, 8.0)
FEAT_COLS = ["f8", "f_age_h", "prem", "prem_age_s", "conf01", "conf02", "conf04", "rho", "mant", "lvl_px"]


def say(*a):
    print(*a, flush=True)


def q(*a):
    p = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def check_ms(ms):
    if int(ms) >= LAST_MS:
        raise SystemExit("охрана: время %d ≥ 14.11 — не читается (проверка вперёд)" % int(ms))


def sigsx_path(wname, side):
    return os.path.join(BASE_DIR, wname, VID[side], ENG[side] + "-sigsx.csv")


def read_sigsx(wname, side):
    """Строки дампа кандидатов базы: sym, sig_ms (мс от T0 окна), level_px_e8, natr_e8, born, first_bar — в порядке файла."""
    sym, sig, px, nat, born, fb = [], [], [], [], [], []
    with open(sigsx_path(wname, side), encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            sym.append(r["sym"])
            sig.append(int(r["sig_ms"]))
            px.append(int(r["level_px_e8"]))
            nat.append(int(r["natr_e8"]))
            born.append(int(r["born"]))
            fb.append(int(r["first_bar"]))
    return dict(sym=sym, sig=np.array(sig, dtype=np.int64), px=np.array(px, dtype=np.int64), natr=np.array(nat, dtype=np.int64),
                born=np.array(born, dtype=np.int64), first_bar=np.array(fb, dtype=np.int64))


def windows():
    return st.TRADE_WINDOWS


def abs_ms(w, sig):
    return w.t0_ms + np.asarray(sig, dtype=np.int64)


def day_of(ms):
    import datetime as dt
    return dt.datetime.fromtimestamp(int(ms) / 1000.0, dt.timezone.utc).date().isoformat()


def month_of(ms):
    return day_of(ms)[:7]


# ---- R: близость к круглому числу (п. 3; целая арифметика e8) -------------------------------------------------------------------

def round_feat(px_e8, natr_e8):
    """(ρ, мантисса): шаг s_r = 10^(⌊log10 p⌋ − 1); ρ = |p − s_r·round(p / s_r)| / (natr·p); мантисса = p / 10^⌊log10 p⌋ ∈ [1; 10). Знак цены не важен (|px|)."""
    p = abs(int(px_e8))
    n = int(natr_e8)
    digits = len(str(p))
    if p <= 0 or digits < 2 or n <= 0:
        return float("nan"), float("nan")
    step = 10 ** (digits - 2)
    k = (p + step // 2) // step
    dist = abs(p - k * step)
    return dist * 1e8 / (n * p), p / 10 ** (digits - 1)


# ---- F: фандинг ----------------------------------------------------------------------------------------------------------------

def interval_round(h):
    return min(INTERVALS_H, key=lambda x: abs(x - h))


def _read_funding_zip(path):
    out = []
    with zipfile.ZipFile(path) as z:
        with z.open(z.namelist()[0]) as f:
            for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")):
                out.append((int(row["calc_time"]), float(row["funding_interval_hours"]), float(row["last_funding_rate"])))
    return out


def funding_zip_path(sym, month):
    for root in (FUND_OLD, FUND_NEW):
        p = os.path.join(root, sym, "%s-fundingRate-%s.zip" % (sym, month))
        if os.path.exists(p):
            return p
    return None


def load_funding(sym):
    """(времена мс по возрастанию, ставка к 8 ч, интервал ч): месячные архивы 07–10 + REST ноября (интервал — расстояние до предыдущей выплаты, округлённое к {1,2,4,8})."""
    rows = []
    for m in MONTHS:
        p = funding_zip_path(sym, m)
        if p:
            rows += [(t, h, r) for t, h, r in _read_funding_zip(p)]
    rp = os.path.join(FUND_NEW, sym, "rest-2026-11.json")
    if os.path.exists(rp):
        rest = sorted(json.load(open(rp, encoding="utf-8")), key=lambda x: int(x["fundingTime"]))
        for x in rest:
            t = int(x["fundingTime"])
            check_ms(t)
            rows.append((t, None, float(x["fundingRate"])))
    rows.sort(key=lambda r: r[0])
    t = np.array([r[0] for r in rows], dtype=np.int64)
    inter = np.zeros(len(rows))
    for i, (ti, h, _) in enumerate(rows):
        if h is not None:
            inter[i] = h
        elif i > 0:
            inter[i] = interval_round((ti - rows[i - 1][0]) / 3.6e6)
        else:
            inter[i] = 8.0
    rate8 = np.array([r[2] for r in rows]) * 8.0 / np.where(inter > 0, inter, 8.0)
    return t, rate8, inter


def funding_at(t, rate8, inter, ms):
    """(f8, возраст последней выплаты, ч) на момент `ms`: последняя выплата с calc_time ≤ ms; нет — NaN; старше 2 интервалов — NaN."""
    if len(t) == 0:
        return float("nan"), float("nan")
    i = int(np.searchsorted(t, ms, side="right")) - 1
    if i < 0:
        return float("nan"), float("nan")
    age_h = (ms - int(t[i])) / 3.6e6
    if age_h > FUND_GAP_X * inter[i]:
        return float("nan"), age_h
    return float(rate8[i]), age_h


# ---- P: премия ------------------------------------------------------------------------------------------------------------------

def prem_zip_path(sym, day):
    for root in (PREM_OLD, PREM_NEW):
        p = os.path.join(root, sym, "%s-1m-%s.zip" % (sym, day))
        if os.path.exists(p):
            return p
    return None


def read_prem_zip(path):
    """{open_ms: close}; единицы времени определяются по порядку (µs → мс)."""
    out = {}
    with zipfile.ZipFile(path) as z:
        with z.open(z.namelist()[0]) as f:
            for r in csv.reader(io.TextIOWrapper(f, encoding="utf-8")):
                if not r or not r[0][:1].isdigit():
                    continue
                t = int(r[0])
                if t > 10 ** 14:
                    t //= 1000
                out[t] = float(r[4])
    return out


class Prem:
    def __init__(self):
        self.cache = {}

    def day(self, sym, day):
        k = (sym, day)
        if k not in self.cache:
            p = prem_zip_path(sym, day)
            self.cache[k] = read_prem_zip(p) if p else None
        return self.cache[k]

    def at(self, sym, ms):
        """(close последней закрытой минутной свечи с open_time + 60 с ≤ ms, её возраст в с): ищем назад до PREM_MAX_AGE_S; нет — NaN."""
        check_ms(ms)
        target = (int(ms) - 60_000) // 60_000 * 60_000
        for k in range(PREM_MAX_AGE_S // 60 + 1):
            o = target - 60_000 * k
            d = self.day(sym, day_of(o))
            if d is not None and o in d:
                return d[o], (int(ms) - (o + 60_000)) / 1000.0
        return float("nan"), float("nan")


# ---- C: слияние уровня (дамп уровней движка) ------------------------------------------------------------------------------------

def read_levels(path):
    """{монета: [(born, end, [минуты], [цены|e8])]}: цены по версиям — модуль (в дампе `mean.abs()`)."""
    out = {}
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            segs = [x.split(":") for x in r["segs"].split("|") if x]
            out.setdefault(r["sym"], []).append((int(r["born"]), int(r["end"]), [int(a) for a, _ in segs], [abs(int(b)) for _, b in segs]))
    return out


def level_px_at(lv, m):
    """Цена уровня на минуте m — версия с временем ≤ m (причинно); уровень не живой (m < born или m ≥ end) — None."""
    born, end, mins, pxs = lv
    if m < born or m >= end:
        return None
    i = int(np.searchsorted(np.array(mins), m, side="right")) - 1
    return pxs[i] if i >= 0 else None


def confluence(levels, sym, m, p60, taus=TAUS):
    """conf_τ (0 / 1) для τ из `taus`: у монеты есть живой на минуте m 240-минутный уровень, цена которого в пределах τ от p60 (|P240 − P60| ≤ τ·P60). Нет уровней монеты — 0."""
    out = [0.0] * len(taus)
    p60 = abs(int(p60))
    for lv in levels.get(sym, ()):
        px = level_px_at(lv, m)
        if px is None:
            continue
        for j, tau in enumerate(taus):
            if abs(px - p60) <= tau * p60:
                out[j] = 1.0
    return out


def levels_path(kind, wname, side):
    """kind = t240 | t60 (контроль)."""
    return os.path.join(ROOT, "levels", wname, "%s_%s" % (side, kind), ENG[side] + "-levels.csv")


# ---- команды -------------------------------------------------------------------------------------------------------------------

def all_rows():
    for w in windows():
        for side in ("L", "S"):
            yield w, side, read_sigsx(w.name, side)


def cmd_need(a):
    fund, prem, nov = set(), set(), set()
    n = 0
    for w, side, r in all_rows():
        ms = abs_ms(w, r["sig"])
        for s_, t in zip(r["sym"], ms):
            check_ms(t)
            n += 1
            mo = month_of(t)
            if mo in MONTHS:
                fund.add((s_, mo))
            else:
                nov.add(s_)
            for x in (int(t), int(t) - 60_000 - PREM_MAX_AGE_S * 1000):
                prem.add((s_, day_of(x)))
    fund_todo = sorted(p for p in fund if funding_zip_path(*p) is None)
    prem_todo = sorted(p for p in prem if prem_zip_path(*p) is None)
    for name, rows in (("need-fund.tsv", sorted(fund)), ("need-prem.tsv", sorted(prem)), ("need-nov.tsv", sorted((s_,) for s_ in nov)),
                       ("todo-fund.tsv", fund_todo), ("todo-prem.tsv", prem_todo)):
        with open(q(name), "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write("\t".join(r) + "\n")
    say("need: строк кандидатов %d; фандинг монето-месяцев %d (докачать %d), монет с ноябрём (REST) %d; премия монето-суток %d (докачать %d)" % (
        n, len(fund), len(fund_todo), len(nov), len(prem), len(prem_todo)))


def cmd_dl(a):
    import dl_batch as db
    tasks = []
    for line in open(q("todo-fund.tsv"), encoding="utf-8"):
        s_, mo = line.rstrip("\n").split("\t")
        tasks.append(("funding", s_, mo, "%s/monthly/fundingRate/%s/%s-fundingRate-%s.zip" % (VBASE, s_, s_, mo),
                      os.path.join(FUND_NEW, s_, "%s-fundingRate-%s.zip" % (s_, mo)), ROOT))
    for line in open(q("todo-prem.tsv"), encoding="utf-8"):
        s_, d = line.rstrip("\n").split("\t")
        if d >= "2026-11-14":
            raise SystemExit("охрана: сутки %s ≥ 14.11" % d)
        tasks.append(("premium", s_, d, "%s/daily/premiumIndexKlines/%s/1m/%s-1m-%s.zip" % (VBASE, s_, s_, d),
                      os.path.join(PREM_NEW, s_, "%s-1m-%s.zip" % (s_, d)), ROOT))
    say("задач %d" % len(tasks))
    ok = miss = 0
    for i in range(0, len(tasks), db.CHUNK):
        o, m = db.run_chunk(tasks[i:i + db.CHUNK], a.par)
        ok += o
        miss += m
        say("%d/%d ok %d miss %d" % (min(i + db.CHUNK, len(tasks)), len(tasks), ok, miss))
    return 0


def cmd_rest(a):
    syms = [l.strip() for l in open(q("need-nov.tsv"), encoding="utf-8") if l.strip()]
    start, end = NOV_MS, LAST_MS - 1
    got = fail = 0
    for s_ in syms:
        p = os.path.join(FUND_NEW, s_, "rest-2026-11.json")
        if os.path.exists(p):
            continue
        url = "%s?symbol=%s&startTime=%d&endTime=%d&limit=1000" % (REST, s_, start, end)
        data = None
        for k in range(3):
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    data = json.loads(r.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                if e.code in (418, 429):
                    raise SystemExit("REST %d для %s — стоп (лимит IP)" % (e.code, s_))
                if e.code == 400:
                    data = []
                    break
            except Exception:
                time.sleep(2)
            time.sleep(a.pause)
        if data is None:
            fail += 1
            say("REST: нет ответа для", s_)
        else:
            for x in data:
                check_ms(int(x["fundingTime"]))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            json.dump(data, open(p, "w", encoding="utf-8"))
            got += 1
        time.sleep(a.pause)
    say("rest: получено %d, без ответа %d" % (got, fail))
    return 1 if fail else 0


def build_one(w, side, funding_cache, prem, lv240):
    r = read_sigsx(w.name, side)
    ms = abs_ms(w, r["sig"])
    n = len(r["sym"])
    out = {k: np.full(n, np.nan) for k in FEAT_COLS}
    for i in range(n):
        s_, t = r["sym"][i], int(ms[i])
        check_ms(t)
        if s_ not in funding_cache:
            funding_cache[s_] = load_funding(s_)
        f8, age = funding_at(*funding_cache[s_], t)
        out["f8"][i], out["f_age_h"][i] = f8, age
        out["prem"][i], out["prem_age_s"][i] = prem.at(s_, t)
        out["rho"][i], out["mant"][i] = round_feat(r["px"][i], r["natr"][i])
        out["lvl_px"][i] = abs(int(r["px"][i]))
        if lv240 is not None:
            c = confluence(lv240, s_, int(r["sig"][i]) // 60_000, r["px"][i])
            out["conf01"][i], out["conf02"][i], out["conf04"][i] = c
    return r, out


def write_table(path, r, cols):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("sym,sig_ms," + ",".join(FEAT_COLS) + "\n")
        for i in range(len(r["sym"])):
            f.write("%s,%d,%s\n" % (r["sym"][i], int(r["sig"][i]), ",".join("" if not np.isfinite(cols[c][i]) else repr(float(cols[c][i])) for c in FEAT_COLS)))


def cmd_build(a):
    funding_cache, prem, info = {}, Prem(), {}
    for w in windows():
        for side in ("L", "S"):
            p = levels_path("t240", w.name, side)
            lv = read_levels(p) if os.path.exists(p) else None
            r, cols = build_one(w, side, funding_cache, prem, lv)
            path = q("feat", "%s-%s.csv" % (w.name, side))
            write_table(path, r, cols)
            info["%s-%s" % (w.name, side)] = dict(rows=len(r["sym"]), defined={c: int(np.isfinite(cols[c]).sum()) for c in FEAT_COLS}, sha256=st.sha256_file(path))
            say("build %s-%s: строк %d, определено %s" % (w.name, side, len(r["sym"]), info["%s-%s" % (w.name, side)]["defined"]))
    json.dump(info, open(q("feat", "info.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("need", "dl", "rest", "build"))
    ap.add_argument("--par", type=int, default=24)
    ap.add_argument("--pause", type=float, default=1.1)
    a = ap.parse_args()
    return {"need": cmd_need, "dl": cmd_dl, "rest": cmd_rest, "build": cmd_build}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
