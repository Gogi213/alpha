#!/usr/bin/env python3
"""TK-018 (В-151): защиты дашборда на июле 2026 для 9 вариантов со вкладкой «защиты» (главный, take, кандидат BTC 1 ч,
без фильтра, Г-105, Г-85, Г-86, Г-08, Г-85б) — те же ступени, что `titration-dashboard-merge.protection_steps`
(без защит; + выключатель BTC −1,5 %/1 ч и исключение прокидов; + потолок 3), тем же `portfolio-sim.py` сеткой
`--max-pos 0,3 --btc-kill-bps 0,150 --exclude-set прокиды=<study/pump-exclude.csv>` (как `t21-psim-gate-deck.sh`).
Вход — сделки июля после busy-replay из чтения jall (`tmp-p07/jall-read/<имя>/jul`, у Г-08 — `jul-g08`), эпоха «июль»,
$2500/$500, TRX/KORU вне пула. Запуск — после `jall-read.finished` (дека):

    python3 ~/alpha/tmp-p07/jall-protect.py        # → tmp-p07/jall-prot/<имя>.json (+ -closes.json)
"""
import csv
import json
import os
import subprocess
import sys

HOME = os.path.expanduser("~/alpha")
READ = os.path.join(HOME, "tmp-p07/jall-read")
OUT = os.path.join(HOME, "tmp-p07/jall-prot")
JUL = os.path.join(HOME, "epochs/e-jul")
PORT_SIM = os.path.join(HOME, "tmp-kpi/portfolio-sim.py")  # тот же, что у чтений (p07-read.PORT_SIM)
DROP = "TRXUSDT,KORUUSDT"
# имя чтения jall → ключ страницы
NAMES = {"m-main": "btc4h_trail", "m-take": "btc4h_take", "m-btc1h": "cand", "m-nofilter": "nofilter",
         "p02-g105": "g105", "read-a-base": "g85", "p02-g86": "g86", "p02-g08": "g08", "read-b-base": "g85b"}


def excl():
    with open(os.path.join(HOME, "study/pump-exclude.csv"), encoding="utf-8") as fh:
        rows = [l.strip() for l in fh if l.strip() and not l.startswith("#")]
    return ",".join(r.split(",")[0] for r in rows[1:])


def main():
    res = json.load(open(os.path.join(READ, "jall-read.json"), encoding="utf-8"))
    os.makedirs(OUT, exist_ok=True)
    ex = excl()
    bad = 0
    for name, key in NAMES.items():
        r = res.get(name)
        if not r or "undefined" in r:
            print(f"{name}: нет чтения jall")
            bad += 1
            continue
        src = os.path.join(READ, name, "jul-g08" if name == "p02-g08" else "jul")
        # дом эпохи — e-jul: выключатель BTC берёт ход BTC за 1 ч из <дом>/study/regime (`load_btc1h`)
        j, co = os.path.join(OUT, f"{name}.json"), os.path.join(OUT, f"{name}-closes.json")
        rc = subprocess.run(["python3", PORT_SIM, "--epoch", f"июль={JUL}:{os.path.relpath(src, JUL)}", "--variant", f"{key}={r['set']}/{r['form']}",
                             "--klines", os.path.join(JUL, "study/klines"), "--klines", os.path.join(HOME, "study/klines"),
                             "--deposit-usd", "2500", "--position-usd", "500", "--max-pos", "0,3", "--day-stop-pct", "0",
                             "--btc-kill-bps", "0,150", "--exclude-set", f"прокиды={ex}", "--drop", DROP,
                             "--json", j, "--closes-out", co],
                            stdout=subprocess.DEVNULL, stderr=open(os.path.join(OUT, f"{name}.log"), "w")).returncode
        # сверка: «без защит» = счёт чтения jall (n)
        g0 = [g for g in json.load(open(j, encoding="utf-8"))["grid"] if g["max_pos"] == 0 and g["kill"] == 0.0
              and g["exclude"] == "нет"] if rc == 0 else []
        ok = rc == 0 and len(g0) == 1 and g0[0]["n"] == r["n_trades"]
        bad += not ok
        print(f"{name}: rc={rc} {'ok' if ok else 'РАЗНИЦА/сбой'}")
    print("ЗАЩИТЫ ИЮЛЯ:", "ok" if not bad else f"сбоев {bad}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
