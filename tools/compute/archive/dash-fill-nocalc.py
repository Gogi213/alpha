#!/usr/bin/env python3
"""TK-018: заполнить варианты П-07 «нет расчёта» (`nocalc`), которые теперь посчитаны на деке, из свежей выгрузки
portfolio-sim (`dash-p07-all-dump.py` → `p07-all-r4.json`). Свои суммы не считаются: счёт, kpi по сделкам и закрытия —
теми же функциями, что `dash-add-p07-all.py` (account_summary, trade_stats, acc_augsep, by_variant); kpi07 — функциями
`dash-kpi07-all.py` (point, max_gap, перенос вердикта из проверенных Судьёй файлов). Прежние варианты не трогаются.

    python tools/compute/dash-fill-nocalc.py --data data/titration-dashboard/data-v39.json \\
        --dump data/titration-dashboard/p07-all-r4.json --out data/titration-dashboard/data-v40pre.json

Сверка до записи: у уже заполненных соседей (`--check`) пересчёт из выгрузки теми же функциями == страница
(account aug/sep/augsep, kpi aug/sep/augsep, by_variant, kpi07 frac/n_main/max_gap) — иначе отказ.
Сделки клеток П-07 на странице не хранятся (как с v33 и у TK-009): kpi по сделкам — есть, список — нет.
"""
import argparse
import datetime as dt
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def mod(name, fn):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fn))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ap = mod("ap", "dash-add-p07-all.py")
k7 = mod("k7", "dash-kpi07-all.py")
merge, ap07, RU = ap.merge, ap.ap07, ap.RU
TAIL = " — нет расчёта portfolio-sim (прогона нет или счёт пуст)"
TRADES_NOTE = "список сделок на странице только у главных и кандидатов (размер страницы ≤ 14 МБ)"


def norm(x):
    return json.loads(json.dumps(x, ensure_ascii=False))


def reviewed_map(stage12, tk004):
    """Как в `dash-kpi07-all.main`: ключ страницы → (источник, доля, вердикт) из проверенных Судьёй файлов."""
    rv = {}
    for name, c in json.load(open(stage12, encoding="utf-8"))["cells"].items():
        if c.get("kpi"):
            rv[k7.stage12_key(name)] = ("p07-stage12-2026-09-27", c["kpi"]["frac_gt_5d"], c["kpi"]["verdict"])
    T = json.load(open(tk004, encoding="utf-8"))
    for name, c in T["g85b"].items():
        rv[k7.tk004_key(name)] = ("p07-tk004-stage2-2026-09-28", c["frac"], c["verdict"])
    for name, c in T["g85a_h6"].items():
        if name != "base":
            rv[k7.stage12_key(name)] = ("p07-tk004-stage2-2026-09-28 (g85a_h6)", c["frac"], c["verdict"])
    return rv


def kpi07_of(key, b, rv):
    """kpi07 новой строки — как ветка «добавлен» в `dash-kpi07-all.main`."""
    cl = [tuple(x) for pk in k7.PK for x in b["closes"][pk]]
    frac, n_main = k7.point(cl)
    gap, gap_c = k7.max_gap(cl)
    k = {"frac": frac, "n_main": n_main, "max_gap_h": gap, "max_gap_censored": gap_c, "verdict": "только доля",
         "note": "точка на двух месяцах; устойчивость по суткам/монете и бутстреп не считались (TK-008)"}
    if key in rv:
        src, rfrac, verdict = rv[key]
        assert rfrac == frac, (src, key, rfrac, frac)
        k.update(verdict=verdict, note=f"вердикт — из {src} (проверено Судьёй); доля пересчитана из закрытий")
    return k


