#!/usr/bin/env python3
"""Пересчёт таблиц отчёта xdl9-conv-window-2026-11-07.md из файлов сделок (только stdlib, ~1 с, ПК не нагружает).

Запуск (из этой папки):  python analysis_trades.py > analysis_trades_output.txt
Читает: grid_V1/trades.csv, grid_V2/trades.csv (окно 15.10-04.11, основной набор без Gate), gate/grid_V*/trades.csv (блок «с Gate»),
formation_ref/formation_V*_trades.csv (формирование 01.10-14.10, те же комбинации класса, прогон `conv_run grid --trades-out --mode calib --last-day 2478`).
Это ОПИСАТЕЛЬНЫЕ срезы после чтения сводки: вне M_окно, без статусов, не входят в «испытаний на окне всего» (433) раннера.
"""
import csv, collections, datetime, statistics as st, sys

sys.stdout.reconfigure(encoding="utf-8")
CODE = {"0": "CV", "2": "S1", "3": "S2", "4": "S3", "6": "TO"}  # CV схождение, S1 потолок G_max, S2 протухшая нога, S3 аномалия ячейки, TO дедлайн T
NUM = ("gross", "fee", "fund", "net", "hold", "g_in", "sig_in", "dir", "t_sig", "f_in", "f_out")
BASE = datetime.date(2020, 1, 1)


