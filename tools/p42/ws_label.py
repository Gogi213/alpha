"""П-42 §13.13: метка `ws` (вход «с обратной стороны») на сделках `b0` — сколько их, $, доля стопов; разрез «убил бы `pb`».
Запуск на srv (pandas не нужен):

  python3 ws_label.py <544|566>

Читает /data/p42-t<U>/out/ws/{b0lab,b0,b0pk,b0pb}/<сторона>-detail.jsonl (поле `ws` пишет только ap ≠ 0 → `b0lab`), минутки
/data/klines/binance/<СИМ>/<день>.csv. Сделка `b0` тождественна сделке `b0lab` (ap=2 ничего не отвергает; гейт — CSV побайтово).
«pb убил бы» — по минуткам: есть минута u, born ≤ u < sig_min, с high ≥ цены уровня (шорт: low ≤); цена уровня — на момент сигнала
(уровень может менять цену по версиям — оценка); точная проверка — ключи (монета, entry_ms) сделок b0, которых нет в `b0pb` (каскад слотов/пауз).
"""
import csv
import datetime
import json
import os
import sys
from collections import defaultdict

T0 = 1_788_220_800_000
SIDES = ("long", "short")


def detail(vdir, side):
    p = os.path.join(vdir, side + "-detail.jsonl")
    return [json.loads(x) for x in open(p, encoding="utf-8")]


_kl = {}


def klines(sym, day):
    k = (sym, day)
    if k not in _kl:
        rows = {}
        p = "/data/klines/binance/%s/%s.csv" % (sym, day)
        if os.path.exists(p):
            for r in csv.DictReader(open(p)):
                rows[(int(r["open_time"]) - T0) // 60000] = (float(r["high"]), float(r["low"]))
        _kl[k] = rows
    return _kl[k]


def mday(t):
    return (datetime.datetime(1970, 1, 1) + datetime.timedelta(milliseconds=T0 + t * 60000)).strftime("%Y-%m-%d")


def pb_would_kill(d):
    """Минутка в [born, sig_min) дошла до уровня (≥ для лонга, ≤ для шорта)."""
    lp = d["level_px_e8"] / 1e8
    for u in range(d["born"], d["sig_min"]):
        h = klines(d["sym"], mday(u)).get(u)
        if h is None:
            continue
        if d["side"] == "long" and h[0] >= lp - 1e-12:
            return True
        if d["side"] == "short" and h[1] <= lp + 1e-12:
            return True
    return False


def summ(rows):
    n = len(rows)
    usd = sum(r["net"] for r in rows)
    st = sum(1 for r in rows if r["kind"] == "stop")
    return n, usd, (usd / n if n else 0.0), (st / n if n else 0.0)


def fmt(rows):
    n, usd, avg, st = summ(rows)
    return "n=%4d  $%+9.2f  ср. $%+7.2f  стопов %3.0f%%" % (n, usd, avg, st * 100)


def main():
    U = sys.argv[1]
    root = "/data/p42-t%s/out/ws" % U
    out = {}
    pbkeys = {s: None for s in SIDES}
    if os.path.isdir(os.path.join(root, "b0pb")):
        for s in SIDES:
            pbkeys[s] = {(d["sym"], d["entry_ms"]) for d in detail(os.path.join(root, "b0pb"), s)}
    pkkeys = {s: {(d["sym"], d["entry_ms"]) for d in detail(os.path.join(root, "b0pk"), s)} for s in SIDES}
    for s in SIDES:
        dl = detail(os.path.join(root, "b0lab"), s)
        for d in dl:
            d["pbk"] = pb_would_kill(d)
            d["inpk"] = (d["sym"], d["entry_ms"]) in pkkeys[s]
            if pbkeys[s] is not None:
                d["pbgone"] = (d["sym"], d["entry_ms"]) not in pbkeys[s]
        res = {}
        for name, sel in (
            ("все сделки b0", lambda d: True),
            ("ws=true (с обратной стороны)", lambda d: d["ws"]),
            ("ws=false (подход с правильной стороны)", lambda d: not d["ws"]),
            ("среди b0pk: все", lambda d: d["inpk"]),
            ("среди b0pk: ws=true", lambda d: d["inpk"] and d["ws"]),
            ("среди b0pk: ws=false", lambda d: d["inpk"] and not d["ws"]),
            ("pb убил бы (минутки)", lambda d: d["pbk"]),
            ("pb убил бы и ws=true", lambda d: d["pbk"] and d["ws"]),
            ("pb убил бы и ws=false", lambda d: d["pbk"] and not d["ws"]),
            ("ws=true и pb не убил бы", lambda d: d["ws"] and not d["pbk"]),
        ):
            rows = [d for d in dl if sel(d)]
            res[name] = {"n": len(rows), "usd": sum(r["net"] for r in rows), "stops": sum(1 for r in rows if r["kind"] == "stop")}
            print("  %-5s %-42s %s" % (s, name, fmt(rows)))
        if pbkeys[s] is not None:
            gone = [d for d in dl if d["pbgone"]]
            print("  %-5s %-42s %s" % (s, "нет в b0pb (точно, ключи)", fmt(gone)))
            both = sum(1 for d in dl if d["pbgone"] and d["pbk"])
            print("        нет в b0pb и pb убил бы по минуткам: %d из %d; минутки без b0pb-расхождения: %d" % (both, len(gone), sum(1 for d in dl if d["pbk"] and not d["pbgone"])))
            res["нет в b0pb"] = {"n": len(gone), "usd": sum(r["net"] for r in gone)}
        out[s] = res
    json.dump(out, open(os.path.join(root, "ws-label.json"), "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    print("== вселенная %s: метка ws на сделках b0" % sys.argv[1])
    main()
