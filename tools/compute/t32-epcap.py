#!/usr/bin/env python3
"""T-32 задача (3) «epcap»: потолок сделок на эпизод просадки BTC (владелец, 27.09, KPI В-120).

Эпизод — подряд идущие минуты режимных файлов с btc_ret_4h_bps ≤ −44,55 (порог главного варианта), разрывы
≤ 30 мин склеены в один эпизод. Сделка относится к эпизоду, если t0 ∈ [начало эпизода; конец эпизода + 31 мин]
(проверено: 0 сделок вне эпизодов). Внутри эпизода сделки упорядочены по t0.

Варианты (данные data/t32/main-trades.csv — главный вариант, без TRX, без потолка):
  all             — без ограничения (справочно, = main-trades.csv)
  firstN3/5/8     — только первые N сделок эпизода (по t0, любая монета)
  onePerCoin      — одна сделка на монету в эпизоде (повторные входы той же монеты в эпизоде убраны)
  skipK3/5        — пропустить первые K сделок эпизода (вход только позже в эпизоде)
  minAge30/60/120 — сделка входит, только если к моменту t0 эпизод уже длится ≥ M минут (t0 − начало эпизода)
  cap3/cap5       — потолок одновременно открытых позиций по [t0;t1] среди сделок ГЛАВНОГО варианта (без
                    занятости монеты — см. оговорку)

KPI — только tools/compute/kpi-newhigh.py:month_metrics(closes, pk), closes=[(t1_ms, pnl_usd), …].

    python tools/compute/t32-epcap.py --out data/t32/epcap.json --summary data/t32/epcap-summary.md
"""
import argparse
import csv
import datetime as dt
import glob
import importlib.util
import json
import os

GAP_MS = 30 * 60_000
TAIL_MS = 31 * 60_000
THRESH = -44.55
REGIME_DIRS = ["data/t32/epochs/e-aug/study/regime", "data/t32/epochs/e-archive/study/regime", "data/t32/study/regime"]


def load_kn():
    spec = importlib.util.spec_from_file_location("kn", "tools/compute/kpi-newhigh.py")
    kn = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kn)
    return kn


def load_regime(dirs):
    """minute_ms → btc_ret_4h_bps; из каждого дневного файла берём только минуты его собственной даты (файлы
    несут «хвост» предыдущих суток для разогрева окна 4ч)."""
    m = {}
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(d, "20*.csv"))):
            day = os.path.basename(f)[:-4]
            for row in csv.DictReader(open(f, encoding="utf-8")):
                mt = int(row["minute_ms"])
                if dt.datetime.fromtimestamp(mt / 1000, dt.timezone.utc).strftime("%Y-%m-%d") != day:
                    continue
                v = row["btc_ret_4h_bps"]
                m[mt] = float(v) if v not in ("", None) else None
    return m


def build_episodes(regime):
    """[(start_ms, end_ms)] — end_ms последняя отфильтрованная минута; разрывы ≤ GAP_MS склеены."""
    mins = sorted(regime)
    runs = []
    cur = None
    for mt in mins:
        under = regime[mt] is not None and regime[mt] <= THRESH
        if under:
            if cur is None:
                cur = [mt, mt]
            else:
                cur[1] = mt
        else:
            if cur is not None:
                runs.append(tuple(cur))
                cur = None
    if cur is not None:
        runs.append(tuple(cur))
    # склейка разрывов <= GAP_MS
    episodes = []
    for s, e in runs:
        if episodes and s - episodes[-1][1] <= GAP_MS:
            episodes[-1] = (episodes[-1][0], e)
        else:
            episodes.append((s, e))
    return episodes


def assign_episode(t0, episodes):
    for i, (s, e) in enumerate(episodes):
        if s <= t0 <= e + TAIL_MS:
            return i
    return None


def load_trades(path):
    rows = []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        rows.append({"month": r["month"], "sym": r["sym"], "t0": int(r["t0_ms"]), "t1": int(r["t1_ms"]),
                     "pnl": float(r["pnl_usd"])})
    return rows


def variant_all(trades, ep_of):
    return list(trades)


def variant_first_n(trades, ep_of, n):
    keep, seen = [], {}
    for t in trades:
        i = seen.setdefault(ep_of[id(t)], 0)
        if i < n:
            keep.append(t)
        seen[ep_of[id(t)]] = i + 1
    return keep


def variant_one_per_coin(trades, ep_of):
    keep, seen = [], set()
    for t in trades:
        k = (ep_of[id(t)], t["sym"])
        if k in seen:
            continue
        seen.add(k)
        keep.append(t)
    return keep


def variant_skip_k(trades, ep_of, k):
    keep, seen = [], {}
    for t in trades:
        i = seen.setdefault(ep_of[id(t)], 0)
        if i >= k:
            keep.append(t)
        seen[ep_of[id(t)]] = i + 1
    return keep


def variant_min_age(trades, ep_of, episodes, m_min):
    keep = []
    for t in trades:
        s, _e = episodes[ep_of[id(t)]]
        if (t["t0"] - s) >= m_min * 60_000:
            keep.append(t)
    return keep


