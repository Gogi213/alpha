"""Фильтры «фальшивых» эпизодов arb-holding: к таблице hold_table добавляются булевы флаги причин и итоговый «слой снятия» (первая сработавшая причина по порядку FILTERS).

Флаги (независимые; слой — по порядку):
  gap     — эпизод ± 2 мин пересекает пропуск сбора биржи любой из ног (gaps_scan ≥ 15 с, dead.json), обрыв Binance 15.11 18:12:18–18:14:20 UTC, обрыв пути 16.11 12:50–12:53 (UTC), или
            участок аудита collect-gap по символу ноги (границы −60 с / +120 с, как в проверке arb-cross-exit);
  stale   — любая нога молчала > 5 с в какой-то момент эпизода (слово владельца «нога устарела > 5 с»): по сырым записям книги, включая смены размера (hold_raw.py); stale_price — буквально «лучшая цена не менялась > 5 с»; stale60 — цена не менялась > 60 с;
  contract— (от нуля) медиана разрыва пары за окно ≥ θ/2: пара весь период стоит со сдвигом (другой контракт: множитель, одноимённая другая монета, индекс), эпизод — не событие, а уровень;
  tradfi  — биржа любой ноги относит символ к акциям / ETF / сырью / форексу / индексу И момент начала вне сессии (акции, ETF: вне пн–пт 14:30–21:00 UTC; сырьё, форекс, индекс: сб–вс);
  delist  — статус любой ноги «делистинг» (Binance SETTLING и поставка ≤ 3 суток от эпизода; Gate in_delisting; Bybit/Binance поставка раньше эпизода), пред-листинг;
  funding — в [начало − 2 мин; конец + 2 мин] у любой ноги начисление фандинга (по записям mark: смена времени T);
  mark    — mark-цены двух бирж не подтверждают разрыв: side · (mark_A / mark_B − 1 − база) < θ/2 (база — 0 или центр);
  trend   — (от центра) медиана середины за 10 мин до эпизода уже ушла от центра в сторону эпизода на ≥ θ/2: центр отстаёт от движущейся середины.
Отдельно, не снимается: bingx — любая нога BingX (книга — снимки ≈ 2,6/с, метки на мс-масштабе не читаются).
Запуск: python3 hold_filter.py <table.pkl> <symstatus.json> <dead.json> <audit_out_dir> <marks.pkl|-> <raw.pkl|-> <выход.pkl>; окна для hold_raw — «hold_filter.py need <flagged.pkl> <need.pkl>»
"""
import csv
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from hold_common import EX5, STALE_MS, all_meta, norm5  # noqa: E402

FILTERS = ("gap", "stale", "contract", "tradfi", "delist", "funding", "mark", "trend")
GAP_MARGIN_MS = 120_000
FUND_MARGIN_MS = 120_000
OUTAGES = {
    "binance": [(datetime(2026, 11, 15, 18, 12, 18, tzinfo=timezone.utc), datetime(2026, 11, 15, 18, 14, 20, tzinfo=timezone.utc))],
    "*": [(datetime(2026, 11, 16, 12, 50, tzinfo=timezone.utc), datetime(2026, 11, 16, 12, 53, tzinfo=timezone.utc))],      # обрыв пути 16.11 (владелец не уточнил пояс; берём UTC и проверяем по dead.json)
}


def utc_ms(s):
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)


def overlaps(starts, ends, a, b):
    """Для интервалов [a, b] (массивы) — пересекается ли с каким-либо из отсортированных непересекающихся [starts, ends]."""
    if not len(starts):
        return np.zeros(len(a), bool)
    i = np.searchsorted(ends, a, "right")
    return (i < len(starts)) & (starts[np.minimum(i, len(starts) - 1)] < b)


def merge(iv):
    iv = sorted(iv)
    out = []
    for s, e in iv:
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return np.array([x[0] for x in out], np.int64), np.array([x[1] for x in out], np.int64)


def load_audit(root):
    res = {}
    for ex in EX5:
        f = Path(root, ex, "stretches-book.csv")
        if not f.exists():
            continue
        for r in csv.DictReader(open(f, encoding="utf-8")):
            res.setdefault(ex, {}).setdefault(r["symbol"], []).append((utc_ms(r["start_utc"]) - 60_000, utc_ms(r["end_utc"]) + 120_000))
    return res


