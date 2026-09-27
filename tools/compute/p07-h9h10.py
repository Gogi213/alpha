#!/usr/bin/env python3
"""П-07 H9/H10 (пауза по монете / потолок позиций) поверх базы Г-85а/Г-85б (T-31 busy-replay,
CEO 27.09: считать сразу по готовности баз, не дожидаясь ступени 2).

Пауза (H9) - слой НАД штатным busy-replay.py: сначала воспроизводим тот же автомат «занято»,
что и bin/busy-replay.py (day_idle/stopped по (symbol,day_utc,form), как в его replay()), затем
добавляем `pause_until` - персистентный по монете (не по суткам) момент, до которого новый сигнал
не принимается, если предыдущий ПРИНЯТЫЙ сигнал дал исполненный круг (rounds.csv) с исходом,
подходящим под правило паузы (любое закрытие или только «stop»). При pause_min=0 запрос идёт без
доп. фильтра прямо в bin/busy-replay.py (byte-for-byte со сверкой b5/p07{a,b}-base) - гейт H0.

Потолок (H10) - одновременно 3/5 - готовый флаг `portfolio-sim.py --max-pos`, доп. слоя не нужно.
«Первые N/K на эпизод BTC» - НЕ реализовано в этом проходе (нужен t32-epcap.py, отсутствует на
деке) - см. отчёт.

    python3 p07-h9h10.py --variant a
    python3 p07-h9h10.py --variant b
"""
import argparse
import csv
import glob
import importlib.util
import json
import os
import subprocess
import sys

HOME = os.path.expanduser("~/alpha")
SET_ = "t-bid-btc4h-q1"
BUSY_REPLAY = os.path.join(HOME, "bin/busy-replay.py")
PORT_SIM = os.path.join(HOME, "tmp-kpi/portfolio-sim.py")
KN_PATH = os.path.join(HOME, "tmp-t32/kpi-newhigh.py")
OUT_ROOT = os.path.join(HOME, "tmp-p07")
HOMES = {  # tag -> (home dir, base subdir for out)
    "aug": os.path.join(HOME, "epochs/e-aug"),
    "hist": os.path.join(HOME, "tmp-lsk0914.used-20260926/home"),
    "rec": os.path.join(HOME, "tmp-t29/rec"),
}


def load_kn():
    spec = importlib.util.spec_from_file_location("kn", KN_PATH)
    kn = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kn)
    return kn


def read_body(path):
    with open(path, encoding="utf-8", newline="") as fh:
        lines = fh.read().splitlines(keepends=True)
    comments = [l for l in lines if l.startswith("#")]
    body = [l for l in lines if not l.startswith("#")]
    return comments, body


# имя формы базы в rounds.csv (TK-004: у Г-85б — σ-лестница, у Г-85а single@fr без поля входа)
FORM_OF = {"a": "pct2-tr1x1-14400-ttl1800", "b": "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"}


def base_dir_for(variant):
    return f"p07{variant}-base"


def all_day_dirs(variant):
    """[(tag, home, day_dir)] по всем трём домам, отсортировано по дню."""
    out = []
    bd = base_dir_for(variant)
    for tag, home in HOMES.items():
        for d in sorted(glob.glob(os.path.join(home, "b5", bd, "20*"))):
            out.append((tag, home, d))
    return out


def load_rounds_index(day_dir):
    """(symbol,day_utc,form,signal_index) -> (exit_ns, reason) из rounds.csv суток."""
    fp = os.path.join(day_dir, SET_, "rounds.csv")
    idx = {}
    if not os.path.exists(fp):
        return idx
    _, body = read_body(fp)
    if not body:
        return idx
    for r in csv.DictReader(body):
        key = (r["symbol"], r["day_utc"], r["form"], r["signal_index"])
        idx[key] = (int(r["exit_ns"]), r["reason"])
    return idx


def load_signals(day_dir):
    fp = os.path.join(day_dir, SET_, "signals.csv")
    if not os.path.exists(fp):
        return []
    _, body = read_body(fp)
    if not body:
        return []
    rounds_idx = load_rounds_index(day_dir)
    out = []
    for r in csv.DictReader(body):
        key = (r["symbol"], r["day_utc"], r["form"], r["signal_index"])
        out.append({
            "symbol": r["symbol"], "day_utc": r["day_utc"], "form": r["form"],
            "signal_index": r["signal_index"], "t0_ns": int(r["t0_ns"]),
            "price_tick": r["price_tick"], "step": r["step"],
            "idle_ns": int(r["idle_ns"]) if r["idle_ns"] else None,
            "residual": r["residual"], "round": rounds_idx.get(key),
        })
    return out


def simulate_pause(variant, pause_min, stop_only):
    """-> [(symbol, t0_ns, price_tick)] сигналов, принятых под паузой pause_min мин."""
    pause_ns = pause_min * 60 * 1_000_000_000
    by_symbol = {}
    for tag, home, d in all_day_dirs(variant):
        for r in load_signals(d):
            by_symbol.setdefault(r["symbol"], []).append(r)
    keep = []
    for symbol, rows in by_symbol.items():
        rows.sort(key=lambda r: r["t0_ns"])
        day_idle = None
        stopped = False
        cur_day = None
        pause_until = -1
        for r in rows:
            if r["day_utc"] != cur_day:
                cur_day = r["day_utc"]
                day_idle = None
                stopped = False
            if stopped:
                continue
            t0 = r["t0_ns"]
            if day_idle is not None and t0 < day_idle:
                continue
            if t0 < pause_until:
                continue
            keep.append((symbol, str(r["t0_ns"]), r["price_tick"]))
            if r["step"] in ("end_of_data", "no_window") or r["residual"] == "ended":
                stopped = True
            else:
                day_idle = r["idle_ns"]
            if r["round"] is not None:
                exit_ns, reason = r["round"]
                if (not stop_only) or reason == "stop":
                    cand = exit_ns + pause_ns
                    if cand > pause_until:
                        pause_until = cand
    return keep


