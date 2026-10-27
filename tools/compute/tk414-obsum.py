#!/usr/bin/env python3
"""TK-414 стакан ob200: сводка по протоколу docs/research/P-TK414-ob200-2026-10-27.md (копия tk414-oifund-sum.py, правила O1–O5).
Использование: tk414-obsum.py <obfeat-events.csv> <pref-legs.csv> <legs-слоя2.csv> > сводка.md
Классы событий (по эталонной ноге): T — подтверждение старта ноги (в ноге того же знака, оценка старта = t0 ноги);
R — возобновление внутри ноги (в ноге того же знака, оценка старта позже t0); P — пауза: событие внутри ноги обратного знака
(ложный разворот: нога потом дошла до нового экстремума); O — вне ног. Плохие ноги исключены.
Популяция сравнения — события с признаком surge (история >= 360 мин): правила «свечи» и «свечи+сделки» меряются на одних и тех же событиях."""
import csv
import sys
from collections import defaultdict
from datetime import datetime

ev_f, leg_f, l2_f = sys.argv[1:4]
TITLE = sys.argv[4] if len(sys.argv) > 4 else "Bybit linear 48 инстр., сентябрь 2026"
MARKETS = sys.argv[5].split(",") if len(sys.argv) > 5 else ["linear"]


def tm(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M").timestamp() / 60


forms = {}
for r in csv.DictReader(open(l2_f, newline="", encoding="utf-8")):
    forms[(r["биржа"], r["рынок"], r["инструмент"], r["знак"], r["начало_utc"])] = r["форма"]
legs = {}
for r in csv.DictReader(open(leg_f, newline="", encoding="utf-8")):
    k = (r["биржа"], r["рынок"], r["инструмент"], r["знак"], r["t0"])
    legs[k] = dict(t0=tm(r["t0"]), t1=tm(r["t1"]), p0=float(r["цена0"]), p1=float(r["цена1"]), done=r["завершена"] == "1", bad=r["плохая"] == "1",
                   form=forms.get((r["биржа"], r["рынок"], r["инструмент"], "памп" if r["знак"] == "1" else "дамп", r["t0"]), ""))
def _f(r, k):
    return float(r[k]) if r[k] != "" else None


def _oi(r, d):
    x = _f(r, "oi_surge")
    return x is not None and x >= 3 and (d == 0 or int(r["oi_dir"]) == d)


def _ob(r, k, op, x):
    v = _f(r, k)
    return v is not None and (v <= x if op == "<=" else v >= x)


R5 = lambda r: _oi(r, 1) and accept((3, .1, "B", 1, 1), r)
RULES = {
    "свечи (без фильтра)": None,
    "ГЛАВНОЕ B потока: surge≥3 и imb≥0,1": (3, .1, "B", 1, 1),
    "R5: OI+поток (контроль)": R5,
    "O1: глубина стороны хода ≤ 1/3 базы": lambda r: _ob(r, "ob_dz", "<=", 1 / 3),
    "O2: дисбаланс книги в сторону ноги ≥ 0,1": lambda r: _ob(r, "ob_imb", ">=", .1),
    "O3: спред ≥ 3× базы": lambda r: _ob(r, "ob_sp", ">=", 3),
    "O4: главное B потока и O1": lambda r: accept((3, .1, "B", 1, 1), r) and _ob(r, "ob_dz", "<=", 1 / 3),
    "O5: R5 и O1": lambda r: R5(r) and _ob(r, "ob_dz", "<=", 1 / 3),
}
MAIN = RULES["ГЛАВНОЕ B потока: surge≥3 и imb≥0,1"]


def accept(rule, r):
    if rule is None:
        return True
    if callable(rule):
        return rule(r)
    sm, im, w, us, ui = rule
    sur, imb = r["surge" + w], r["imb" + w]
    if (us and sur == "") or (ui and imb == ""):
        return False
    return (not us or float(sur) >= sm) and (not ui or float(imb) >= im)


def q(x):
    x = sorted(x)
    return tuple(x[int(p * (len(x) - 1))] for p in (.25, .5, .75)) if x else None


def f3(t, d=1):
    return "—" if t is None else "/".join(("%." + str(d) + "f") % v for v in t)


def med(x):
    return sorted(x)[len(x) // 2] if x else float("nan")


E = defaultdict(list)
nofeat = tot = 0
for r in csv.DictReader(open(ev_f, newline="", encoding="utf-8")):
    if r["рынок"] not in MARKETS:
        continue
    r.setdefault("oi_surge", ""); r.setdefault("oi_dir", "")  # Gate: OI не считался, R5 отвергает всё
    s = int(r["знак"])
    if r["нога_плохая"] == "1" or r["обратная_плохая"] == "1":
        continue
    k = (r["биржа"], r["рынок"], r["инструмент"])
    i = tm(r["t_событие"])
    ext = tm(r["t_оценка_старта"])
    r["i"], r["ext"], r["s"] = i, ext, s
    lk = k + (str(s), r["нога_того_же_знака"]) if r["нога_того_же_знака"] else None
    ok = k + (str(-s), r["нога_обратная"]) if r["нога_обратная"] else None
    if lk:
        r["cls"] = "T" if abs(ext - legs[lk]["t0"]) < 1 else "R"
        r["leg"] = lk
    elif ok:
        r["cls"] = "P"
        r["leg"] = ok
    else:
        r["cls"] = "O"
        r["leg"] = None
    tot += 1
    if r["surge"] == "" or r["ob_dz"] == "":
        nofeat += 1
        continue
    E[r["вариант"]].append((k, r))

print("# TK-414 стакан против свечей и потока сделок (%s)\n" % TITLE)
print("Событий без surge (история < 360 мин) или без признака стакана (нет срезов) исключены из всех строк: %d из %d. Равный вес инструментов (медиана по ногам инструмента → "
      "кварт. 25/50/75 по инструментам, ≥5 ног). Число испытаний: 5 правил (O1–O5) × 2 разворота = 10 (+ 3 контрольные строки).\n" % (nofeat, tot))
for var in ("5", "5half"):
    ev = E[var]
    print("## θs=%s\n" % var)
    byleg = defaultdict(list)
    pausebyleg = defaultdict(list)
    for k, r in ev:
        if r["cls"] in "TR":
            byleg[r["leg"]].append(r)
        elif r["cls"] == "P":
            pausebyleg[r["leg"]].append(r)
    for sg, nm in ((1, "памп"), (-1, "дамп")):
        print("### %s\n" % nm)
        print("| правило | ног с принятым D, % | D лаг мин (25/50/75) | доля хода (25/50/75) | ног с принятой ложной паузой после D, % | принято T / R / P / O, % |")
        print("|---|---|---|---|---|---|")
        L = [(k, v) for k, v in legs.items() if k[3] == str(sg) and v["done"] and not v["bad"] and v["p1"] != v["p0"] and byleg.get(k)]
        for name, rule in RULES.items():
            lag = defaultdict(list)
            sh = defaultdict(list)
            have = miss = pz = 0
            cnt = {c: [0, 0] for c in "TRPO"}
            for k, r in ev:
                if r["s"] == sg:
                    cnt[r["cls"]][1] += 1
                    cnt[r["cls"]][0] += accept(rule, r)
            for k, v in L:
                rs = [r for r in byleg[k] if accept(rule, r)]
                if rs:
                    r = min(rs, key=lambda x: x["i"])
                    have += 1
                    lag[k[:3]].append(r["i"] - v["t0"])
                    sh[k[:3]].append((float(r["цена_события"]) - v["p0"]) / (v["p1"] - v["p0"]))
                    pz += any(p["i"] > r["i"] and accept(rule, p) for p in pausebyleg.get(k, []))
                else:
                    miss += 1
            m = lambda d: q([med(x) for x in d.values() if len(x) >= 5])
            n = have + miss
            c = lambda x: "%.0f" % (100 * cnt[x][0] / cnt[x][1]) if cnt[x][1] else "—"
            print("| %s | %.0f | %s | %s | %.0f | %s/%s/%s/%s |" % (name, 100 * have / n if n else 0, f3(m(lag), 0), f3(m(sh), 2),
                                                                  100 * pz / have if have else 0, c("T"), c("R"), c("P"), c("O")))
        print("\nНог в сравнении: %d. Классы в популяции: T %d, R %d, P %d, O %d событий (%s)\n" % (
            len(L), *[sum(1 for k, r in ev if r["cls"] == x and r["s"] == sg) for x in "TRPO"], nm))
    if var == "5":
        print("### по формам (θs=5, главное правило против свечей; только ликвидные ноги слоя 2; пампы+дампы)\n")
        print("| форма | ног | D лаг мин ме (свечи → главное) | доля хода ме (свечи → главное) | пропущено главным, % | ног с ложной паузой после D, % (свечи → главное) |")
        print("|---|---|---|---|---|---|")
        fo = defaultdict(list)
        for k, v in legs.items():
            if v["form"] and v["done"] and not v["bad"] and byleg.get(k) and v["p1"] != v["p0"]:
                fo[v["form"]].append(k)
        for fm, ks in sorted(fo.items()):
            out = []
            for rule in (None, MAIN):
                lg, sr, mi, pz = [], [], 0, 0
                for k in ks:
                    v = legs[k]
                    rs = [r for r in byleg[k] if accept(rule, r)]
                    if rs:
                        r = min(rs, key=lambda x: x["i"])
                        lg.append(r["i"] - v["t0"])
                        sr.append((float(r["цена_события"]) - v["p0"]) / (v["p1"] - v["p0"]))
                        pz += any(p["i"] > r["i"] and accept(rule, p) for p in pausebyleg.get(k, []))
                    else:
                        mi += 1
                out.append((med(lg), med(sr), mi, pz, len(lg)))
            a, b = out
            print("| %s | %d | %.0f → %.0f | %.2f → %.2f | %.0f | %.0f → %.0f |" % (
                fm, len(ks), a[0], b[0], a[1], b[1], 100 * b[2] / len(ks), 100 * a[3] / max(a[4], 1), 100 * b[3] / max(b[4], 1)))
        print()
    print("### ложные срабатывания «нога началась» (события вне ног того же знака, классы P+O), θs=%s\n" % var)
    ninst = len({k for k, r in ev})
    for name, rule in RULES.items():
        b = [r for k, r in ev if r["cls"] in "PO"]
        n = sum(accept(rule, r) for r in b)
        print("- %s: ложных %d из %d базы (%.0f %%); на инстр.-сутки (30 сут × %d инстр.): %.2f" % (name, n, len(b), 100 * n / len(b) if b else 0, ninst, n / (30 * ninst)))
    print()
