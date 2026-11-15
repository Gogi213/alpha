"""Общее для статистики арбитража (вкладка «Арбитраж» страницы здоровья G-022).

Формулы — из проекта viking (формулы раздвижки: strategies/xarb_base.md §2, viking-docs p.calc_method;
перцентили, доли времени выше уровней, центр и разброс: tools/dashboard.py spread_stats / center_tw / vol_tw / bar_stats).
"""
import json
import os
from pathlib import Path

# порядок бирж = порядок пар (A, B): A раньше B
EXCHANGES = ["binance", "bybit", "okx", "bingx", "gate"]
ROOTS = {
    "binance": "/data/collect/binance/data",
    "bybit": "/data/turbo-probe/soak-bybit/data",
    "okx": "/data/collect/okx/data",
    "bingx": "/data/collect/bingx/data",
    "gate": "/data/collect/gate/data",
}
MINRUN = 1794656000                 # начало последнего запуска сборщиков (final6j): записи старых запусков другого вида
MINRUN_EX = {"gate": 1794721764}    # у Gate — свой: короткий пробный запуск 1794721649 (05:47 UTC 15.11, ≈ 2 мин) пропускается, постоянный — 1794721764 (05:49)
SINCE = {"gate": "2026-11-15"}      # первый день с данными: пулов раньше нет, биржа в те дни пропускается
PFX = (("1000000", 1_000_000.0), ("100000", 100_000.0), ("10000", 10_000.0), ("1000", 1000.0))
LATE_MS = 2000      # порог «поздней» записи (как на странице здоровья, thresholds.json)


def norm(ex, sym, meta=None):
    """(монета, множитель названия): 1000PEPEUSDT -> ('PEPE', 1000)."""
    if ex == "okx":
        s = sym.removesuffix("-USDT-SWAP")
    elif ex == "gate":
        s = sym.removesuffix("_USDT")
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


def minrun(ex):
    """Метка запуска сборщика биржи (с), с которой файлы годятся в счёт."""
    return MINRUN_EX.get(ex, MINRUN)


def unit_of(ex, m):
    """Размер контракта (монет в контракте) по записи пула символа: OKX — ctVal, Gate — quanto_multiplier; у остальных количество уже в монете."""
    if ex == "okx":
        return float((m or {}).get("ctVal") or 1.0)
    if ex == "gate":
        return float((m or {}).get("quanto_multiplier") or 1.0)
    return 1.0


def load_pool(ex, day):
    """Пул символов биржи на день: пул самого дня, иначе последний раньше (сборщик пишет пул при запуске: у бирж, запущенных 14.11, последний — 14.11, у Gate — 15.11).
    До первого дня данных биржи (SINCE) — пусто; нет ни одного пула не позже дня — OSError."""
    if day < SINCE.get(ex, ""):
        return [], {}, {}
    p = Path(ROOTS[ex], f"pool-{day}.json")
    if not p.exists():
        older = sorted(f for f in os.listdir(ROOTS[ex]) if f.startswith("pool-") and f.endswith(".json") and f[5:-5] <= day) if Path(ROOTS[ex]).is_dir() else []
        if not older:
            raise FileNotFoundError(str(p))
        p = Path(ROOTS[ex], older[-1])
    d = json.loads(p.read_text(encoding="utf-8"))
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