def compute(b, pos, dep, spans):
    """Строка варианта из выгрузки — как ветка `b is not None` в `dash-add-p07-all.main` (сделки не хранятся)."""
    grid = lambda pk: next(g for g in b["grid"] if g["epoch"] == RU[pk])
    acc, kpi = {}, {}
    for pk in RU:
        acc[pk] = merge.account_summary(grid(pk), dep, spans[pk])
        if b["trades"]:
            kpi[pk] = merge.trade_stats(b["trades"][pk], pos, dep, spans[pk])
    if b["trades"]:
        kpi["augsep"] = merge.trade_stats(b["trades"]["aug"] + b["trades"]["sep"], pos, dep, spans["augsep"])
    acc["augsep"] = ap.acc_augsep(acc["aug"], acc["sep"], dep, spans["aug"], spans["sep"])
    closes = {pk: [tuple(x) for x in b["closes"][pk]] for pk in RU}
    closes["augsep"] = closes["aug"] + closes["sep"]
    return acc, kpi, ap07.by_variant(closes), {pk: len(b["closes"][pk]) for pk in RU}


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--data", required=True)
    a.add_argument("--dump", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--stage12", default="docs/findings/p07-stage12-2026-09-27.json")
    a.add_argument("--tk004", default="docs/findings/p07-tk004-stage2-2026-09-28.json")
    a.add_argument("--check", nargs="+", default=["p07a:h9-pause30", "p07b:h9r-k1", "p07b:h9-pause0"])
    a = a.parse_args()
    assert os.path.abspath(a.out) != os.path.abspath(a.data), "вход не перезаписывается"
    D = json.load(open(a.data, encoding="utf-8"))
    B = json.load(open(a.dump, encoding="utf-8"))
    spans = {}
    for p in D["periods"]:
        d0 = dt.datetime.strptime(p["from"], "%Y-%m-%d")
        spans[p["key"]] = int(((dt.datetime.strptime(p["to"], "%Y-%m-%d") - d0).days + 1) * 1440)
    pos, dep = D["position_usd"], D["deposit_usd"]
    got = {}
    for name, b in B.items():
        side, suf = ap.suffix(name)
        key = {"read-a-base": "g85", "read-b-base": "g85b"}.get(name, f"p07{side}:{suf}")
        if b.get("closes") and any(b["closes"][pk] for pk in RU):
            got[key] = b
    rv = reviewed_map(a.stage12, a.tk004)
    A, K, BV = D["account"], D["kpi"], D["nh"]["by_variant"]

    # сверка соседей: пересчёт из выгрузки == страница
    for key in a.check:
        acc, kpi, bv, ncl = compute(got[key], pos, dep, spans)
        for pk in ("aug", "sep", "augsep"):
            assert norm(acc[pk]) == A[pk][key], ("account", key, pk)
            assert norm(kpi.get(pk)) == K[pk].get(key), ("kpi", key, pk)
            assert norm(bv[pk]) == BV[key][pk], ("by_variant", key, pk)
        meta = next(v for v in D["variants"] if v["key"] == key)
        assert meta["n_closes"] == ncl, ("n_closes", key)
        mine, page = kpi07_of(key, got[key], rv), BV[key]["kpi07"]
        for f in ("frac", "n_main", "max_gap_h", "max_gap_censored", "verdict"):
            pv = {pk: page[f][pk] for pk in k7.PK} if f in ("frac", "n_main") else page[f]
            assert norm(mine[f]) == pv, ("kpi07", key, f, mine[f], pv)
        print("сверка", key, "ок: n", acc["aug"]["n"], "/", acc["sep"]["n"], "kpi07", mine["frac"], mine["verdict"])

    filled, left = [], []
    vrow_keys = {k for r in D["vrows"] for l in r.get("lines", []) for k in l["keys"]}
    for meta in D["variants"]:
        if not meta.get("nocalc"):
            continue
        key = meta["key"]
        b = got.get(key)
        if b is None:
            left.append(key)
            continue
        assert key not in A["aug"] and key not in BV, key
        assert key in vrow_keys, ("нет в раскладке vrows", key)
        side, suf = key[3], key.split(":", 1)[1]
        acc, kpi, bv, ncl = compute(b, pos, dep, spans)
        for pk in ("aug", "sep", "augsep"):
            A[pk][key] = acc[pk]
            if pk in kpi:
                K[pk][key] = kpi[pk]
        BV[key] = bv
        BV[key]["kpi07"] = kpi07_of(key, b, rv)
        # статус — правило dash-add-p07-all; у Г-85а h9r-f* записки «вердикт Судьи 82b0454» нет: порог f там не судился
        if side == "a" and suf.startswith("h9r-"):
            status = "не определена (охват < 10 сигналов/мес.)" if suf.startswith("h9r-s") else ap.FAIL
        elif side == "a" and suf in ap.STAGE12:
            status = ap.FAIL
        else:
            status = ap.REVIEW
        assert meta["label"].endswith(TAIL), key
        meta["label"] = meta["label"][: -len(TAIL)] + ", форма " + b["form"] + ", набор " + b["set"]
        meta["status"] = status
        del meta["nocalc"]
        meta["n_closes"] = ncl
        meta["trades_note"] = TRADES_NOTE
        filled.append(key)
        k = BV[key]["kpi07"]
        print(f"{key}: n {acc['aug']['n']}/{acc['sep']['n']}, $ {acc['aug']['net_usd']}/{acc['sep']['net_usd']}, "
              f"kpi07 frac {k['frac']['aug']}/{k['frac']['sep']} ({k['verdict']}), max_gap {k['max_gap_h']} ч, статус «{status}»")

    D["generated_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        json.dump(D, f, ensure_ascii=False, separators=(",", ":"))
    print(f"заполнено {len(filled)}; осталось nocalc {len(left)} {left} (в выгрузке нет или счёт пуст)")
    print(f"{a.out}: {os.path.getsize(a.out) / 1e6:.2f} МБ")


if __name__ == "__main__":
    main()
