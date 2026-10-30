"""TK-455: отчёт docs/findings/sancho-strategy-<дата>.md из выхода агрегатора (rows/years/funnel)."""
import sys, pandas as pd
src, out = sys.argv[1], sys.argv[2]  # каталог выхода агрегатора, префикс docs/findings/sancho-strategy-DATE
rd = lambda n: pd.read_csv(f"{src}/sancho-l3-{n}.csv", encoding="utf-8")
r, y, f = rd("rows"), rd("years"), rd("funnel")
TF = {60: "1H", 240: "4H", 1440: "1D"}
SD = {"H": "хай", "L": "лой"}
yrs = sorted(y.year.unique())
key = ["ev", "form", "tf", "dir"]
yy = y.set_index(key + ["year"])
def ycell(k, yr):
    try:
        t = yy.loc[k + (yr,)]
    except KeyError:
        return "—"
    return f"{t.mean_mix:.0f}" if t.enough else "н/д"
def f1(x): return f"{x:.1f}"
hdr = ("| правила | форма | сделок | выигр. | net, bps (все мейкер … ДИ смеси … все тейкер) | Δ к C1 / C2, bps (p Холма) | "
       + " | ".join(str(a) for a in yrs) + " | с 2020 / позже | BTC·ETH | вердикт |\n|" + "---|" * (9 + len(yrs)) + "\n")
def rows():
    s = ""
    for _, a in r.sort_values("form", kind="stable").iterrows():
        k = (a.ev, a.form, int(a.tf), a["dir"])
        v = a.verdict
        s += (f"| {a.ev}·{a.form}·{TF[int(a.tf)]}·{SD[a['dir']]} | {a.form} | {int(a.N)} | {a.win_mix:.0%} | "
              f"{f1(a.mean_mk)} … **{f1(a.mean_mix)}** [{f1(a.mix_lo)}; {f1(a.mix_hi)}] … {f1(a.mean_tk)} | "
              f"{f1(a.delta_C1)} / {f1(a.delta_C2)} (p={a.p_D_holm:.2f}) | "
              + " | ".join(ycell(k, yr) for yr in yrs)
              + f" | {f1(a.mix_hist2020_True)} / {f1(a.mix_hist2020_False)} | {f1(a['mix_BTC/ETH'])} | {v} |\n")
    return s
hdr1 = hdr.split(chr(10))[0]
hdr = hdr1 + chr(10) + "|" + "---|" * (hdr1.count("|") - 1) + chr(10)
cnt = r.verdict.value_counts().to_dict()
nD = r.D.value_counts().to_dict(); nP = r.P.value_counts().to_dict()
neg = r[r.verdict == "оспорено"]
fun = f.pivot_table(index=key, columns="level_4", values="0", aggfunc="sum").fillna(0).astype(int)
ax = pd.read_csv(f"{src}/sancho-l3-axes.csv", keep_default_na=False)
ov = pd.read_csv(f"{src}/sancho-l3-axes-overlap.csv")
ax["net"] = (ax.gross - ax.fee_mix - ax.fund) / ax.n.where(ax.n > 0)
re_ = ax[ax.role == "real"]
def cell(ev, d, tf, form, E, W, S, m, T, rr, N):
    q = re_[(re_.ev == ev) & (re_["dir"] == d) & (re_.tf == tf) & (re_.form == form) & (re_.E == E) & (re_.W == W) & (re_.S == S)
            & (re_.m == m) & (re_["T"] == T) & (re_.r == rr) & (re_.N == N)]
    return q.iloc[0] if len(q) else None
AXV = [("якорь", ("E0", 0, "S1", 0.0, "TS", -1.0, 10))] + [(f"E1 W={w}", ("E1", w, "S1", 0.0, "TS", -1.0, 10)) for w in (4, 10, 32)] \
    + [(f"S2 ×{m:g}", ("E0", 0, "S2", m, "TS", -1.0, 10)) for m in (0.5, 1.0, 2.0)] \
    + [(f"T-R {x:g}R", ("E0", 0, "S1", 0.0, "TR", x, 10)) for x in (0.5, 1.0, 2.0)] + [(f"N={n}", ("E0", 0, "S1", 0.0, "TS", -1.0, n)) for n in (4, 32)]
