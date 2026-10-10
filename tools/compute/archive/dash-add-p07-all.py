#!/usr/bin/env python3
"""TK-007 (v33): все посчитанные клетки П-07 Г-85а/Г-85б вариантами дашборда + раскладка строки «Вариант» по группам
(`D.vrows`) и статус каждого варианта (`variants[].status`). Счёт, закрытия и сделки — из выгрузки
`dash-p07-all-dump.py` (portfolio-sim прогонов, свои суммы не считаются); вливание — теми же функциями, что
`dash-add-p07.py` (account_summary, trade_stats, one_per_coin, by_variant).

    dash-add-p07-all.py --data data-v32.json --dump p07-all.json --stage12 docs/findings/p07-stage12-2026-09-27.json \\
        --out data-v33.json [--trades-max-mb 14]

Сверка: базы read-a-base / read-b-base == g85 / g85b страницы; клетки Г-85а из ступеней 1–2 == stage12 (n, $ ±0,02).
Вариант без выгрузки (нет ps.json или пустой счёт) — кнопка «нет расчёта» (`nocalc`).
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


merge, os1, ap07 = mod("merge", "titration-dashboard-merge.py"), mod("os1", "dash-one-source.py"), mod("ap07", "dash-add-p07.py")
RU = {"aug": "август", "sep": "сентябрь"}
# ось П-07: префикс суффикса → (подпись строки, текст кнопки по значению)
AXES = [
    ("h2-fr", "H2 отступ от P+1", lambda v: "+" + v + " тик"),
    ("h2-", "H2 ширина лестницы, σ", lambda v: "0," + v[1:]),
    ("h3-", "H3 размер стены", lambda v: "$" + str(int(v) // 1000) + "k"),
    ("h4-", "H4 возраст, с", lambda v: v),
    ("h5-", "H5 окно BTC", lambda v: v),
    ("h6-", "H6 стоп", lambda v: {"pct15": "pct1,5"}.get(v, v)),
    ("h7-", "H7 выход", lambda v: {"tr15x1": "tr1,5x1", "tr1x15": "tr1x1,5"}.get(v, v)),
    ("h8-", "H8 удержание, с", lambda v: v),
    ("h13-", "H13 стоп на уровень снятой стены", lambda v: v),
]
H9 = [("pause", "H9 пауза после закрытия, мин", lambda v: v.replace("Stop", "после стопа ")),
      ("cap", "H10 потолок позиций", lambda v: "≤ " + v)]
H9R = [("h9r-s", "H9р запрет после стопа, сетап", lambda v: "S" + v),
       ("h9r-k", "H14 подходов до K", lambda v: "K ≤ " + v),
       ("h9r-n", "H14 с N-го подхода", lambda v: "N ≥ " + v),
       ("h9r-f", "H1 порог f (TK-004 replay)", lambda v: "f" + v),
       ("h9r-keep", "H9р/H14 без фильтра (= база)", lambda v: "база")]
H9R_SHOWN = [x for x in H9R if x[0] != "h9r-n"]  # TK-007: «с N-го подхода» не показывать (владелец, п.3) — данные остаются


def acc_augsep(aa, asp, dep, sp_a, sp_s):
    """Счёт «авг+сен» из двух месячных счётов portfolio-sim — как у g85 на странице (398 = 297 + 101): суммы и
    веса по сделкам, просадка/пик/худший день — худший из месяцев, Шарп — по объединённым суткам."""
    n = aa["n"] + asp["n"]
    wsum = lambda f: sum(x[f] * x["n"] for x in (aa, asp) if x.get(f) is not None)
    net = round(aa["net_usd"] + asp["net_usd"], 2)
    worst = min((aa, asp), key=lambda x: x["worst_day_usd"])
    dd = max((aa, asp), key=lambda x: x["dd_usd"])
    daily = dict(aa["daily_usd"])  # сутки на стыке месяцев бывают в обоих счетах — складывать, не затирать
    for d, v in asp["daily_usd"].items():
        daily[d] = round(daily.get(d, 0) + v, 2)
    sh, so = merge.sharpe_sortino({d: v / dep * 100 for d, v in daily.items()})
    wn = {id(x): x["win_rate"] * x["n"] for x in (aa, asp)}
    def side(f, cnt):
        tot = sum(x[f] * cnt(x) for x in (aa, asp) if x.get(f) is not None)
        c = sum(cnt(x) for x in (aa, asp) if x.get(f) is not None)
        return round(tot / c, 2) if c else None
    gl = [x["net_usd"] / (x["profit_factor"] - 1) for x in (aa, asp) if x.get("profit_factor") not in (None, 1)]
    pf = None
    if len(gl) == 2:
        gw = sum(x["profit_factor"] * g for x, g in zip((aa, asp), gl))
        pf = round(gw / sum(gl), 3) if sum(gl) else None
    tim = [x.get("pct_time_in_market") for x in (aa, asp)]
    return {"n": n, "net_usd": net, "net_pct": round(net / dep * 100, 3), "dd_usd": dd["dd_usd"], "dd_pct": dd["dd_pct"],
            "recovery_factor": round(net / dd["dd_usd"], 3) if dd["dd_usd"] else None,
            "win_rate": round((wn[id(aa)] + wn[id(asp)]) / n, 4) if n else 0,
            "peak_n": max(aa["peak_n"], asp["peak_n"]), "peak_usd": max(aa["peak_usd"], asp["peak_usd"]),
            "worst_day": worst["worst_day"], "worst_day_usd": worst["worst_day_usd"], "worst_day_pct": worst["worst_day_pct"],
            "fill": round(wsum("fill") / n, 4) if n else 0, "sharpe": sh, "sortino": so, "daily_usd": daily,
            "avg_usd": round(net / n, 2) if n else None, "avg_bps": round(wsum("avg_bps") / n, 2) if n else None,
            "avg_win_bps": side("avg_win_bps", lambda x: wn[id(x)]), "avg_loss_bps": side("avg_loss_bps", lambda x: x["n"] - wn[id(x)]),
            "profit_factor": pf,
            "pct_time_in_market": round((tim[0] * sp_a + tim[1] * sp_s) / (sp_a + sp_s), 4) if None not in tim else None}


STAGE12 = {"h6-pct15": "H6-pct1.5", "h6-pct3": "H6-pct3", "h6-before": "H6-before", "h7-tr15x1": "H7-tr1.5x1",
           "h7-tr1x15": "H7-tr1x1.5", "h7-tr2x1": "H7-tr2x1", "h7-1to1": "H7-1to1", "h2-fr1": "H2-fr1", "h2-fr2": "H2-fr2",
           "h2-fr3": "H2-fr3", "h9-pause0": "H9-pause0(=base)", "h9-pause30": "H9-pause30", "h9-pause60": "H9-pause60",
           "h9-pause120": "H9-pause120", "h9-pauseStop60": "H9-pauseStop60", "h9-pauseStop120": "H9-pauseStop120",
           "h9-cap3": "H10-cap3", "h9-cap5": "H10-cap5"}
REVIEW, FAIL, BASE = "на проверке у Судьи", "не проходит", "база сравнения"


def suffix(dirname):
    """read-a-h4-900 → ('a', 'h4-900'); h9-a-pause30 → ('a', 'h9-pause30'); h9-a-h9r-k1 → ('a', 'h9r-k1')."""
    if dirname.startswith("read-"):
        return dirname[5], dirname[7:]
    side, rest = dirname[3], dirname[5:]
    return side, (rest if rest.startswith("h9r-") else "h9-" + rest)


def axis_of(suf, axes):
    body = suf[3:] if suf.startswith("h9-") else suf
    for pre, lab, txt in axes:
        if body.startswith(pre):
            return lab, txt(body[len(pre):] or body)
    return None


TK009_GROUP = "Г-85б K≤1 — пачка TK-009 (авг/сен, найдено поиском)"
TK009_T9 = {"market": "рынок (Г-86)", "early1": "ранний выход 1 с (Г-110)", "early2": "ранний выход 2 с (Г-110)",
            "early3": "ранний выход 3 с (Г-110)", "ttl60": "TTL 60 с (Г-129)", "ttl300": "TTL 300 с (Г-129)",
            "gone50be": "стена −50 % → б/у (П-04)", "gone90be": "стена −90 % → б/у (П-04)",
            "gone50tr1": "стена −50 % → трейл 1 (П-04)", "gone90tr1": "стена −90 % → трейл 1 (П-04)", "touch": "касание (Г-147)"}


def tk009_cell(suf):
    """Суффикс каталога пачки TK-009 → (имя клетки в tk009-batch.json, подпись оси, кнопка) или None.
    h9r-p07b-t9-market-k1 → p07b-t9-market; h9r-p07b-h3-25000-k1 → p07b-h3-25000; h9r-p07b-base-k1f25 → k1f25;
    h9r-p07b-base-k1 → «база K≤1» (= p07b:h9r-k1, ось None)."""
    if not suf.startswith("h9r-p07b-"):
        return None
    body = suf[len("h9r-"):]
    if body == "p07b-base-k1":
        return "база K≤1", None, None
    if body.startswith("p07b-base-k1"):
        v = body[len("p07b-base-k1"):]
        if v.startswith("f"):
            return "k1" + v, "K≤1 × H1 порог f", v
        return "k1" + v, "K≤1 × H10 потолок позиций", "≤ " + v[3:]
    assert body.endswith("-k1"), suf
    cell = body[:-3]
    rest = cell[len("p07b-"):]
    if rest.startswith("t9-"):
        return cell, "K≤1 × пачка TK-009", TK009_T9[rest[3:]]
    ax, val = rest.split("-", 1)
    lab = next(l for pre, l, _ in AXES if pre == ax + "-")
    return cell, "K≤1 × " + lab, ("$" + str(int(val) // 1000) + "k") if ax == "h3" else val.replace(".", ",")


def add_tk009(D, B, T, pos, dep, spans):
    """TK-009 (v37): только добавить клетки пачки TK-009 (каталоги h9-b-h9r-p07b-*) отдельной группой; прежние варианты,
    строки и данные не трогаются. Статус — §10 из tk009-batch (все candidate_rule = false); база K≤1 = p07b:h9r-k1."""
    grid = lambda b, pk: next(g for g in b["grid"] if g["epoch"] == RU[pk])
    cells, lines, metas, seen = T["cells"], {}, [], set()
    note = "кандидатов на июль нет; описание, найдено поиском, П-07 поправка 6 (" + T["note"] + ")"
    for name, b in sorted(B.items()):
        if not name.startswith("h9-b-"):
            continue
        side, suf = suffix(name)
        c = tk009_cell(suf)
        if c is None:
            continue
        cell, lab, btn = c
        seen.add(cell)
        ref = cells[cell]
        for pk in RU:
            g = grid(b, pk)
            assert g["n"] == ref["n"][pk] and abs(g["total_usd"] - ref["usd"][pk]) <= 0.02, (cell, pk, g["n"], g["total_usd"], ref["n"][pk], ref["usd"][pk])
        if lab is None:  # база K≤1 уже есть на странице как p07b:h9r-k1 — сверить и не дублировать
            for pk in RU:
                acc = D["account"][pk]["p07b:h9r-k1"]
                assert (acc["n"], acc["net_usd"]) == (grid(b, pk)["n"], round(grid(b, pk)["total_usd"], 2)), (pk, acc["n"], acc["net_usd"])
            continue
        assert ref["candidate_rule"] is False, cell
        key = f"p07{side}:{suf}"
        assert key not in D["account"]["aug"], key
        meta = {"key": key, "group": "p07b", "pill": "Г-85б · " + lab + ": " + btn, "btn": btn, "axis": lab,
                "label": "Г-85б (П-07), H14 K≤1 и клетка «" + lab + " = " + btn + "»: остальное как в базе Г-85б K≤1"
                         " (p07b:h9r-k1). Счёт — portfolio-sim пачки TK-009 (busy-replay, без TRX), форма " + b["form"] +
                         ", набор " + b["set"] + ("" if b["max_pos"] == 0 else f", потолок {b['max_pos']}"),
                "status": ref["state"] + " · найдено поиском, не кандидат на июль (TK-009)",
                "kpi_note": "TK-009 (docs/findings/tk009-batch-2026-09-28.md): §10 «" + ref["state"] + "», правило кандидата — нет; " + note,
                "trades_note": b.get("trades_note") or "список сделок на странице только у главных и кандидатов (размер страницы ≤ 14 МБ)"}
        for pk in RU:
            D["account"][pk][key] = merge.account_summary(grid(b, pk), dep, spans[pk])
            if b["trades"]:
                D["kpi"][pk][key] = merge.trade_stats(b["trades"][pk], pos, dep, spans[pk])
        if b["trades"]:  # сделки клеток П-07 на странице не хранятся (как в v33+), kpi по сделкам — остаётся
            D["kpi"]["augsep"][key] = merge.trade_stats(b["trades"]["aug"] + b["trades"]["sep"], pos, dep, spans["augsep"])
        D["account"]["augsep"][key] = acc_augsep(D["account"]["aug"][key], D["account"]["sep"][key], dep, spans["aug"], spans["sep"])
        meta["n_closes"] = {pk: len(b["closes"][pk]) for pk in RU}
        closes = {pk: [tuple(x) for x in b["closes"][pk]] for pk in RU}
        closes["augsep"] = closes["aug"] + closes["sep"]
        D["nh"]["by_variant"][key] = ap07.by_variant(closes)
        metas.append(meta)
        lines.setdefault(lab, []).append(key)
    miss = sorted(set(cells) - seen)
    assert not miss, ("клетки tk009 без каталога", miss)
    D["variants"] += metas
    order = ["K≤1 × пачка TK-009"] + ["K≤1 × " + l for _, l, _ in AXES] + ["K≤1 × H1 порог f", "K≤1 × H10 потолок позиций"]
    D["vrows"].append({"title": TK009_GROUP, "note": note, "lines": [{"axis": l, "keys": lines[l]} for l in order if l in lines]})
    return metas


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--dump", required=True)
    ap.add_argument("--stage12", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--trades-max-mb", type=float, default=14.0)
    ap.add_argument("--tk009", help="TK-009: только добавить клетки пачки (tk009-batch-*.json), прежнее не трогать")
    a = ap.parse_args()
    D = json.load(open(a.data, encoding="utf-8"))
    B = json.load(open(a.dump, encoding="utf-8"))
    if a.tk009:
        spans = {}
        for p in D["periods"]:
            d0 = dt.datetime.strptime(p["from"], "%Y-%m-%d")
            spans[p["key"]] = int(((dt.datetime.strptime(p["to"], "%Y-%m-%d") - d0).days + 1) * 1440)
        metas = add_tk009(D, B, json.load(open(a.tk009, encoding="utf-8")), D["position_usd"], D["deposit_usd"], spans)
        D["generated_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        s = json.dumps(D, ensure_ascii=False, separators=(",", ":"))
        open(a.out, "w", encoding="utf-8").write(s)
        print(a.out, f"{len(s.encode('utf-8')) / 1e6:.1f} МБ", "добавлено клеток TK-009:", len(metas),
              "строки:", [(l["axis"], len(l["keys"])) for l in D["vrows"][-1]["lines"]])
        return
    S12 = json.load(open(a.stage12, encoding="utf-8"))["cells"]
    grid = lambda b, pk: next(g for g in b["grid"] if g["epoch"] == RU[pk])
    for base, key in (("read-a-base", "g85"), ("read-b-base", "g85b")):
        for pk in RU:
            g, acc = grid(B[base], pk), D["account"][pk][key]
            assert (g["n"], round(g["total_usd"], 2)) == (acc["n"], acc["net_usd"]), (base, pk, g["n"], g["total_usd"], acc["n"], acc["net_usd"])
    spans = {}
    for p in D["periods"]:
        d0 = dt.datetime.strptime(p["from"], "%Y-%m-%d")
        spans[p["key"]] = int(((dt.datetime.strptime(p["to"], "%Y-%m-%d") - d0).days + 1) * 1440)
    pos, dep = D["position_usd"], D["deposit_usd"]
    checked, metas = [], []
    got = {}
    for name, b in sorted(B.items()):
        if name.endswith("-base"):
            continue
        side, suf = suffix(name)
        got[(side, suf)] = b
    # клетки без выгрузки у одной стороны — «нет расчёта» (есть у другой стороны или пустой счёт)
    all_suf = {s for _, s in got} | {"h6-before"}
    rows = {}  # (side, bucket) -> {axis: [(key, text)]}
    for side in "ab":
        for suf in sorted(all_suf, key=lambda s: (s.split("-")[0], s)):
            key = f"p07{side}:{suf}"
            is_h9 = suf.startswith("h9-") or suf.startswith("h9r-")
            bucket = ("h9r" if suf.startswith("h9r-") else "h9") if is_h9 else "grid"
            if side == "b" and is_h9:
                bucket = "b-extra"
            axes = H9R if suf.startswith("h9r-") else H9 if suf.startswith("h9-") else AXES
            ax = axis_of(suf, axes)
            if ax is None:
                continue
            if side == "b" and suf.startswith("h2-fr") or side == "a" and suf.startswith("h2-0"):
                continue
            lab, txt = ax
            b = got.get((side, suf))
            cand = "Г-85а" if side == "a" else "Г-85б"
            if side == "a" and suf.startswith("h9r-"):
                status = "не определена (охват < 10 сигналов/мес.)" if suf.startswith("h9r-s") else (FAIL if suf != "h9r-keep" else FAIL)
            elif side == "a" and suf in STAGE12:
                status = FAIL
            else:
                status = REVIEW
            meta = {"key": key, "group": "p07" + side, "pill": cand + " · " + lab + ": " + txt, "btn": txt, "axis": lab,
                    "label": cand + " (П-07), клетка «" + lab + " = " + txt + "»: остальное как в базе " + cand +
                    ". Счёт — portfolio-sim прогона чтения TK-004 (busy-replay, без TRX)" + ("" if b is None else ", форма " + b["form"] + ", набор " + b["set"]),
                    "status": status}
            if b is None:
                meta["nocalc"] = True
                meta["status"] = "нет расчёта"
                meta["label"] += " — нет расчёта portfolio-sim (прогона нет или счёт пуст)"
            else:
                if side == "a" and suf in STAGE12 and S12[STAGE12[suf]].get("kpi"):
                    c = S12[STAGE12[suf]]
                    for pk in RU:
                        g = grid(b, pk)
                        assert g["n"] == c["n"][pk] and abs(g["total_usd"] - c["usd"][pk]) <= 0.02, (suf, pk, g["n"], g["total_usd"], c["n"][pk], c["usd"][pk])
                    checked.append(suf)
                    k = c["kpi"]
                    meta["kpi_note"] = "stage12: доля часов > 5 сут авг " + f"{k['frac_gt_5d']['aug']:.3f}".replace(".", ",") + " / сен " + \
                        f"{k['frac_gt_5d']['sep']:.3f}".replace(".", ",") + ", клетка — «" + k["verdict"] + "»; Судья 4d49252: случай В"
                if side == "a" and suf.startswith("h9r-"):
                    meta["kpi_note"] = "вердикт Судьи 82b0454 (docs/findings/p07-h9r-h14-2026-09-27.md)"
                if b.get("trades_note"):
                    meta["trades_note"] = b["trades_note"]
                for pk in RU:
                    D["account"][pk][key] = merge.account_summary(grid(b, pk), dep, spans[pk])
                    if b["trades"]:
                        D["trades"][pk][key] = b["trades"][pk]
                        D["kpi"][pk][key] = merge.trade_stats(b["trades"][pk], pos, dep, spans[pk])
                if b["trades"]:
                    D["trades"]["augsep"][key] = b["trades"]["aug"] + b["trades"]["sep"]  # = счёт «авг+сен» (сумма месяцев, возврат Судьи 00:55)
                    D["kpi"]["augsep"][key] = merge.trade_stats(D["trades"]["augsep"][key], pos, dep, spans["augsep"])
                meta["n_closes"] = {pk: len(b["closes"][pk]) for pk in RU}
                closes = {pk: [tuple(x) for x in b["closes"][pk]] for pk in RU}
                closes["augsep"] = closes["aug"] + closes["sep"]
                D["nh"]["by_variant"][key] = ap07.by_variant(closes)
            metas.append(meta)
            rows.setdefault((side, bucket), {}).setdefault(lab, []).append(key)
    # TK-007 возврат Судьи 00:55: «авг+сен» — счёт portfolio-sim (сумма месяцев, как g85), не kpi по сделкам
    A = D["account"]
    ref = acc_augsep(A["aug"]["g85"], A["sep"]["g85"], dep, spans["aug"], spans["sep"])
    for f in ("n", "net_usd", "dd_usd", "recovery_factor", "win_rate", "fill", "sharpe", "sortino", "avg_usd", "avg_bps",
              "avg_win_bps", "avg_loss_bps", "profit_factor", "pct_time_in_market"):
        tol = 0.15 if f in ("sharpe", "sortino") else 0.011  # Шарп/Сортино g85: 3,80/4,33 у счёта против 3,73/4,22 по объединённым суткам (иной учёт пустых суток)
        assert abs((ref[f] or 0) - (A["augsep"]["g85"][f] or 0)) <= tol, (f, ref[f], A["augsep"]["g85"][f])
    fixed = []
    for key in [m["key"] for m in metas if not m.get("nocalc")] + ["g85b"]:
        if key in A["aug"] and key in A["sep"]:
            A["augsep"][key] = acc_augsep(A["aug"][key], A["sep"][key], dep, spans["aug"], spans["sep"])
            fixed.append(key)
    print("авг+сен из счёта:", len(fixed), "вариантов; g85 воспроизведён")
    # H9 пауза/потолок и H9р Г-85б, которых нет у Г-85б, но есть у Г-85а — заглушки «нет расчёта» уже созданы выше
    D["variants"] = [v for v in D["variants"] if not str(v["key"]).startswith("p07")] + metas
    order = lambda side, bucket, axes: [{"axis": lab, "keys": rows[(side, bucket)][lab]} for _, lab, _ in axes if lab in rows.get((side, bucket), {})]
    D["vrows"] = [
        {"title": "Главный и кандидаты", "lines": [{"axis": None, "keys": ["btc4h_trail", "btc4h_take", "cand", "nofilter", "g85", "g85b"]}], "cand": ["g85", "g85b"]},
        {"title": "П-02", "lines": [{"axis": None, "keys": [v["key"] for v in D["variants"] if v.get("group") == "p02" and v["key"] not in ("g85", "g85b")]}]},
        {"title": "П-05", "p05": True},
        {"title": "Г-85а · П-07", "lines": order("a", "grid", AXES)},
        {"title": "Г-85а · H9 пауза/потолок", "lines": order("a", "h9", H9)},
        {"title": "Г-85а · H9р/H14", "lines": order("a", "h9r", H9R_SHOWN)},
        {"title": "Г-85б · П-07", "lines": order("b", "grid", AXES)},
        {"title": "Г-85б · надстройки H9/H10/H9р/H14", "lines": order("b", "b-extra", H9 + H9R_SHOWN)},
    ]
    D["generated_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = json.dumps(D, ensure_ascii=False, separators=(",", ":"))
    if len(s.encode("utf-8")) > a.trades_max_mb * 1e6:  # сделки — только главным и кандидатам (kpi по сделкам — остаётся)
        print("с выгрузкой сделок всех клеток:", f"{len(s.encode('utf-8')) / 1e6:.1f} МБ > {a.trades_max_mb} — сделки клеток П-07 сняты")
        for p in D["trades"]:
            for k in [k for k in D["trades"][p] if str(k).startswith("p07")]:
                del D["trades"][p][k]
        for v in metas:
            if not v.get("nocalc"):
                v["trades_note"] = "список сделок на странице только у главных и кандидатов (размер страницы ≤ 14 МБ)"
        s = json.dumps(D, ensure_ascii=False, separators=(",", ":"))
    open(a.out, "w", encoding="utf-8").write(s)
    nocalc = [v["key"] for v in metas if v.get("nocalc")]
    print(a.out, f"{len(s.encode('utf-8')) / 1e6:.1f} МБ", "клеток П-07:", len(metas), "нет расчёта:", nocalc)
    print("сверка stage12 (n, $):", len(checked), checked)
    print("строки:", [(r["title"], sum(len(l["keys"]) for l in r.get("lines", []))) for r in D["vrows"]])


if __name__ == "__main__":
    main()
