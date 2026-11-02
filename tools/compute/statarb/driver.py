#!/usr/bin/env python3
"""Драйвер счёта П-32 (TK-484): клетка §7 × месяцы → задания `alpha statarb run`, волнами от дешёвых к дорогим.

  plan — для клеток выбранных волн: задания по месяцам (формирование — сменный модуль formation.py),
         jobs-<клетка>.csv, run-waves.sh (клетки по порядку волн, суточный csv каждой пишется сразу по
         готовности), steps.tsv для `guard.py plan` (выписка до подачи, В-213), manifest.json.

Значений по умолчанию у параметров клетки нет (новых чисел не назначать): window, min_obs, min_span, entry_z,
exit_z, stop_z, max_hold, refresh, threads — обязательные `--param k=v`. Порядок волн — решение CEO (03.11):
1 — D2, A2; 2 — B1; 3 — A1; C1 ждёт слияния TK-482 (задержка ноги X), D1/D3 — TK-483. Семья Холма и
правило вердикта (m₁ = 19) от порядка не зависят.
"""
import argparse
import csv
import datetime as dt
import json
import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import formation as fm  # noqa: E402

EPOCH = dt.date(2020, 1, 1)
EXCHANGES = ("binance-um", "bybit-linear", "gate-futures_usdt")
REQUIRED = ("window", "min_obs", "min_span", "entry_z", "exit_z", "stop_z", "max_hold", "refresh", "threads")
FORM_DAYS = 90  # окно формирования §6: 90 суток до торгового месяца


def day_of(s):
    return (dt.date.fromisoformat(s) - EPOCH).days


def months(first, last):
    """['YYYY-MM' первый .. последний] → [(метка, from_day, to_day не включая)]."""
    y, m = map(int, first.split("-"))
    ly, lm = map(int, last.split("-"))
    out = []
    while (y, m) <= (ly, lm):
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        out.append((f"{y}-{m:02d}", day_of(f"{y}-{m:02d}-01"), day_of(f"{ny}-{nm:02d}-01")))
        y, m = ny, nm
    return out


def cells():
    """Клетки этапа 1 §7 с волной; status — можно ли гнать сейчас."""
    c = []
    c.append(dict(id="D2-binance-bybit", wave=1, kind="funding-pp", ex="binance-um", ex_b="bybit-linear",
                  formation="same_coin", beta=1, note="перп↔перп с выплатами обеих ног"))
    for ex in EXCHANGES:
        c.append(dict(id=f"A2-{ex}", wave=1, kind="pair", ex=ex, formation="anchor", beta="ols"))
    for ex in EXCHANGES:
        for k in (3, 10):
            c.append(dict(id=f"B1-{ex}-K{k}", wave=2, kind="basket", ex=ex, formation="basket", beta="ols", k=k))
    for ex in EXCHANGES:
        for b in ("ols", "1"):
            c.append(dict(id=f"A1-{ex}-b{b}", wave=3, kind="pair", ex=ex, formation="pairs", beta=b))
    for a, b in (("binance-um", "bybit-linear"), ("binance-um", "gate-futures_usdt"),
                 ("bybit-linear", "gate-futures_usdt")):
        c.append(dict(id=f"C1-{a}-{b}", wave=4, kind="cross", ex=a, ex_b=b, formation="same_coin", beta=1,
                      blocked="нужен delay_x (TK-482, не слит в master)"))
    return c


def read_alive(path):
    """Вселенная ворот → {биржа: [(символ, from_day, to_day включая)]}."""
    out = {e: [] for e in EXCHANGES}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            out.setdefault(r["exchange"], []).append((r["symbol"], day_of(r["from_day"]), day_of(r["to_day"])))
    return out


def alive_in(intervals, from_day, to_day):
    """Символы, у которых есть сутки внутри [from_day, to_day)."""
    return {s for s, a, b in intervals if a < to_day and b >= from_day}


def job_rows(cell, formation_fn, universe, mon, options):
    """Строки заданий клетки по месяцам: (y, x через +, from_day, to_day)."""
    rows = []
    for label, d0, d1 in mon:
        alive_all = {e: alive_in(universe.get(e, []), d0, d1) for e in universe}
        ctx = dict(exchange=cell["ex"], month=label, form_from_day=d0 - FORM_DAYS, form_to_day=d0,
                   trade_from_day=d0, trade_to_day=d1, alive=alive_all.get(cell["ex"], set()),
                   alive_all=alive_all, options=options)
        for y, xs in formation_fn(ctx):
            rows.append((y, "+".join(xs), d0, d1))
    return rows


def write_jobs(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["y", "x", "from_day", "to_day"])
        w.writerows(rows)


def run_command(cell, a, params, jobs, daily, trades=None, trade_jobs=()):
    """Командная строка `alpha statarb run` для клетки."""
    cmd = [a.bin, "statarb", "run", "--kind", cell["kind"], "--cell", cell["id"], "--jobs", jobs,
           "--panels", f"{a.panels_root}/panels-{cell['ex']}", "--daily-out", daily]
    if cell.get("ex_b"):
        cmd += ["--panels-b", f"{a.panels_root}/panels-{cell['ex_b']}"]
    if cell["beta"] == 1 or cell["beta"] == "1":
        cmd += ["--beta-fixed", "1"]
    if trades and trade_jobs:
        cmd += ["--trades-out", trades, "--trade-jobs", ",".join(map(str, trade_jobs))]
    for k in REQUIRED:
        cmd += [f"--{k.replace('_', '-')}", str(params[k])]
    return " ".join(shlex.quote(c) for c in cmd)


