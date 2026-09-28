#!/usr/bin/env python3
"""TK-018 (v39 → v40): период «Июль» у ВСЕХ вариантов дашборда — контракт TK-017 (`dash-add-jul.py`) без изменений:
`account['jul'][key]` = `merge.account_summary` строки ps.json «июль» (span 31 сут); `nh.by_variant[key].kpi07`:
`frac['jul']`, `n_main['jul']`, `max_gap_h_jul`, `max_gap_censored_jul`, `states['jul']` — `kpi-newhigh.rolling_kpi`
(закрытия июля, h = 5 сут, границы месяца `p07-jul-read.patch_july`), сверка с `jall-read.json`.
Метка (владелец TK-018): отобранные по В-146 (34 клетки TK-009 — «проверка на июле», итог — `j9-read.json`
`vs_k1.result`) и 5 форм TK-010 (вердикт Судьи 413da17, не трогаются — только сверка n и $); остальные —
`verdict_jul = «описание, не отобран»`, числа показываются. П-05 — ещё `p05.cells[key].months.jul` (поля авг/сен:
n, usd, delta/lo/hi — Δ$ к главному по суткам, dd_pct, win). Выгрузки: `dash-jall-dump.py` (jall.json), j9-read.json.

    python tools/compute/dash-add-jall.py --data data/titration-dashboard/data-v39.json \\
        --dump data/titration-dashboard/jall.json --j9 data/titration-dashboard/j9-read.json \\
        --out data/titration-dashboard/data-v40.json
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


merge, kn, pjr = mod("merge", "titration-dashboard-merge.py"), mod("kn", "kpi-newhigh.py"), mod("pjr", "p07-jul-read.py")
pjr.patch_july(kn)
SPECIAL = {"read-a-base": "g85", "read-b-base": "g85b", "m-main": "btc4h_trail", "m-take": "btc4h_take",
           "m-btc1h": "cand", "m-nofilter": "nofilter", "p02-g105": "g105", "p02-g86": "g86", "p02-g08": "g08",
           "h9-b-h9r-p07b-base-k1": "p07b:h9r-k1", "h9-b-h9r-k1": None}  # K≤1 — два имени одного варианта, берётся первое
TK010 = {"btc4h_trail", "g85", "g85b", "p07b:h9r-k1", "p07a:h2-fr1"}
DESC = "описание, не отобран"


def key_of(name):
    if name in SPECIAL:
        return SPECIAL[name]
    if name.startswith("p05-"):
        return name
    side, rest = name.split("-", 2)[1], name.split("-", 2)[2]
    if name.startswith("read-"):
        return f"p07{side}:{rest}"
    return f"p07{side}:{rest if rest.startswith('h9r-') else 'h9-' + rest}"


def j9_key(cell):
    """Имя клетки `p07-tk009-jul-read` → ключ страницы."""
    if cell == "b-k1":
        return "p07b:h9r-k1"
    if cell in ("k1f25", "k1cap5"):
        return f"p07b:h9r-p07b-base-{cell}"
    return f"p07b:h9r-{cell}-k1"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--dump", required=True)
    ap.add_argument("--j9", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    D = json.load(open(a.data, encoding="utf-8"))
    B = json.load(open(a.dump, encoding="utf-8"))
    J9 = json.load(open(a.j9, encoding="utf-8"))
    sel = {j9_key(c): r for c, r in J9.items() if c != "_meta"}
    span = 31 * 1440
    acc_j = D["account"].setdefault("jul", {})
    n_add = n_desc = n_sel = n_undef = 0
    missing = []
    for name, rec in B.items():
        if name == "_meta":
            continue
        key = key_of(name)
        if key is None:
            continue
        is_p05 = key.startswith("p05-")
        known = key in D["p05"]["cells"] if is_p05 else key in D["account"]["aug"]
        if not known:
            missing.append(key)
            continue
        r = rec["read"]
        k07 = D["nh"]["by_variant"].setdefault(key, {}).setdefault("kpi07", {})
        if "undefined" in r or "grid" not in rec:
            k07["verdict_jul"] = "не определена: " + r.get("undefined", "нет счёта")
            n_undef += 1
            continue
        acc = merge.account_summary(rec["grid"], D["deposit_usd"], span)
        assert (acc["n"], acc["net_usd"]) == (r["n_trades"], round(r["usd"]["est"], 2)), (name, acc["n"], acc["net_usd"])
        roll = kn.rolling_kpi(sorted(tuple(x) for x in rec["closes"]), h_days=5)["aug"]
        if r["kpi"]:
            assert (roll["frac_gt_h"], roll["n_main"]) == (r["kpi"]["frac_gt_5d"], r["kpi"]["n_main"]), name
        if key in TK010:  # вердикт Судьи TK-010 уже на странице — только сверка
            old = D["account"]["jul"][key]
            assert (old["n"], old["net_usd"]) == (acc["n"], acc["net_usd"]), (key, old["n"], acc["n"])
            continue
        if not is_p05:
            acc_j[key] = acc
        k07.setdefault("frac", {})["jul"] = roll["frac_gt_h"]
        k07.setdefault("n_main", {})["jul"] = roll["n_main"]
        k07["max_gap_h_jul"] = None if roll["max"]["max"] is None else round(roll["max"]["max"], 1)
        k07["max_gap_censored_jul"] = roll["max"]["max_censored"]
        k07.setdefault("states", {})["jul"] = (r["kpi"] or {}).get("state")
        if key in sel and "vs_k1" in sel[key]:
            s = sel[key]
            assert s["n_trades"] == acc["n"], (key, s["n_trades"], acc["n"])
            k07["verdict_jul"] = "проверка на июле: " + s["vs_k1"]["result"]
            k07["verdict_jul_src"] = "TK-009 июль по В-146 (j9-read.json, С1–С3 к K≤1)"
            n_sel += 1
        else:
            k07["verdict_jul"] = DESC
            k07["verdict_jul_src"] = "TK-018: июль для всех вариантов (владелец 28.09 11:40)"
            n_desc += 1
        if is_p05:
            dm = r.get("diff_main_usd") or {}
            D["p05"]["cells"][key]["months"]["jul"] = {
                "n": acc["n"], "usd": acc["net_usd"], "delta": dm.get("est"),
                "lo": (dm.get("ci95") or [None, None])[0], "hi": (dm.get("ci95") or [None, None])[1],
                "dd_pct": acc["dd_pct"], "win": acc["win_rate"]}
        n_add += 1
    D["generated_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = json.dumps(D, ensure_ascii=False, separators=(",", ":"))
    open(a.out, "w", encoding="utf-8").write(s)
    print(f"июль добавлен: {n_add} (проверка {n_sel}, описание {n_desc}), не определено {n_undef}; "
          f"нет на странице: {len(missing)} {missing[:5]}")
    print(a.out, f"{len(s.encode('utf-8')) / 1e6:.1f} МБ")


if __name__ == "__main__":
    main()