def write_keep(rows, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "t0_ns", "price_tick"])
        w.writerows(rows)


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        print("CMD FAILED:", " ".join(cmd), file=sys.stderr)
        print(r.stdout[-3000:], file=sys.stderr)
        print(r.stderr[-3000:], file=sys.stderr)
        raise SystemExit(1)
    return r.stdout


def busy_replay_for(variant, name, keep_path):
    bd = base_dir_for(variant)
    outs = {}
    for tag, home in HOMES.items():
        src = os.path.join(home, "b5", bd)
        out_dir = os.path.join(OUT_ROOT, f"h9-{variant}-{name}", tag)
        os.makedirs(out_dir, exist_ok=True)
        cmd = ["python3", BUSY_REPLAY, src, out_dir, "--sets", SET_]
        if keep_path:
            cmd += ["--keep", keep_path]
        run(cmd)
        outs[tag] = out_dir
    return outs


def portfolio_sim_for(variant, name, outs, max_pos=0):
    os.makedirs(os.path.join(OUT_ROOT, f"h9-{variant}-{name}"), exist_ok=True)
    j = os.path.join(OUT_ROOT, f"h9-{variant}-{name}", "ps.json")
    co = os.path.join(OUT_ROOT, f"h9-{variant}-{name}", "ps-closes.json")
    cmd = ["python3", PORT_SIM,
           "--epoch", f"история={outs['hist']}:.",
           "--epoch", f"запись={outs['rec']}:.",
           "--epoch", f"август={outs['aug']}:.",
           "--join", "сентябрь=история+запись",
           "--variant", f"п07{variant}={SET_}/{FORM_OF[variant]}",
           "--klines", os.path.join(HOME, "study/klines"),
           "--klines", os.path.join(HOME, "epochs/e-aug/study/klines"),
           "--deposit-usd", "2500", "--position-usd", "500", "--max-pos", str(max_pos),
           "--day-stop-pct", "0", "--btc-kill-bps", "0", "--drop", "TRXUSDT",
           "--json", j, "--closes-out", co]
    run(cmd)
    return json.load(open(j, encoding="utf-8")), json.load(open(co, encoding="utf-8"))


def kpi_of(kn, ps_closes, variant, cap=0):
    v = f"п07{variant}"
    money = {}
    for pk in ("август", "сентябрь"):
        money[pk] = ps_closes.get(v, {}).get(pk, {}).get(str(cap), [])  # ключ closes — значение --max-pos
    aug_closes = money["август"]
    sep_closes = money["сентябрь"]
    all_closes = sorted([tuple(c) for c in aug_closes] + [tuple(c) for c in sep_closes])
    roll = kn.rolling_kpi(all_closes, h_days=5)
    def n_usd(cs):
        return len(cs), round(sum(c[1] for c in cs), 2)
    n_a, usd_a = n_usd(aug_closes)
    n_s, usd_s = n_usd(sep_closes)
    return {"aug": {"n": n_a, "usd": usd_a, "frac_gt_5d": roll["aug"]["frac_gt_h"], "n_main": roll["aug"]["n_main"]},
            "sep": {"n": n_s, "usd": usd_s, "frac_gt_5d": roll["sep"]["frac_gt_h"], "n_main": roll["sep"]["n_main"]}}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", required=True, choices=["a", "b"])
    ap.add_argument("--caps-only", action="store_true", help="только потолки H10 (дописать в готовый json)")
    ap.add_argument("--no-pauses", action="store_true",
                    help="без H9 «пауза» (заменена H9р/H14, поправка 3): база pause0 + потолки H10")
    a = ap.parse_args()
    kn = load_kn()
    out_json0 = os.path.join(OUT_ROOT, f"h9h10-{a.variant}.json")
    results = json.load(open(out_json0, encoding="utf-8")) if a.caps_only else {}

    # база (pause=0) - прямой busy-replay без фильтра, гейт H0 (сверка с посчитанной базой отдельно)
    outs0 = busy_replay_for(a.variant, "pause0", None)
    ps, co = portfolio_sim_for(a.variant, "pause0", outs0, max_pos=0)
    results["pause0"] = kpi_of(kn, co, a.variant)
    print("pause0:", results["pause0"], flush=True)

    for pause_min, stop_only, tag in ([] if (a.caps_only or a.no_pauses) else ((30, False, "pause30"), (60, False, "pause60"),
                                       (120, False, "pause120"), (60, True, "pauseStop60"),
                                       (120, True, "pauseStop120"))):
        keep = simulate_pause(a.variant, pause_min, stop_only)
        keep_path = os.path.join(OUT_ROOT, f"h9-{a.variant}-{tag}", "keep.csv")
        write_keep(keep, keep_path)
        outs = busy_replay_for(a.variant, tag, keep_path)
        ps, co = portfolio_sim_for(a.variant, tag, outs, max_pos=0)
        results[tag] = kpi_of(kn, co, a.variant)
        print(f"{tag}: {results[tag]}", flush=True)

    for cap in (3, 5):
        tag = f"cap{cap}"
        ps, co = portfolio_sim_for(a.variant, tag, outs0, max_pos=cap)
        results[tag] = kpi_of(kn, co, a.variant, cap)
        print(f"{tag}: {results[tag]}", flush=True)

    out_json = os.path.join(OUT_ROOT, f"h9h10-{a.variant}.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print("DONE", out_json)


if __name__ == "__main__":
    main()