def variant_cap(trades, cap):
    """Потолок одновременно открытых позиций по [t0;t1] главного варианта (без занятости монеты — оговорка)."""
    trades = sorted(trades, key=lambda t: t["t0"])
    open_ = []  # список t1 открытых
    keep = []
    for t in trades:
        open_ = [e for e in open_ if e > t["t0"]]
        if len(open_) < cap:
            open_.append(t["t1"])
            keep.append(t)
    return keep


def closes_of(trades, month=None):
    xs = trades if month is None else [t for t in trades if t["month"] == month]
    return [(t["t1"], t["pnl"]) for t in xs]


def metrics_for(kn, trades):
    return {"aug": kn.month_metrics(closes_of(trades, "aug"), "aug"),
            "sep": kn.month_metrics(closes_of(trades, "sep"), "sep"),
            "augsep": kn.month_metrics(closes_of(trades, None), "augsep")}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trades", default="data/t32/main-trades.csv")
    ap.add_argument("--out", default="data/t32/epcap.json")
    ap.add_argument("--summary", default="data/t32/epcap-summary.md")
    a = ap.parse_args()

    kn = load_kn()
    regime = load_regime(REGIME_DIRS)
    episodes = build_episodes(regime)
    trades = load_trades(a.trades)

    ep_of = {}
    outside = 0
    for t in trades:
        i = assign_episode(t["t0"], episodes)
        if i is None:
            outside += 1
        ep_of[id(t)] = i
    eps_touched_baseline = len({ep_of[id(t)] for t in trades if ep_of[id(t)] is not None})

    variants = {}
    variants["all"] = variant_all(trades, ep_of)
    for n in (3, 5, 8):
        variants[f"firstN{n}"] = variant_first_n(trades, ep_of, n)
    variants["onePerCoin"] = variant_one_per_coin(trades, ep_of)
    for k in (3, 5):
        variants[f"skipK{k}"] = variant_skip_k(trades, ep_of, k)
    for m_min in (30, 60, 120):
        variants[f"minAge{m_min}"] = variant_min_age(trades, ep_of, episodes, m_min)
    for cap in (3, 5):
        variants[f"cap{cap}"] = variant_cap(trades, cap)

    results = {}
    for name, ts in variants.items():
        eps_touched = len({ep_of[id(t)] for t in ts if id(t) in ep_of and ep_of[id(t)] is not None})
        results[name] = {"n_trades": len(ts), "episodes_with_trades": eps_touched, **metrics_for(kn, ts)}

    out = {"threshold_bps": THRESH, "gap_glue_min": 30, "tail_min": 31, "n_episodes_total": len(episodes),
           "n_trades_outside_episodes": outside, "n_trades_baseline": len(trades),
           "episodes_with_trades_baseline": eps_touched_baseline, "n_variants_viewed": len(variants),
           "results": results}
    json.dump(out, open(a.out, "w", encoding="utf-8", newline=""), ensure_ascii=False, indent=1)

    h = lambda x: "—" if x is None else f"{x:.1f}"
    ok5 = []
    lines = []
    lines.append(f"# T-32 (3) epcap — потолок сделок на эпизод просадки BTC\n")
    lines.append(f"Порог главного BTC 4ч ≤ {THRESH} bps; эпизод — разрывы ≤ 30 мин склеены; сделка → эпизод, если "
                  f"t0 ∈ [начало; конец + 31 мин]. Эпизодов всего: {len(episodes)}. Сделок вне эпизодов: {outside} "
                  f"(из {len(trades)}) — сверка ok.\n")
    lines.append("| вариант | сделок | эп. со сделками | до перехая, дни (авг;сен;augsep worst) | $ авг | $ сен | $ augsep | ≤5 дн оба мес |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for name, r in results.items():
        wa = r["aug"]["hours"]["worst"] / 24 if r["aug"]["hours"]["worst"] is not None else None
        ws = r["sep"]["hours"]["worst"] / 24 if r["sep"]["hours"]["worst"] is not None else None
        wg = r["augsep"]["hours"]["worst"] / 24 if r["augsep"]["hours"]["worst"] is not None else None
        good = wa is not None and ws is not None and wa <= 5 and ws <= 5
        if good:
            ok5.append(name)
        lines.append(f"| {name} | {r['n_trades']} | {r['episodes_with_trades']} | {h(wa)};{h(ws)};{h(wg)} | "
                      f"{r['aug']['usd']:+.0f} | {r['sep']['usd']:+.0f} | {r['augsep']['usd']:+.0f} | {'да' if good else ''} |")
    lines.append("")
    lines.append(f"≤ 5 дней в обоих месяцах: {', '.join(ok5) if ok5 else 'нет вариантов'}.")
    lines.append(f"\nПросмотрено вариантов: {len(variants)} (+ baseline «all»). Найдено на данных подбора (август/сентябрь, "
                  f"те же дни, что и главный вариант).")
    lines.append("\nОговорки: убранные сделки просто выпадают из счёта (занятость монеты и очередь входа не "
                  "пересчитаны); `cap3`/`cap5` — потолок по [t0;t1] всех сделок главного варианта без учёта занятости "
                  "монеты (дашборд: потолок 3 → авг ≈ +$27, сен ≈ +$102 — с занятостью монеты, здесь чуть иначе). "
                  "Никакой t-статистики по сделкам.")
    md = "\n".join(lines)
    open(a.summary, "w", encoding="utf-8", newline="").write(md)
    print(md)


if __name__ == "__main__":
    main()