def axcell(ev, d, tf, v):
    c = cell(ev, d, tf, "B", *v)
    return "—" if c is None or c.n < 30 else f"{c.net:.1f}"
axh = "| правила | " + " | ".join(n for n, _ in AXV) + " |\n|" + "---|" * (len(AXV) + 1) + "\n"
axb = "".join(f"| {ev}·B·{TF[tf]}·{SD[d]} | " + " | ".join(axcell(ev, d, tf, v) for _, v in AXV) + " |\n"
              for ev in ("A5", "A6", "A7", "A8") for tf in (60, 240, 1440) for d in ("H", "L"))
def pool(v, tf):
    c = [cell(ev, d, tf, "B", *v) for ev in ("A5", "A6", "A7", "A8") for d in ("H", "L")]
    c = [x for x in c if x is not None]
    return [sum(x[k] for x in c) for k in ("n_att", "n", "r_nofill", "r_stop_invalid", "r_no_target", "r_stop", "r_target", "r_deadline")]
fh = "| ТФ | ячейка | сигналов | сделок | ретест не исполнен | стоп невалиден | нет цели | выход: стоп | цель | дедлайн |\n|" + "---|" * 10 + "\n"
fb = ""
for tf in (60, 240, 1440):
    for nm, v in AXV:
        a_, n_, nf, si, nt, st, tg, dl = pool(v, tf)
        pc = lambda x: f"{x / a_:.1%}" if a_ else "—"
        fb += f"| {TF[tf]} | {nm} | {int(a_)} | {int(n_)} | {pc(nf)} | {pc(si)} | {pc(nt)} | {pc(st)} | {pc(tg)} | {pc(dl)} |\n"