def parse_kv(items, what):
    out = {}
    for it in items:
        if "=" not in it:
            raise SystemExit(f"{what}: нужен k=v, получено {it!r}")
        k, v = it.split("=", 1)
        out[k] = v
    return out


def cmd_plan(a):
    params = parse_kv(a.param, "--param")
    miss = [k for k in REQUIRED if k not in params]
    if miss:
        raise SystemExit(f"не заданы параметры клетки (значений по умолчанию нет): {miss}")
    options = parse_kv(a.formation_opt, "--formation-opt")
    forms = parse_kv(a.cell_formation, "--cell-formation")  # id клетки = имя модуля формирования
    waves = {int(w) for w in a.waves.split(",")}
    universe = read_alive(a.universe)
    mon = months(a.first_month, a.last_month)
    os.makedirs(a.out_dir, exist_ok=True)
    manifest, steps, script = [], [], ["#!/usr/bin/env bash", "# TK-484: клетки по волнам; суточный csv каждой клетки пишется сразу", "set -eu"]
    for cell in sorted(cells(), key=lambda c: (c["wave"], c["id"])):
        if cell["wave"] not in waves or (a.only and cell["id"] not in a.only):
            continue
        st = dict(cell=cell["id"], wave=cell["wave"], variant=cell["id"].split("-")[0])
        if cell.get("blocked"):
            manifest.append(dict(st, status="blocked", why=cell["blocked"]))
            continue
        name = forms.get(cell["id"], cell["formation"])
        if name not in fm.FORMATIONS:
            manifest.append(dict(st, status="no_formation", why=f"формирование {name!r} не зарегистрировано"))
            continue
        fn = fm.get(name)
        opts = {k: v for k, v in options.items() if ":" not in k}
        opts.update({k.split(":", 1)[1]: v for k, v in options.items() if k.startswith(cell["id"] + ":")})
        try:
            rows = job_rows(cell, fn, universe, mon, dict(opts, exch_a=cell["ex"], exch_b=cell.get("ex_b", "")))
        except KeyError as e:
            manifest.append(dict(st, status="no_option", why=f"формированию {name!r} не хватает --formation-opt {e.args[0]}"))
            continue
        jobs = os.path.join(a.out_dir, f"jobs-{cell['id']}.csv")
        daily = os.path.join(a.out_dir, f"daily-{cell['id']}.csv")
        write_jobs(jobs, rows)
        cmd = run_command(cell, a, params, jobs, daily, os.path.join(a.out_dir, f"trades-{cell['id']}.csv"), a.trade_jobs)
        script.append(f"[ -e {shlex.quote(daily)} ] || {cmd}")
        steps.append("\t".join([cell["id"], daily, f"{a.panels_root}:{jobs}", cmd]))
        manifest.append(dict(st, status="planned", formation=name, stub=bool(getattr(fn, "stub", False)),
                             jobs=len(rows), months=len(mon), jobs_file=jobs, daily_file=daily))
    with open(os.path.join(a.out_dir, "run-waves.sh"), "w", newline="\n") as f:
        f.write("\n".join(script) + "\n")
    with open(os.path.join(a.out_dir, "steps.tsv"), "w", newline="\n") as f:
        f.write("\n".join(steps) + ("\n" if steps else ""))
    with open(os.path.join(a.out_dir, "manifest.json"), "w") as f:
        json.dump(dict(params=params, months=[m[0] for m in mon], cells=manifest), f, ensure_ascii=False, indent=1)
    for m in manifest:
        print(m["cell"], m["status"], m.get("jobs", ""), m.get("why", ""), "STUB" if m.get("stub") else "")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("plan")
    q.add_argument("--universe", required=True, help="statarb-universe-<дата>.csv ворот")
    q.add_argument("--first-month", required=True)
    q.add_argument("--last-month", required=True)
    q.add_argument("--waves", required=True, help="номера волн через запятую")
    q.add_argument("--only", action="append", default=[], help="id клетки (можно несколько); без флага — все волны")
    q.add_argument("--bin", required=True)
    q.add_argument("--panels-root", required=True, help="каталог с panels-<биржа>/")
    q.add_argument("--out-dir", required=True)
    q.add_argument("--param", action="append", default=[], help="k=v: " + ",".join(REQUIRED))
    q.add_argument("--formation-opt", action="append", default=[], help="k=v для модуля формирования")
    q.add_argument("--cell-formation", action="append", default=[], help="id клетки=модуль формирования")
    q.add_argument("--trade-jobs", type=int, nargs="*", default=[], help="номера заданий для сверки сделок")
    a = p.parse_args(argv)
    if a.cmd == "plan":
        cmd_plan(a)


if __name__ == "__main__":
    main()
