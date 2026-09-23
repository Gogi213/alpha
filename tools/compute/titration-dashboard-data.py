#!/usr/bin/env python3
"""Данные дашборда титрования F10 (план 2026-09-23, G4; просьба владельца 23.09: «дашборд, чтобы было
прозрачно видно, что это релевантно, со статистикой сделок и эквити-кривой»).

Собирает по каждой эпохе и каждому набору прогона `b5/<тег>/<сутки>/<набор>/rounds.csv` все сделки с
контролем «рост рынка» той же монеты в тот же день (функции `placebo.py`, M19), режим суток и частоту
просадок (В-86) → один JSON для страницы.

    titration-dashboard-data.py --tag titr-v1 --epoch история=<дом>:2026-09-01:2026-09-15 \
        --epoch запись=<дом>:2026-09-16:2026-09-22 --points <titration-points.csv> --out data.json
"""
import argparse
import csv
import datetime as dt
import importlib.util
import json
import os
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("placebo", os.path.join(HERE, "placebo.py"))
placebo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(placebo)

DIP_AXES = {"btc1h": "btc_ret_1h_bps", "btc4h": "btc_ret_4h_bps", "pool1h": "pool_ret_1h_bps"}
GAP_MIN = 60  # отрезки с перерывом до часа — одна просадка (как в частоте, доложенной владельцу 23.09)


def day_start_ms(day):
    return int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()) * 1000


def regime_rows(home, day):
    path = os.path.join(home, "study", "regime", f"{day}.csv")
    if not os.path.exists(path):
        return []
    s = day_start_ms(day)
    with open(path, encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if s <= int(r["minute_ms"]) < s + 86_400_000]


def day_moves(home, day):
    """Ход пула (медиана монет) и BTC за сутки, % — по первой и последней минуте суток в кэше."""
    mids_dir = os.path.join(home, "study", "touches", day)
    moves = []
    if os.path.isdir(mids_dir):
        for name in os.listdir(mids_dir):
            if not (name.startswith("mids1m-") and name.endswith(".csv")):
                continue
            with open(os.path.join(mids_dir, name), encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            if len(rows) > 60 and int(rows[0]["mid2x"]) > 0:
                moves.append((int(rows[-1]["mid2x"]) / int(rows[0]["mid2x"]) - 1) * 100)
    return round(st.median(moves), 2) if moves else None


def dip_episodes(rows, col, thr):
    mins = sorted(int(r["minute_ms"]) // 60_000 for r in rows if r.get(col) and float(r[col]) <= thr)
    eps = []
    for m in mins:
        if eps and m - eps[-1][1] <= GAP_MIN:
            eps[-1][1] = m
        else:
            eps.append([m, m])
    return eps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--epoch", action="append", required=True, help="имя=дом:с:по")
    ap.add_argument("--points", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--form-trades", action="append", default=[],
                    help="сделки одной формы другого прогона: <каталог b5>|<набор>|<форма>|<ключ> (титрование выхода, G9)")
    ap.add_argument("--exit-agg", default=None, help="сводка exit-titration-read.py (CSV) — встраивается как есть")
    ap.add_argument("--pool", default=None, help="instruments.csv пула — список монет для статистики по монетам")
    a = ap.parse_args()

    with open(a.points, encoding="utf-8") as f:
        points = {r["axis"]: r for r in csv.DictReader(f)}
    out = {"generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M"), "tag": a.tag,
           "points": points, "epochs": [], "sets": {}, "days": []}
    for spec_ in a.epoch:
        name, rest = spec_.split("=", 1)
        home, d_from, d_to = rest.split(":")
        run_dir = os.path.join(home, "b5", a.tag)
        days = sorted(d for d in os.listdir(run_dir) if d[:4].isdigit() and d_from <= d <= d_to and os.path.isdir(os.path.join(run_dir, d)))
        out["epochs"].append({"name": name, "from": d_from, "to": d_to, "days": days})
        mids = placebo.Mids(os.path.join(home, "study", "touches"))
        for day in days:
            rows = regime_rows(home, day)
            dips = {ax: len(dip_episodes(rows, col, float(points[ax]["q20"]))) for ax, col in DIP_AXES.items()}
            dip_share = {ax: round(sum(1 for r in rows if r.get(col) and float(r[col]) <= float(points[ax]["q20"]))
                                   / max(1, sum(1 for r in rows if r.get(col))), 3) for ax, col in DIP_AXES.items()}
            out["days"].append({"day": day, "epoch": name, "pool_pct": day_moves(home, day),
                                "dips": dips, "dip_share": dip_share})
            for set_name in sorted(os.listdir(os.path.join(run_dir, day))):
                path = os.path.join(run_dir, day, set_name, "rounds.csv")
                if not os.path.exists(path):
                    continue
                with open(path, encoding="utf-8", errors="replace", newline="") as f:
                    raw = {(r["symbol"], r["t0_ns"]): r for r in csv.DictReader(l for l in f if not l.startswith("#"))}
                for r in placebo.control(placebo.load_rounds(path, None), mids):
                    x = raw.get((r["symbol"], str(r["t0_ns"])), {})
                    out["sets"].setdefault(set_name, []).append([
                        name, day, r["symbol"], r["t0_ns"] // 60_000_000_000, r["exit_ns"] // 60_000_000_000,
                        round(r["net_bps"], 2), round(r["control_bps"], 2), x.get("reason", ""),
                        round(float(x.get("fill_frac") or 1.0), 3),
                    ])
    # Титрование выхода (G9): сделки выбранных форм и сводка всех форм.
    out["form_trades"] = {}
    for spec_ in a.form_trades:
        run, set_name, form, key = spec_.split("|")
        for ep in out["epochs"]:
            home = next(e.split("=", 1)[1].split(":")[0] for e in a.epoch if e.split("=", 1)[0] == ep["name"])
            mids = placebo.Mids(os.path.join(home, "study", "touches"))
            for day in ep["days"]:
                path = os.path.join(home, "b5", run, day, set_name, "rounds.csv")
                if not os.path.exists(path):
                    continue
                with open(path, encoding="utf-8", errors="replace", newline="") as f:
                    raw = {(r["symbol"], r["t0_ns"]): r for r in csv.DictReader(l for l in f if not l.startswith("#"))
                           if r["form"] == form}
                for r in placebo.control(placebo.load_rounds(path, form), mids):
                    x = raw.get((r["symbol"], str(r["t0_ns"])), {})
                    out["form_trades"].setdefault(key, []).append([
                        ep["name"], day, r["symbol"], r["t0_ns"] // 60_000_000_000, r["exit_ns"] // 60_000_000_000,
                        round(r["net_bps"], 2), round(r["control_bps"], 2), x.get("reason", ""),
                        round(float(x.get("fill_frac") or 1.0), 3),
                    ])
    if a.exit_agg:
        with open(a.exit_agg, encoding="utf-8") as f:
            out["exit_agg"] = list(csv.DictReader(f))
    if a.pool:
        with open(a.pool, encoding="utf-8") as f:
            out["pool"] = [r["symbol"] for r in csv.DictReader(l for l in f if not l.startswith("#"))]
    out["columns"] = ["epoch", "day", "symbol", "t0_min", "exit_min", "net_bps", "control_bps", "reason", "fill_frac"]
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    n = sum(len(v) for v in out["sets"].values())
    print(f"наборов {len(out['sets'])}, сделок {n}, суток {len(out['days'])} → {a.out}")


if __name__ == "__main__":
    main()
