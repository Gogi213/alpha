"""Общее для счёта «расхождения, которые держатся» (arb-holding): пять бирж (с Gate), имена монет, размер контракта, окна суток GMT+4.

Данные — как у tokyo_run (parsed/ книг четырёх бирж в /data/arb-tokyo/full/b/parsed) плюс Gate (hold_gate_parse.py → /data/arb-hold/gate/parsed/gate).
"""
import glob
import json
from pathlib import Path

from common import PFX, ROOTS, norm

EX5 = ["binance", "bybit", "okx", "bingx", "gate"]
ROOTS5 = dict(ROOTS, gate="/data/collect/gate/data")
DAY_MS = 86_400_000
TZ_MS = 4 * 3600_000                      # сутки — по GMT+4 (так читает владелец)
BUCKETS_S = (5, 10, 15, 20, 30, 40, 50, 60, 120, 300, 600, 1800, 3600)   # владелец: 5…60 с, «либо дольше» (≥ 2/5/10/30 мин, ≥ 1 ч)
THETAS = (50.0, 100.0)                    # bps: 0,5 % и 1 %
CAP_MS = 60_000                           # отсечка устаревания центра, как в Tokyo-отчёте
STALE_MS = 5_000                          # нога «устарела», если лучшая цена не менялась дольше 5 с (слово владельца)
FEE_RT_BPS = 12.6                         # 4 тейкерские ноги × 3,5 bps × 0,9 (preferences п.12)


def norm5(ex, sym, meta=None):
    """(монета, множитель названия) для пяти бирж; Gate — BTC_USDT."""
    if ex != "gate":
        return norm(ex, sym, meta)
    s, mult = sym.removesuffix("_USDT"), 1.0
    for p, m in PFX:
        if s.startswith(p) and len(s) > len(p):
            s, mult = s[len(p):], m
            break
    return s.upper(), mult


def unit_of(ex, meta_sym):
    """Монет на единицу размера в записи книги: OKX — ctVal, Gate — quanto_multiplier, остальные — 1."""
    if ex == "okx":
        return float((meta_sym or {}).get("ctVal") or 1.0)
    if ex == "gate":
        return float((meta_sym or {}).get("quanto_multiplier") or 1.0)
    return 1.0


def all_meta(ex, days):
    """Метаданные символов биржи по пулам всех дней (позднее перекрывает раннее)."""
    meta = {}
    for d in days:
        for f in sorted(glob.glob(f"{ROOTS5[ex]}/pool-{d}.json")):
            meta.update(json.loads(Path(f).read_text(encoding="utf-8")).get("meta") or {})
    return meta


def day_edges(t0, t1):
    """Границы суток GMT+4 внутри окна: [t0, b1, b2, …, t1]."""
    first = (t0 + TZ_MS) // DAY_MS + 1
    last = (t1 - 1 + TZ_MS) // DAY_MS
    return [t0] + [d * DAY_MS - TZ_MS for d in range(first, last + 1)] + [t1]


def build_coins(parsed, days):
    """{монета: {биржа: (символ, множитель, meta символа)}} по тем символам, что реально лежат разобранными в parsed/<биржа>/ (хотя бы на двух биржах)."""
    meta = {ex: all_meta(ex, days) for ex in EX5}
    coins = {}
    for ex in EX5:
        syms = {Path(f).name.split(".")[0] for f in glob.glob(str(Path(parsed, ex, "*.npz")))}
        for sym in sorted(syms):
            coin, mult = norm5(ex, sym, meta[ex])
            cur = coins.setdefault(coin, {}).get(ex)
            exact = norm5(ex, sym)[0] == coin
            if cur is None or (exact and not cur[3]):
                coins[coin][ex] = (sym, mult, meta[ex].get(sym) or {}, exact)
    return {c: {ex: v[:3] for ex, v in d.items()} for c, d in coins.items() if len(d) >= 2}, meta