oa = ov.groupby(["form", "tf"], as_index=False)[["trades", "overlapped"]].sum()
ovb = "".join(f"| {a.form} | {TF[int(a.tf)]} | {int(a.trades)} | {a.overlapped / a.trades:.1%} |\n" for _, a in oa.iterrows())
axes_md = f"""## Кривые по осям (описательно, вне семейства Холма)

Форма B, события A5–A8, обе стороны, все 84 монеты и годы; ячейка = среднее net смеси, bps на сделку (одна ось меняется, остальные — якорь: E0, S1, T-S, N=10; «—» — меньше 30 сделок). Полная сетка E×S×T×N и роли контролей C1/C2 — `-axes.csv`. Кривые без ДИ и не входят в вердикт. E1 W=32 = W=10: окно заявки обрезано дедлайном N=10 (допущение сима).

{axh}{axb}
Сводная воронка по ячейкам (форма B, A5–A8, доли от сигналов; «неразрешимо» = стоп невалиден + нет цели; «ретест не исполнен» — только E1):

{fh}{fb}
Перекрытие: сделки по сигналам считаются независимо и не исключаются; доля якорных сделок, чей сигнал пришёл до конца предыдущей сделки той же (монета, ТФ, событие, сторона, форма); конец = t0 + удержание в барах (приближённо), `-axes-overlap.csv`:

| форма | ТФ | сделок | перекрыто |
|---|---|---|---|
{ovb}"""
md = f"""# Санчо, слой 3: метод как стратегия со стопом/целью и издержками (TK-455, В-65)

Протокол до счёта: `docs/research/P-TK455-sancho-layer3-2026-10-30.md` (утверждён Судьёй, `docs/research/reviews/tk455-protocol-2026-10-30.md`).
Данные: минутки Binance USDⓈ-M, 84 монеты × ТФ 1H/4H/1D, 2020–2026; издержки 2/5 bps (мейкер/тейкер) + фандинг по факту. Все числа — bps на сделку после издержек.

## Итог

**Из {len(r)} строк семейства Холма «держится» — 0; «оспорено» — {cnt.get('оспорено',0)}; «не различимо» — {cnt.get('не различимо',0)}.**
- (D) против контролей (C1 псевдо-уровни, C2 бары без условия), поправка Холма K=60: подтверждено {nD.get('подтверждено',0)}, оспорено {nD.get('оспорено',0)}, не различимо {nD.get('не различимо',0)}. Наименьшее p Холма — {r.p_D_holm.min():.2f} (A6·B·1D·хай: Δ к C1 {r[(r.ev=='A6')&(r.form=='B')&(r.tf==1440)&(r['dir']=='H')].delta_C1.iloc[0]:.0f}, к C2 {r[(r.ev=='A6')&(r.form=='B')&(r.tf==1440)&(r['dir']=='H')].delta_C2.iloc[0]:.0f}, 423 сделки; в «сырых» p=0,00, после Холма не проходит).
- (P) после издержек: положительно {nP.get('положительно',0)} (A1, A4 · форма A · 1D · хай — по годам знак смешан, против контролей не отличается), отрицательно {nP.get('отрицательно',0)}, не различимо {nP.get('не различимо',0)}.
- «Оспорено» везде по (P): ожидание отрицательно даже при лучшем конце издержек (все ноги мейкер) — {', '.join(f"{a.ev}·{a.form}·{TF[int(a.tf)]}·{SD[a['dir']]}" for _, a in neg.iterrows())}.
- Форма B (стоп за экстремум свипа, цель — структурный свинг, дедлайн N=10) на 1H почти везде даёт ≈ −9…−11 bps на сделку при N 2,6–176 тыс.: узкий ДИ, ниже нуля, от контролей не отличается (Δ≈0) — издержки съедают нулевой край.
- Ближе всех к порогу — A6·B·1D·хай (+61 bps, 423 сделки, 7 лет из 7 положительны, Δ к C1 +127, к C2 +135, сырые p=0,002/0,003): после Холма K=60 p=0,16, (P) при худшем конце издержек сырое p=0,02 → не различимо. Далее A8·B·4H·лой (+23, 7/7 лет, Холм p=0,41). Это кандидаты на отдельную проверку, не результат.
- Безубыточное проскальзывание на ногу (`breakeven_slip_bps_leg`) и фандинг — в `-rows.csv`. Торговых рекомендаций нет — только измеренное и объём проверки (K=60, B=20000 кластеров-дней).

## Таблица. Все 60 строк семейства (форма A — выход по close через N=10 баров, тейкер-тейкер; форма B — стоп за экстремум свипа S1, цель структурная T-S, дедлайн N=10, вход E0 по рынку, цель — мейкер)

net: «все мейкер» — лучший конец издержек, «все тейкер» — худший; жирное — смесь по протоколу. Годы — среднее net смеси по году (н/д — год с N < 8 событий или < 5 монет). «с 2020 / позже» — монеты с историей с 2020 / остальные.

{hdr}{rows()}
{axes_md}
## Воронка и проверки

Воронка по строкам (сигналов / сделок / стоп невалиден / нет цели) — `-funnel.csv`. Сверка числа сигналов со слоем 2: 504/504 совпало (84 монеты × 3 ТФ × k1/k2, включая C1/C2). Ускорение (один скан на сигнал) = прямому счёту: 0 расхождений из 15042. C3 («монетка» — случайная сторона, те же стоп/цель/издержки): в `-rows.csv` (`c3_mean_mix`), описательно.

## Не покрыто этим отчётом (честно)

Оси титрования и воронка — раздел «Кривые по осям» выше. Датированной истории тарифа Binance нет — взята единая пара 2/5 bps для всех лет (вопрос зафиксирован в логе тикета). По монетам — `-syms.csv`.

**Алгоритм:** варианты — (а) пересчёт минут на ячейку; (б) один скан на сигнал по всем ячейкам; (в) то же на calc, 15 ядер по монетам; замер — (б) совпало с (а) в 15042 ячейках без расхождений, проба ETH 1H 9,3 мин/ядро; выбран — (в), потому что быстро и без потери качества. Агрегатор: категориальная загрузка 26 млн строк (первый прогон падал по OOM).

Артефакты: `{out.split('/')[-1]}-rows.csv`, `-years.csv`, `-syms.csv`, `-funnel.csv`, `-axes.csv`, `-axes-overlap.csv`; код `tools/sancho_tk455_findings.py`.
"""
open(out + ".md", "w", encoding="utf-8", newline="\n").write(md)
for n in ("rows", "years", "syms", "funnel", "axes", "axes-overlap"):
    open(f"{out}-{n}.csv", "w", encoding="utf-8", newline="").write(open(f"{src}/sancho-l3-{n}.csv", encoding="utf-8", newline="").read())