def session_closed(kind, t_ms):
    """Сессия базового рынка закрыта в момент t (UTC)? Акции/ETF: вне пн–пт 14:30–21:00; сырьё/форекс/индекс: сб–вс."""
    t_ms = np.asarray(t_ms, np.int64)
    wd, hm = (t_ms // 86_400_000 + 3) % 7, (t_ms % 86_400_000) // 60_000        # 01.01.1970 — четверг (пн = 0)
    if kind in ("equity", "etf"):
        return (wd >= 5) | (hm < 14 * 60 + 30) | (hm >= 21 * 60)
    return wd >= 5


def mark_at(m, ex, sym, mult, t_ms):
    """Mark символа на момент t (последняя запись не позже окна 10 с), делённая на множитель названия; NaN — нет."""
    d = m.get(ex, {}).get(sym)
    if d is None:
        return np.full(len(t_ms), np.nan)
    i = np.searchsorted(d["b"], t_ms // 10_000, "right") - 1
    ok = (i >= 0) & (t_ms // 10_000 - d["b"][np.maximum(i, 0)] <= 90)    # не старше 15 мин
    return np.where(ok, d["p"][np.maximum(i, 0)] / mult, np.nan)


def max_gap(e, a, b, pad=10_000):
    """Наибольший промежуток между соседними записями (времена e, отсортированы) на отрезке [a, b]; границы — a − pad и b + pad (нет записей — вся длина)."""
    lo, hi = np.searchsorted(e, a - pad, "left"), np.searchsorted(e, b + pad, "right")
    seq = np.r_[a - pad, e[lo:hi], b + pad]
    d = np.diff(seq)
    ok = (seq[1:] > a) & (seq[:-1] < b)
    return float(d[ok].max()) if ok.any() else 0.0


def apply(tab, symstatus, dead, audit, marks, raw=None):
    df, pairs = tab["df"].copy(), tab["pairs"]
    n = len(df)
    t_end = df["t"].to_numpy() + df["dur"].to_numpy()
    t = df["t"].to_numpy()
    th = df["th"].to_numpy()
    side = df["side"].to_numpy()
    meta = {ex: all_meta(ex, ["2026-11-13", "2026-11-14", "2026-11-15", "2026-11-16"]) for ex in EX5}
    # символы и множители ног
    symA = np.empty(n, object)
    symB = np.empty(n, object)
    mulA, mulB = np.ones(n), np.ones(n)
    keys = df[["coin", "exA", "exB"]].drop_duplicates().itertuples(index=False)
    idx_of = df.groupby(["coin", "exA", "exB"]).indices
    for coin, a, b in keys:
        ii = idx_of[(coin, a, b)]
        sa, sb = pairs[(coin, a, b)]["syms"]
        symA[ii], symB[ii] = sa, sb
        mulA[ii], mulB[ii] = norm5(a, sa, meta[a])[1], norm5(b, sb, meta[b])[1]
    df["symA"], df["symB"] = symA, symB
    exA, exB = df["exA"].to_numpy(), df["exB"].to_numpy()

    # gap
    gap = np.zeros(n, bool)
    for ex in EX5:
        iv = [tuple(x) for x in dead.get(ex, [])] + [(int(a.timestamp() * 1000), int(b.timestamp() * 1000)) for a, b in OUTAGES.get(ex, []) + OUTAGES["*"]]
        s, e = merge([(a, b) for a, b in iv])
        on = (exA == ex) | (exB == ex)
        gap[on] |= overlaps(s, e, t[on] - GAP_MARGIN_MS, t_end[on] + GAP_MARGIN_MS)
    aud = np.zeros(n, bool)
    for (ex_col, sym_col) in (("exA", "symA"), ("exB", "symB")):
        for (ex, sym), ii in df.groupby([ex_col, sym_col]).indices.items():
            iv = audit.get(ex, {}).get(sym)
            if iv:
                s, e = merge(iv)
                aud[ii] |= overlaps(s, e, t[ii], t_end[ii])
    df["f_gap"] = gap | aud
    df["f_audit"] = aud
    # stale: f_stale_price — буквально «лучшая цена ноги не менялась > 5 с»; f_stale — нога молчала > 5 с по сырым записям (размеры тоже), если сырьё дано (иначе = f_stale_price)
    df["f_stale_price"] = (df["ageA"] > STALE_MS) | (df["ageB"] > STALE_MS)
    df["f_stale60"] = (df["ageA"] > 60_000) | (df["ageB"] > 60_000)
    if raw is None:
        df["f_stale"] = df["f_stale_price"]
    else:
        rgA, rgB = df["ageA"].to_numpy().astype(float).copy(), df["ageB"].to_numpy().astype(float).copy()
        for col, ex_col, sym_col, rg in (("ageA", "exA", "symA", rgA), ("ageB", "exB", "symB", rgB)):
            for i in np.flatnonzero(df[col].to_numpy() > STALE_MS):
                e = raw.get((df[ex_col].iat[i], df[sym_col].iat[i]))
                rg[i] = max_gap(e, t[i], t_end[i]) if e is not None else rg[i]
        df["raw_gapA"], df["raw_gapB"] = rgA, rgB
        df["f_stale"] = (rgA > STALE_MS) | (rgB > STALE_MS)
    # contract
    mm = np.array([abs(pairs[(c, a, b)]["M_med"] or 0.0) for c, a, b in zip(df["coin"], df["exA"], df["exB"])])
    df["pair_Mmed"] = np.array([pairs[(c, a, b)]["M_med"] for c, a, b in zip(df["coin"], df["exA"], df["exB"])], float)
    df["f_contract"] = (df["view"] == "z") & (mm >= th / 2)
    # tradfi / delist
    kindA = np.array([symstatus[a].get(s, {}).get("kind", "crypto") for a, s in zip(exA, symA)])
    kindB = np.array([symstatus[b].get(s, {}).get("kind", "crypto") for b, s in zip(exB, symB)])
    df["kindA"], df["kindB"] = kindA, kindB
    TRADFI = ("equity", "etf", "commodity", "forex", "index")
    coin_kind = {}                                     # класс монеты — по любой бирже, которая её так классифицирует (у BingX класса нет)
    for ex in EX5:
        for sym, info in symstatus[ex].items():
            if info["kind"] in TRADFI:
                coin_kind.setdefault(norm5(ex, sym, meta[ex])[0], info["kind"])
    tradfi_kind = df["coin"].map(coin_kind).fillna("").to_numpy().astype(object)
    closed = np.zeros(n, bool)
    for k in TRADFI:
        m = tradfi_kind == k
        if m.any():
            closed[m] = session_closed(k, t[m])
    df["tradfi_kind"] = tradfi_kind
    df["f_tradfi"] = closed
    dl = np.zeros(n, bool)
    for (ex_col, sym_col) in (("exA", "symA"), ("exB", "symB")):
        for (ex, sym), ii in df.groupby([ex_col, sym_col]).indices.items():
            info = symstatus[ex].get(sym, {})
            if info.get("pre"):
                dl[ii] = True
            if info.get("status") in ("SETTLING", "delisting"):
                dm = info.get("delist_ms")
                dl[ii] = True if dm is None else (t[ii] >= dm - 3 * 86_400_000)
            elif info.get("delist_ms") and info["delist_ms"] < 4_000_000_000_000:
                dl[ii] |= t[ii] >= info["delist_ms"]
    df["f_delist"] = dl
    # funding и mark
    fund = np.zeros(n, bool)
    mkA, mkB = np.full(n, np.nan), np.full(n, np.nan)
    for (ex_col, sym_col, mk, mul) in (("exA", "symA", mkA, mulA), ("exB", "symB", mkB, mulB)):
        for (ex, sym), ii in df.groupby([ex_col, sym_col]).indices.items():
            d = marks.get(ex, {}).get(sym)
            mk[ii] = mark_at(marks, ex, sym, mul[ii], t[ii])
            if d is not None and d["pay"]:
                tf = np.array([x[0] for x in d["pay"]], np.int64)
                j = np.searchsorted(tf, t[ii] - FUND_MARGIN_MS, "left")
                fund[ii] |= (j < len(tf)) & (tf[np.minimum(j, len(tf) - 1)] <= t_end[ii] + FUND_MARGIN_MS)
    df["f_funding"] = fund
    df["markA"], df["markB"] = mkA, mkB
    with np.errstate(invalid="ignore", divide="ignore"):
        mg = (mkA / mkB - 1) * 1e4
        base = np.where(df["view"].to_numpy() == "c", df["c0"].to_numpy(), 0.0)
        df["mark_gap"] = mg
        df["mark_dev_A"] = (df["price"].to_numpy() / mkA - 1) * 1e4
        df["mark_dev_B"] = (df["priceB"].to_numpy() / mkB - 1) * 1e4
        conf = side * (mg - base)
    df["f_mark"] = np.isfinite(conf) & (conf < th / 2)
    df["mark_known"] = np.isfinite(conf)
    df["f_trend"] = (df["view"] == "c") & (df["trend"] >= th / 2)
    df["f_bingx"] = (exA == "bingx") | (exB == "bingx")
    layer = np.full(n, "", object)
    for f in reversed(FILTERS):
        layer[df[f"f_{f}"].to_numpy()] = f
    df["layer"] = layer
    df["keep"] = layer == ""
    return df


def need_windows(df, pad=10_000):
    """Окна сырых записей для проверки живости: ноги с возрастом цены > 5 с у эпизодов, не снятых раньше (gap, contract, tradfi, delist)."""
    pre = ~(df["f_gap"] | df["f_contract"] | df["f_tradfi"] | df["f_delist"])
    need = {}
    for col, ex_col, sym_col in (("ageA", "exA", "symA"), ("ageB", "exB", "symB")):
        m = pre & (df[col] > STALE_MS)
        for ex, sym, a, b in zip(df.loc[m, ex_col], df.loc[m, sym_col], df.loc[m, "t"], df.loc[m, "t"] + df.loc[m, "dur"]):
            need.setdefault((ex, sym), []).append((int(a) - pad - 1000, int(b) + pad + 1000))
    return need


def load_marks(spec):
    """Один или несколько pkl hold_marks через запятую (старые и новые запуски сборщика) -> склеенный словарь; «-» — нет."""
    out = {}
    for path in ([] if spec == "-" else spec.split(",")):
        for ex, m in pickle.load(open(path, "rb")).items():
            for sym, d in m.items():
                o = out.setdefault(ex, {}).get(sym)
                if o is None:
                    out[ex][sym] = d
                    continue
                b, p_ = np.concatenate([o["b"], d["b"]]), np.concatenate([o["p"], d["p"]])
                srt = np.argsort(b, kind="stable")
                b, p_ = b[srt], p_[srt]
                keep = np.r_[b[1:] != b[:-1], True]
                out[ex][sym] = {"b": b[keep], "p": p_[keep], "pay": sorted(set(map(tuple, o["pay"])) | set(map(tuple, d["pay"])))}
    return out


def main(tabp, symp, deadp, auditp, marksp, rawp, out):
    tab = pickle.load(open(tabp, "rb"))
    marks = load_marks(marksp)
    raw = pickle.load(open(rawp, "rb")) if rawp != "-" else None
    df = apply(tab, json.load(open(symp)), json.load(open(deadp)), load_audit(auditp), marks, raw)
    pickle.dump({**tab, "df": df}, open(out, "wb"), protocol=4)
    print({"episodes": len(df), "keep": int(df["keep"].sum()), **{f: int(df[f"f_{f}"].sum()) for f in FILTERS}})


if __name__ == "__main__":
    if sys.argv[1] == "need":     # hold_filter.py need <flagged.pkl> <need.pkl>
        d = pickle.load(open(sys.argv[2], "rb"))["df"]
        nw = need_windows(d)
        pickle.dump(nw, open(sys.argv[3], "wb"))
        print({"keys": len(nw), "windows": sum(len(v) for v in nw.values())})
    else:
        main(*sys.argv[1:8])
