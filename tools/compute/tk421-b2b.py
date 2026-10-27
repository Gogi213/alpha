#!/usr/bin/env python3
"""TK-421 шаг B2b (протокол P-TK421 v6): L4 «не рынок», кластеры, пары, id_рынка, диагностика, лист ВСЕХ кандидатов.
Использование: tk421-b2b.py <корень свечей> <b1-thresh.json> <b2-каталог> <выход-каталог> [месяц] [art.json]
L4 (v6 п.1 + решение Судьи 3): доля монет рынка в csv как есть, фильтр по ней НЕ применяется (излом доли не ищется);
исключает только ход BTC той же биржи: max a_T(BTC, T_min) на минутах [t_start, t_peak] >= a*_{T_min} рынка события, тот
же знак. Доля монет — по часам qh (разрешение 1 ч): инструменты рынка с проходящей минутой того же знака в
[час пика − ceil(T_min/60), час пика + ceil(T_min/60)] / число инструментов рынка. Исключённые остаются в csv (L4=рынок).
Лист: без κ, m, оборота, дормантности, OI, фандинга; цена 1 м = текстовый график (8 уровней), окно [t_start − 1 сут,
t_end + длит.], ≤ 96 точек; метка «памп/не памп» не ставится."""
import bisect
import csv
import gzip
import importlib.util
import json
import math
import os
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location("ev", os.path.join(HERE, "tk421-events.py"))
ev = importlib.util.module_from_spec(sp)
sp.loader.exec_module(ev)
mv, MIN, DAY, TS = ev.mv, ev.MIN, ev.DAY, ev.TS
BARS = "▁▂▃▄▅▆▇█"
BTC = {"bybit-linear": "BTCUSDT", "bybit-spot": "BTCUSDT", "gate-futures_usdt": "BTC_USDT", "gate-spot": "BTC_USDT"}


def coin(sym):
    s = sym.split("-")[0]
    for suf in ("_USDT", "USDT", "_USD", "USD"):
        if s.endswith(suf):
            return s[: -len(suf)]
    return s


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%m-%d %H:%M")


def spark(vals, n=96):
    """(строка, индексы исходных точек): точка графика = последний close корзины; шкала min–max по окну графика."""
    ix = list(range(len(vals)))
    if len(vals) > n:
        step = len(vals) / n
        ix = [min(len(vals) - 1, int((k + 1) * step) - 1) for k in range(n)]
    vs = [vals[i] for i in ix]
    lo, hi = min(vs), max(vs)
    if hi <= lo:
        return BARS[0] * len(vs), ix
    return "".join(BARS[min(7, int((v - lo) / (hi - lo) * 8))] for v in vs), ix


def chart(arg):
    d, sym, wins = arg  # wins: (id, окно_от, окно_до, t_start, t_peak)
    t, c = [], []
    with gzip.open(os.path.join(d, sym + ".csv.gz"), "rt") as f:
        f.readline()
        for line in f:
            p = line.split(",", 6)
            t.append(int(p[0])); c.append(float(p[4]))
    out = []
    for key, a, b, ts, tp in wins:
        i, j = bisect.bisect_left(t, a), bisect.bisect_right(t, b)  # окно обрезано началом данных
        if j - i < 2:
            out.append((key, ""))
            continue
        s, ix = spark(c[i:j])
        mk = [" "] * len(s)
        for ch_, tt in (("S", ts), ("P", tp)):
            k = min(range(len(ix)), key=lambda q: abs(t[i + ix[q]] - tt))
            mk[k] = ch_ if mk[k] == " " else "*"
        note = "" if t[0] <= a else " ⟨данные с %s⟩" % utc(t[0])
        out.append((key, "`%s`<br>`%s`%s" % (s, "".join(mk), note)))
    return out


def btc_a(root, sub, t0, t1, sign, tmin):
    t, h, l, c, q = ev.load_q(os.path.join(root, sub, BTC[sub] + ".csv.gz"), t0, t1)
    up = sign == "+"
    hh, ll = (h, l) if up else ([1 / x for x in l], [1 / x for x in h])
    return t, mv.moves(t, hh, ll, tmin, True)