def load(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    for r in rows:
        for k in NUM:
            r[k] = float(r[k])
    return rows


def day(r, d0=2465):
    return d0 + int(r["t_sig"]) // 1440


def dstr(d):
    return (BASE + datetime.timedelta(days=d)).strftime("%d.%m")


def legs(r):
    """(длинная нога, короткая нога): dir=+1 - нога A (первая биржа ячейки) дорога - шорт A, лонг B"""
    a, b = r["pid"].split("|")[0].split("~")
    return (b, a) if r["dir"] > 0 else (a, b)


def m(rs, k):
    return sum(r[k] for r in rs) / len(rs) if rs else float("nan")


def line(rs, label, tot=None):
    if not rs:
        return f"{label:28s} n=0"
    s = f"{label:28s} n={len(rs):4d}"
    if tot:
        s += f" доля={len(rs)/tot:6.1%}"
    return s + f" gross={m(rs,'gross'):8.2f} фандинг={m(rs,'fund'):8.2f} net={m(rs,'net'):8.2f} сумма_net={sum(r['net'] for r in rs):9.1f} удерж.ср={m(rs,'hold'):7.1f}"


def decomp(F, W, title):
    print(f"\n=== {title}: разложение чистой на сделку, формирование -> окно (bps на сделку; вклад = сумма по выходу / все сделки)")
    for nm, rs in (("формирование", F), ("окно", W)):
        print(f"[{nm}]", line(rs, "ВСЕ"))
        for c in sorted({r["code"] for r in rs}):
            sub = [r for r in rs if r["code"] == c]
            print("   ", line(sub, "выход " + CODE[c], len(rs)), f"вклад_net={sum(r['net'] for r in sub)/len(rs):7.2f} вклад_gross={sum(r['gross'] for r in sub)/len(rs):7.2f} вклад_фандинг={sum(r['fund'] for r in sub)/len(rs):7.2f}")
    print("итого на сделку: gross %.2f -> %.2f; комиссия %.2f -> %.2f; фандинг %.2f -> %.2f; net %.2f -> %.2f" % (
        m(F, "gross"), m(W, "gross"), -m(F, "fee"), -m(W, "fee"), m(F, "fund"), m(W, "fund"), m(F, "net"), m(W, "net")))
    codes = sorted({r["code"] for r in F} | {r["code"] for r in W})
    print("Δ вклада net по выходам:", {CODE[c]: round(sum(r['net'] for r in W if r['code'] == c) / len(W) - sum(r['net'] for r in F if r['code'] == c) / len(F), 2) for c in codes}, "Σ =", round(m(W, "net") - m(F, "net"), 2))
    cvF = [r for r in F if r["code"] == "0"]; cvW = [r for r in W if r["code"] == "0"]
    print(f"сделки с выходом CV: gross {m(cvF,'gross'):.2f} -> {m(cvW,'gross'):.2f}; фандинг {m(cvF,'fund'):.2f} -> {m(cvW,'fund'):.2f}; net {m(cvF,'net'):.2f} -> {m(cvW,'net'):.2f}; доля {len(cvF)/len(F):.3f} -> {len(cvW)/len(W):.3f}")
    nonS1 = [r for r in W if r["code"] != "2"]
    print(f"окно: сделки без выхода S1: n={len(nonS1)} net={m(nonS1,'net'):.2f} (разбиение реализованного, не контрфактическое)")


def bybit(F, W, title):
    print(f"\n=== {title}: ноги Bybit (длинная/короткая нога по dir; Bybit-пары = в сделке есть нога Bybit)")
    out = {}
    for nm, rs in (("формирование", F), ("окно", W)):
        b = [r for r in rs if "bybit" in legs(r)]; nb = [r for r in rs if "bybit" not in legs(r)]
        sb = [r for r in rs if legs(r)[1] == "bybit"]; lb = [r for r in rs if legs(r)[0] == "bybit"]
        print(f"[{nm}]", line(b, "с ногой Bybit", len(rs))); print("    ", line(nb, "без ноги Bybit", len(rs)))
        print("    ", line(sb, "шорт Bybit (лонг другая)", len(rs))); print("    ", line(lb, "лонг Bybit (шорт другая)", len(rs)))
        out[nm] = (len(b) / len(rs) * m(b, "net"), len(nb) / len(rs) * m(nb, "net"))
    print("вклад в net на сделку: Bybit-пары %.2f -> %.2f (Δ %.2f); остальные %.2f -> %.2f (Δ %.2f)" % (
        out["формирование"][0], out["окно"][0], out["окно"][0] - out["формирование"][0], out["формирование"][1], out["окно"][1], out["окно"][1] - out["формирование"][1]))
    S1 = [r for r in W if r["code"] == "2"]
    print(f"S1 на окне: {len(S1)}; с ногой Bybit {sum(1 for r in S1 if 'bybit' in legs(r))}; шорт Bybit {sum(1 for r in S1 if legs(r)[1]=='bybit')}; различных актив-суток {len({(r['key'], day(r)) for r in S1})}; активов {len({r['key'] for r in S1})}")


def s1_list(W):
    print("\n=== S1 (потолок G_max = 320 bps) на окне, по времени входа (UTC):")
    for r in sorted((r for r in W if r["code"] == "2"), key=lambda r: r["t_sig"]):
        mm = int(r["t_sig"]) % 1440
        print(f"{dstr(day(r))} {mm//60:02d}:{mm%60:02d} {r['pid'].split('|')[0]:14s} {r['key']:8s} лонг {legs(r)[0]:8s} шорт {legs(r)[1]:8s} g_in={r['g_in']:5.0f} удерж.={r['hold']:4.0f} gross={r['gross']:6.0f} фандинг={r['fund']:5.0f} net={r['net']:6.0f}")


def depth(W, title):
    print(f"\n=== {title}: глубина отклонения при входе (g_in, bps) и в σ (sig_in): доля стопов и net")
    for lo, hi in ((0, 50), (50, 80), (80, 120), (120, 200), (200, 1e9)):
        sub = [r for r in W if lo <= r["g_in"] < hi]
        print(f"  g_in [{lo},{hi if hi<1e8 else 'inf'}): n={len(sub):3d} S1={sum(1 for r in sub if r['code']=='2'):2d} ({sum(1 for r in sub if r['code']=='2')/max(1,len(sub)):5.1%}) S3={sum(1 for r in sub if r['code']=='4'):2d} TO={sum(1 for r in sub if r['code']=='6'):2d} gross={m(sub,'gross'):6.1f} net={m(sub,'net'):7.1f} сумма={sum(r['net'] for r in sub):7.0f}")
    big = [r for r in W if r["g_in"] >= 120]
    print(f"  g_in >= 120: n={len(big)} S1={sum(1 for r in big if r['code']=='2')} net={m(big,'net'):.2f} сумма={sum(r['net'] for r in big):.0f}; max g_in = {max(r['g_in'] for r in W):.1f}; >= 320 bps: {sum(1 for r in W if r['g_in']>=320)} сделок")
    for lo, hi in ((0, 5), (5, 8), (8, 13), (13, 21), (21, 1e9)):
        sub = [r for r in W if lo <= r["sig_in"] < hi]
        if sub:
            print(f"  sig_in [{lo},{hi if hi<1e8 else 'inf'}): n={len(sub):3d} S1={sum(1 for r in sub if r['code']=='2'):2d} gross={m(sub,'gross'):6.1f} net={m(sub,'net'):7.1f}")
    print(f"  g_in среднее/медиана: {m(W,'g_in'):.1f}/{st.median(r['g_in'] for r in W):.1f}; sig_in: {m(W,'sig_in'):.1f}/{st.median(r['sig_in'] for r in W):.1f}")


def reentry(W, title):
    print(f"\n=== {title}: перезаход на той же паре после стопа (стопы S1/S2/S3; S0 отключён)")
    bypid = collections.defaultdict(list)
    for r in W:
        bypid[r["pid"]].append(r)
    cnt = collections.Counter(); after = collections.defaultdict(list); gaps = []
    for pid, rs in bypid.items():
        rs.sort(key=lambda r: r["f_in"]); k = 0
        for i, r in enumerate(rs):
            if k > 0:
                after[min(k, 3)].append(r)
            if r["code"] in ("2", "3", "4"):
                k += 1
                if i + 1 < len(rs):
                    gaps.append(rs[i + 1]["f_in"] - r["f_out"])
        cnt[k] += 1
    print("  пар по числу стопов (из пар со сделками):", dict(sorted(cnt.items())), "пар со сделками:", len(bypid))
    for k in sorted(after):
        rs = after[k]
        print(f"  сделки на паре после её стопа №{k}: n={len(rs)} net={m(rs,'net'):.1f} сумма={sum(r['net'] for r in rs):.0f} выходы={dict(collections.Counter(CODE[r['code']] for r in rs))}")
    if gaps:
        print(f"  пауза от выхода по стопу до следующего входа: n={len(gaps)} мин={min(gaps):.0f} медиана={st.median(gaps):.0f} макс={max(gaps):.0f} минут")


def concentration(W, title):
    print(f"\n=== {title}: концентрация (ячейки, активы, сутки, недели)")
    bycell = collections.defaultdict(list); byas = collections.defaultdict(list); byd = collections.defaultdict(list)
    for r in W:
        bycell[r["pid"].split("|")[0]].append(r); byas[r["key"]].append(r); byd[day(r)].append(r)
    c = sorted(((k, len(v), sum(r["net"] for r in v)) for k, v in bycell.items()), key=lambda x: x[2])
    print("  худшие ячейки:", [(k, n, round(s)) for k, n, s in c[:5]]); print("  лучшие ячейки:", [(k, n, round(s)) for k, n, s in c[-5:]])
    a = sorted(((k, len(v), sum(r["net"] for r in v)) for k, v in byas.items()), key=lambda x: x[2])
    print("  худшие активы:", [(k, n, round(s)) for k, n, s in a[:8]], "сумма худших 10:", round(sum(x[2] for x in a[:10])))
    print("  лучшие активы:", [(k, n, round(s)) for k, n, s in a[-8:]], "сумма лучших 10:", round(sum(x[2] for x in a[-10:])))
    print(f"  активов {len(a)}: с минусом {sum(1 for x in a if x[2]<0)}, с плюсом {sum(1 for x in a if x[2]>0)}")
    rest = [r for r in W if r["key"] not in {x[0] for x in a[:10]}]
    print(f"  без 10 худших активов (отбор по исходу, только описание): n={len(rest)} net={m(rest,'net'):.2f}")
    print("  сутки (дата: сделок / стопов S1 / сумма net):", "; ".join(f"{dstr(d)}: {len(v)}/{sum(1 for r in v if r['code']=='2')}/{sum(r['net'] for r in v):.0f}" for d, v in sorted(byd.items())))
    for lab, sub in (("с ногой Bybit", [r for r in W if "bybit" in legs(r)]), ("без ноги Bybit", [r for r in W if "bybit" not in legs(r)])):
        parts = []
        for lo, hi in ((2479, 2486), (2486, 2493), (2493, 2500)):
            s = [r for r in sub if lo <= day(r) < hi]
            parts.append(f"{dstr(lo)}-{dstr(hi-1)}: n={len(s)} net={m(s,'net'):.1f} gross={m(s,'gross'):.1f} фандинг={m(s,'fund'):.1f}")
        print(f"  {lab}, по неделям:", " | ".join(parts))
    nb = collections.defaultdict(float)
    for r in W:
        if "bybit" not in legs(r):
            nb[day(r)] += r["net"]
    print(f"  без ноги Bybit: суток с плюсом {sum(1 for d in range(2479,2500) if nb.get(d,0)>0)}, с минусом {sum(1 for d in range(2479,2500) if nb.get(d,0)<0)}")


def funding(F, W):
    print("\n=== фандинг: доля сделок с ненулевым фандингом, знак, концентрация по активам")
    for nm, rs in (("формирование", F), ("окно", W)):
        nz = [r for r in rs if r["fund"] != 0]
        print(f"[{nm}] n={len(rs)} ненулевой={len(nz)} ({len(nz)/len(rs):.1%}) минус={sum(1 for r in rs if r['fund']<0)} плюс={sum(1 for r in rs if r['fund']>0)} сумма минуса={sum(r['fund'] for r in rs if r['fund']<0):.0f} сумма плюса={sum(r['fund'] for r in rs if r['fund']>0):.0f} среднее удержание={m(rs,'hold'):.1f} мин")
        by = collections.defaultdict(float)
        for r in rs:
            by[r["key"]] += r["fund"]
        tot = sum(by.values()); top = sorted(by.items(), key=lambda x: x[1])[:5]
        print("   сумма", round(tot), "топ-5 активов:", [(k, round(v)) for k, v in top], "доля топ-4:", round(sum(v for k, v in top[:4]) / tot, 3))
        t4 = {k for k, v in top[:4]}; rest = [r for r in rs if r["key"] not in t4]
        print(f"   без топ-4 активов по фандингу: n={len(rest)} gross={m(rest,'gross'):.1f} фандинг={m(rest,'fund'):.1f} net={m(rest,'net'):.1f}")
        srt = sorted(r["fund"] for r in rs)
        print("   доля суммарного фандинга в худших 10/20/40 сделках:", [round(sum(srt[:k]) / tot, 2) for k in (10, 20, 40)])


def tails(F, W):
    print("\n=== хвост сделок")
    for nm, rs in (("формирование", F), ("окно", W)):
        w = [r["net"] for r in rs if r["net"] > 0]; l = [-r["net"] for r in rs if r["net"] < 0]
        q = sorted(r["net"] for r in rs)
        print(f"[{nm}] доля побед {len(w)/len(rs):.3f}; средний выигрыш {st.mean(w):.1f}; средний проигрыш {st.mean(l):.1f}; отношение {st.mean(w)/st.mean(l):.2f}; худшая {q[0]:.0f}; квантили 1/5/50 %: {q[int(len(q)*0.01)]:.0f}/{q[int(len(q)*0.05)]:.0f}/{q[len(q)//2]:.1f}; доля <=2 мин {sum(1 for r in rs if r['hold']<=2)/len(rs):.3f}; удерж. среднее/медиана {m(rs,'hold'):.1f}/{st.median(r['hold'] for r in rs):.0f}")
    for lo, hi in ((0, 3), (3, 10), (10, 60), (60, 240), (240, 1e9)):
        for nm, rs in (("ф", F), ("о", W)):
            sub = [r for r in rs if lo <= r["hold"] < hi]
            print(f"   удержание [{lo},{hi if hi<1e8 else 'inf'}) мин [{nm}]: " + (line(sub, "", len(rs)).strip() if sub else "n=0"))


def direction(F, W):
    print("\n=== направление dir (+1: нога A дорога, шорт A / лонг B; A = первая по алфавиту биржа ячейки)")
    for nm, rs in (("формирование", F), ("окно", W)):
        for d in (1.0, -1.0):
            print(f"[{nm}] dir={d:+.0f}", line([r for r in rs if r["dir"] == d], "", len(rs)).strip())


def main():
    sets = {}
    for v in ("V1", "V2"):
        sets[v] = (load(f"formation_ref/formation_{v}_trades.csv"), load(f"grid_{v}/trades.csv"))
    for v, (F, W) in sets.items():
        print("\n" + "#" * 30, v, "формирование n=%d, окно n=%d" % (len(F), len(W)))
        decomp(F, W, v); bybit(F, W, v); direction(F, W); depth(W, v); reentry(W, v); concentration(W, v); funding(F, W); tails(F, W)
        if v == "V1":
            s1_list(W)
        else:
            TO = [r for r in W if r["code"] == "6"]
            print(f"\nV2 окно, выход TO (дедлайн T=120 мин): n={len(TO)} с ногой Bybit {sum(1 for r in TO if 'bybit' in legs(r))}, шорт Bybit {sum(1 for r in TO if legs(r)[1]=='bybit')}, активов {len({r['key'] for r in TO})}; net={m(TO,'net'):.1f} gross={m(TO,'gross'):.1f} фандинг={m(TO,'fund'):.1f}")
            TOF = [r for r in F if r["code"] == "6"]
            print(f"V2 формирование, выход TO: n={len(TOF)} net={m(TOF,'net'):.1f} gross={m(TOF,'gross'):.1f} фандинг={m(TOF,'fund'):.1f}")
    print("\n" + "#" * 30, "блок «с Gate» (описательно)")
    for v in ("V1", "V2"):
        G = load(f"gate/grid_{v}/trades.csv")
        S1 = [r for r in G if r["code"] == "2"]
        gl = [r for r in G if "gate" in legs(r)]; ng = [r for r in G if "gate" not in legs(r)]
        print(f"{v}: n={len(G)} S1={len(S1)} (с ногой Bybit {sum(1 for r in S1 if 'bybit' in legs(r))}); пары с ногой Gate n={len(gl)} net={m(gl,'net'):.2f} gross={m(gl,'gross'):.2f}, S1 в них {sum(1 for r in gl if r['code']=='2')}; остальные n={len(ng)} net={m(ng,'net'):.2f}")


if __name__ == "__main__":
    main()
