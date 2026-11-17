"""Общее для статистики арбитража (вкладка «Арбитраж» страницы здоровья G-022).

Формулы — из проекта viking (формулы раздвижки: strategies/xarb_base.md §2, viking-docs p.calc_method;
перцентили, доли времени выше уровней, центр и разброс: tools/dashboard.py spread_stats / center_tw / vol_tw / bar_stats).
"""
import json
from pathlib import Path

# порядок бирж = порядок пар (A, B): A раньше B
EXCHANGES = ["binance", "bybit", "okx", "bingx"]
ROOTS = {
    "binance": "/data/collect/binance/data",
    "bybit": "/data/turbo-probe/soak-bybit/data",
    "okx": "/data/collect/okx/data",
    "bingx": "/data/collect/bingx/data",
}
PFX = (("1000000", 1_000_000.0), ("100000", 100_000.0), ("10000", 10_000.0), ("1000", 1000.0))
LATE_MS = 2000      # порог «поздней» записи (как на странице здоровья, thresholds.json)


def norm(ex, sym, meta=None):
    """(монета, множитель названия): 1000PEPEUSDT -> ('PEPE', 1000)."""
    if ex == "okx":
        s = sym.removesuffix("-USDT-SWAP")
    elif ex == "bingx":
        disp = ((meta or {}).get(sym) or {}).get("displayName") or sym
        s = disp.removesuffix("-USDT")
    else:
        s = sym.removesuffix("USDT")
    mult = 1.0
    for p, m in PFX:
        if s.startswith(p) and len(s) > len(p):
            s, mult = s[len(p):], m
            break
    return s.upper(), mult


def load_pool(ex, day):
    d = json.loads(Path(ROOTS[ex], f"pool-{day}.json").read_text(encoding="utf-8"))
    return d["pool"], d.get("meta") or {}, d


def build_symmap(day):
    """{биржа: {символ: (монета, множитель)}} только для монет, общих минимум для двух бирж."""
    per = {}
    for ex in EXCHANGES:
        pool, meta, _ = load_pool(ex, day)
        per[ex] = {s: norm(ex, s, meta) for s in pool}
    cnt = {}
    for ex, m in per.items():
        for coin in {c for c, _ in m.values()}:
            cnt[coin] = cnt.get(coin, 0) + 1
    keep = {c for c, n in cnt.items() if n >= 2}
    return {ex: {s: cm for s, cm in m.items() if cm[0] in keep} for ex, m in per.items()}