def main():
    root, thf, b2d, outd = sys.argv[1:5]
    ym = sys.argv[5] if len(sys.argv) > 5 else "2026-09"
    TH = {k: (v["x_star"] if isinstance(v, dict) else None) for k, v in json.load(open(thf))["результат"].items()}
    if len(sys.argv) > 6:
        mv.load_excl(sys.argv[6])
    t0, t1 = mv.month_bounds(ym)
    os.makedirs(outd, exist_ok=True)
    rows, nins, medq, per_coin = [], {}, defaultdict(dict), defaultdict(lambda: defaultdict(int))
    qh = {}
    for ex, mk, sub in mv.MKT:
        n = 0
        for line in open(os.path.join(b2d, "events-%s-%s-%s.jsonl" % (ym, ex, mk))):
            r = json.loads(line)
            if r.get("пропуск"):
                continue
            n += 1
            medq[sub][r["инструмент"]] = (r["med_q"], bool(r["events"]))
            for sg in "+-":
                for h in r["qh"].get(sg, []):
                    qh.setdefault((sub, sg, h), set()).add(r["инструмент"])
            for e in r["events"]:
                per_coin[sub][r["инструмент"]] += 1
                rows.append(dict(e, ex=ex, mk=mk, sub=sub, sym=r["инструмент"], coin=coin(r["инструмент"])))
        nins[sub] = n
    # окно артефакта (Судья 28.10): событие, у которого [t_pre, t_end] пересекает окно рынка, снимается целиком
    WIN = {k: v.get("окно_артефакта", []) for k, v in json.load(open(sys.argv[6]))["результат"].items()} if len(sys.argv) > 6 else {}
    snyato = defaultdict(list)
    keep = []
    for r in rows:
        tpre = r["t_start"] - r["длит_мин"] * MIN
        hit = [w for w in WIN.get(r["sub"], []) if tpre <= w[1] and r["t_end"] >= w[0]]
        (snyato[r["sub"]].append(r) if hit else keep.append(r))
    rows = keep
    json.dump({k: [{"sym": r["sym"], "знак": r["знак"], "t_start": r["t_start"], "t_peak": r["t_peak"], "t_end": r["t_end"], "ход_%": r["ход_%"]} for r in v]
               for k, v in snyato.items()}, open(os.path.join(outd, "art-window-removed-%s.json" % ym), "w"), ensure_ascii=False, indent=1)
    # L4
    btc = {}
    for r in rows:
        sub, sg, tm = r["sub"], r["знак"], r["T_min"]
        hp = (r["t_peak"] - t0) // 3600000
        k = math.ceil(tm / 60)
        s = set()
        for h in range(hp - k, hp + k + 1):
            s |= qh.get((sub, sg, h), set())
        r["доля"] = round(len(s) / nins[sub], 4)
        key = (sub, sg, tm)
        if key not in btc:
            btc[key] = btc_a(root, sub, t0, t1, sg, tm)
        bt, ba = btc[key]
        i, j = bisect.bisect_left(bt, r["t_start"]), bisect.bisect_right(bt, r["t_peak"])
        r["btc_a"] = round(max(ba[i:j]), 4) if j > i else None
        th = TH.get("%s|%d%s|м" % (sub, tm, sg))
        r["L4"] = "рынок" if (r["btc_a"] is not None and th is not None and r["btc_a"] >= th) else "ok"
    # монета_id, кластеры (монета, любые рынки, перекрытие [t_start,t_end]); id_рынка (±T_min)
    def comps(pad):
        out, byc = {}, defaultdict(list)
        for i, r in enumerate(rows):
            byc[r["coin"]].append(i)
        for cn, idx in byc.items():
            idx.sort(key=lambda i: rows[i]["t_start"])
            cid, end, k = 0, None, 0
            for i in idx:
                r = rows[i]
                a = r["t_start"] - (r["T_min"] * MIN if pad else 0)
                b = r["t_end"] + (r["T_min"] * MIN if pad else 0)
                if end is None or a > end:
                    k += 1; end = b
                else:
                    end = max(end, b)
                out[i] = "%s#%d" % (cn, k)
        return out
    cl, mk_id = comps(False), comps(True)
    first = {}
    for i, r in enumerate(rows):
        r["id"] = "E%05d" % (i + 1); r["кластер"] = cl[i]; r["id_рынка"] = mk_id[i]
        first[mk_id[i]] = min(first.get(mk_id[i], 1 << 62), r["t_start"])
    byc = defaultdict(list)
    for r in rows:
        byc[r["coin"]].append(r)
    for r in rows:
        r["первый"] = int(first[r["id_рынка"]] == r["t_start"])
        r["лаг_мин"] = (r["t_start"] - first[r["id_рынка"]]) // MIN
        r["пара"], r["тип_пары"], r["парный"] = "", r["знак"] == "+" and "памп" or "дамп", ""
        r["t_pre"] = r["t_start"] - r["длит_мин"] * MIN
        r["вид"] = "кредитный_токен" if re.search(r"\d[SL]$", r["coin"]) else "обычный"
    pid = 0
    for cn, lst in byc.items():
        for a in lst:
            if a["парный"] or a["знак"] != "+":
                continue
            for b in lst:
                if b["знак"] == "-" and not b["парный"] and a["t_peak"] <= b["t_start"] <= a["t_end"] + a["длит_мин"] * MIN:
                    pid += 1
                    a["пара"] = b["пара"] = "P%04d" % pid
                    a["тип_пары"], b["тип_пары"] = "памп→дамп", "памп→дамп"
                    a["парный"], b["парный"] = b["id"], a["id"]
                    break
        for b in lst:
            if b["знак"] == "-" and not b["парный"]:
                for a in lst:
                    if a["знак"] == "+" and not a["парный"] and b["t_peak"] <= a["t_start"] <= b["t_end"] + b["длит_мин"] * MIN:
                        pid += 1
                        a["пара"] = b["пара"] = "P%04d" % pid
                        a["тип_пары"], b["тип_пары"] = "дамп→памп", "дамп→памп"
                        a["парный"], b["парный"] = b["id"], a["id"]
                        break
    # csv
    cols = ["биржа", "рынок", "монета", "монета_id", "id", "кластер_id", "id_рынка", "пара_id", "тип_пары", "вид", "знак",
            "парный_id", "t_pre_ms", "t_start_ms", "t_peak_ms", "t_end_ms", "ход_%", "длит_мин", "T_min", "конец"] + \
           ["a_T%d" % T for T in TS] + ["κ", "m", "оборот_до_отн", "σ_до_отн", "плоских_до", "pre_полное",
                                         "доля_монет_рынка", "ход_BTC_a", "L4", "первый", "лаг_мин", "a_порог", "инвариант", "месяц"]
    with open(os.path.join(outd, "pump-events-%s.csv" % ym), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([r["ex"], r["mk"], r["sym"], r["coin"], r["id"], r["кластер"], r["id_рынка"], r["пара"], r["тип_пары"],
                        r["вид"], r["знак"], r["парный"], r["t_pre"], r["t_start"], r["t_peak"], r["t_end"], r["ход_%"],
                        r["длит_мин"], r["T_min"], r["конец"]] + [r["aT"][str(T)] if str(T) in r["aT"] else r["aT"].get(T) for T in TS] +
                       [r["κ"], r["m"], r["оборот_до_отн"], r["σ_до_отн"], r["плоских_до"], r["pre_полное"], r["доля"],
                        r["btc_a"], r["L4"], r["первый"], r["лаг_мин"], r["a_порог"], r["инвариант"], ym])
    # диагностика (описание, не фильтр)
    D = {}
    cand = [r for r in rows if r["L4"] == "ok"]
    for ex, mk, sub in mv.MKT:
        al = [r for r in rows if r["sub"] == sub]
        cd = [r for r in cand if r["sub"] == sub]
        pc = sorted(per_coin[sub].values(), reverse=True)
        ev_q = [v[0] for v in medq[sub].values() if v[1] and v[0] > 0]
        no_q = [v[0] for v in medq[sub].values() if not v[1] and v[0] > 0]
        D[sub] = {
            "инструментов": nins[sub], "снято_окном_артефакта": len(snyato[sub]), "в_окне_артефакта": WIN.get(sub, []), "событий_L0": len(al), "после_L4": len(cd), "исключено_рынок": len(al) - len(cd),
            "кластеров_после_L4": len({r["кластер"] for r in cd}), "монет_с_событиями": len(pc),
            "событий_на_монету_медиана/p90/макс": [statistics.median(pc) if pc else 0, pc[int(0.1 * len(pc))] if pc else 0, pc[0] if pc else 0],
            "доля_событий_топ10_монет": round(sum(pc[:10]) / max(1, sum(pc)), 3),
            "медиана_оборота_мин_с_событиями": round(statistics.median(ev_q) / 1440, 1) if ev_q else None,
            "медиана_оборота_мин_без": round(statistics.median(no_q) / 1440, 1) if no_q else None,
            "доля_монет_рынка_медиана/p95/макс": [statistics.median([r["доля"] for r in al]) if al else 0,
                                                  sorted(r["доля"] for r in al)[int(0.95 * len(al))] if al else 0,
                                                  max((r["доля"] for r in al), default=0)],
            "нарушений_инварианта": sum(1 for r in al if not r["инвариант"]), "ход_вне_допустимого": sum(1 for r in al if r["ход_%"] <= -100),
            "пар_памп↔дамп": len({r["пара"] for r in al if r["пара"]}),
            "по_знаку+": sum(1 for r in cd if r["знак"] == "+"), "по_знаку-": sum(1 for r in cd if r["знак"] == "-")}
    json.dump(D, open(os.path.join(outd, "b2b-diag-%s.json" % ym), "w"), ensure_ascii=False, indent=1)
    # лист ВСЕХ кандидатов
    jobs = defaultdict(list)
    for r in cand:
        jobs[(r["sub"], r["sym"])].append((r["id"], r["t_start"] - DAY, r["t_end"] + r["длит_мин"] * MIN, r["t_start"], r["t_peak"]))
    args = [(os.path.join(root, s), y, w_) for (s, y), w_ in jobs.items()]
    ch = {}
    with Pool(2) as pool:
        for res in pool.imap_unordered(chart, args, chunksize=8):
            ch.update(dict(res))
    with open(os.path.join(outd, "pump-events-sample-%s.md" % ym), "w", encoding="utf-8") as f:
        f.write("# Лист просмотра ВСЕХ кандидатов TK-421 (v6, %s): L0 (V0) ∩ L4, без меток\n\n" % ym)
        f.write("Показаны только цена 1 м, ход %, T, доля монет рынка, ход BTC (v6 решение 1). Метка: `памп` или `не памп:<артефакт_данных|листинг|рынок|не_ход>`. "
                "Время UTC. График: окно [старт − 1 сут, конец + длительность], обрезано началом данных (пометка ⟨данные с …⟩), ≤ 96 точек; точка = последний close корзины, шкала min–max по окну графика; вторая строка: S = старт, P = пик, * = совпали. Вид: кредитный_токен (*3S/*5L…) — только пометка.\n\n")
        for ex, mk, sub in mv.MKT:
            for sg in "+-":
                sel = sorted((r for r in cand if r["sub"] == sub and r["знак"] == sg), key=lambda r: (r["кластер"], r["t_start"]))
                f.write("## %s %s, %s — %d событий, %d кластеров\n\n" % (ex, mk, "памп" if sg == "+" else "дамп", len(sel), len({r["кластер"] for r in sel})))
                f.write("| id | кластер | инструмент | вид | старт | пик | ход % | длит. мин | T | доля монет | BTC a | график | метка |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
                for r in sel:
                    f.write("| %s | %s | %s | %s | %s | %s | %s | %d | %d | %s | %s | %s | |\n" % (
                        r["id"], r["кластер"], r["sym"], r["вид"], utc(r["t_start"]), utc(r["t_peak"]), r["ход_%"], r["длит_мин"],
                        r["T_min"], r["доля"], r["btc_a"], ch.get(r["id"], "")))
                f.write("\n")
    print(json.dumps(D, ensure_ascii=False))


if __name__ == "__main__":
    main()
