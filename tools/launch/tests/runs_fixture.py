"""TK-244: фикстурные каталоги прогонов всех пяти статусов (для check_page и тестов) — не на calc, во временном каталоге.
build(base) -> (cfg-подмена, текст `alsched ps`). Хеши — настоящие из Летописи (plan_rows / master.have_hashes), сделки — условные."""
import json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
import launch

A = 1769904000000   # 2026-02-01 UTC
H, D = 3_600_000, 86_400_000


def _run(base, rid, new_hm, hyps, days=0, total=0, psim=(), overlay=None, done=False, result=None, claim_ago=None):
    d = os.path.join(base, rid)
    os.makedirs(os.path.join(d, "psim"), exist_ok=True)
    ts = launch.datetime.datetime.fromtimestamp(time.time() - 400 * 60, launch.datetime.timezone(launch.datetime.timedelta(hours=4))).strftime("%Y-%m-%dT%H:%M:%S+04:00")
    json.dump({"id": rid, "ts": ts, "hyps": hyps, "hm_new": sum(len(v) for v in new_hm.values()), "new_hm": new_hm}, open(os.path.join(d, "run.json"), "w", encoding="utf-8"))
    if total:
        open(os.path.join(d, "days.txt"), "w", encoding="utf-8").write("".join(f"feb {i}\n" for i in range(total)))
    for i in range(days):
        x = os.path.join(d, "out", f"run-feb-{i}")
        os.makedirs(os.path.join(x, "claim"))
        open(os.path.join(x, "done"), "w").close()
        if claim_ago:
            os.utime(os.path.join(x, "claim"), (time.time() - claim_ago, time.time() - claim_ago))
    for m in psim:
        for c in (0, 3):
            open(os.path.join(d, "psim", f"closes-p14f1-cap{c}-{m}.json"), "w").write("{}")
    if overlay:
        key, trades = overlay
        for m in {mm for ms in new_hm.values() for mm in ms}:
            json.dump({key: {m: {"0": trades, "3": trades}}}, open(os.path.join(d, "psim", f"closes-p14f1-cap0-{m}.json"), "w"))
        with open(os.path.join(d, "overlay.jsonl"), "w", encoding="utf-8") as f:
            for h, ms in new_hm.items():
                for m in ms:
                    f.write(json.dumps({"cell_id": h, "month": m, "ext": {"cell_line": "form state"}}) + "\n")
        open(os.path.join(d, "psim", "psim-p14f1.done"), "w").close()
    if result:
        json.dump(result, open(os.path.join(d, "result.json"), "w", encoding="utf-8"))


def build(base):
    import master
    cfg = launch.load_config()
    rows = launch.plan_rows(cfg["protocol"], cfg["fraction"])
    have = master.have_hashes()
    canon = next((r for r in rows if have.get(r["hash"])), rows[0])
    canon_m = sorted(have.get(canon["hash"], {"2026-02"}))[:1]
    routes = launch.load_routes()
    todo = [r for r in rows if "2026-02" not in have.get(r["hash"], ()) and routes.get(r["hash"], "cells") in launch.COUNTABLE]   # форма-считаемые без февраля: у «вне формы» плана к счёту ноль
    fresh = todo[:2]
    rest = [r for r in todo[2:]]
    fresh3 = next((r for r in rest if r["hyp"] not in {x["hyp"] for x in fresh}), rest[0])   # другая гипотеза, если есть — запрос в очереди даёт «N хеш-мес»
    run_dir, req_dir = os.path.join(base, "out"), os.path.join(base, "req")
    os.makedirs(run_dir)
    os.makedirs(req_dir)
    tr = [[A + H, 10.0, "BTC"], [A + D + H, -4.0, "ETH"], [A + 3 * D + H, 6.0, "SOL"]]
    _run(run_dir, "L-20261015-014502", {fresh[0]["hash"]: ["2026-02", "2026-03"]}, [fresh[0]["hyp"]], days=100, total=200, claim_ago=3600)
    _run(run_dir, "L-20261014-205900", {fresh[1]["hash"]: ["2026-02"]}, [fresh[1]["hyp"]], days=3, total=3, psim=["2026-02"], overlay=("state@form", tr))
    _run(run_dir, "L-20261014-120000", {canon["hash"]: canon_m}, [canon["hyp"]], days=3, total=3, psim=canon_m, overlay=("state@form", tr))
    _run(run_dir, "L-20261014-184100", {fresh[0]["hash"]: ["2026-02"]}, [fresh[0]["hyp"]], result={"ok": False, "why": "потолок 501 хеша, в плане 640. Ничего не посчитано."})
    json.dump({"hyps": [fresh3["hyp"]], "months": ["2026-02"], "axes": {}, "groups": []}, open(os.path.join(req_dir, "L-20261015-022210.json"), "w", encoding="utf-8"))
    ps = ("ядра: занято 15/16 по заявкам; в очереди 1\n"
          "1015014502 running prod    ядер=12 L-20261015-014502     252.0 мин rc= env BIN=/x bash run-launch.sh\n"
          "1015022210 queued  prod    ядер=12 L-20261015-022210       0.0 мин rc= env BIN=/x bash run-launch.sh\n"
          "1014170116 running gate    ядер= 3 tk231-psim              38.0 мин rc= bash psim.sh\n")
    return cfg | {"run_dir": run_dir, "request_dir": req_dir}, ps
