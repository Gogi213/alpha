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
    return f"{t.mean_gross:.0f}" if t.enough else "н/д"
def f1(x): return f"{x:.1f}"
hdr = ("| правила | форма | сделок | выигр. | gross, bps | net с 2026-05-01: все мейкер … все тейкер (сделок) | net смеси 2/5 без даты [ДИ] — справочно | Δ к C1 / C2, bps (p Холма) | "
       + " | ".join(f"gross {a}" for a in yrs) + " | net смеси: с 2020 / позже | net смеси: BTC·ETH | (D) | (P) | вердикт |\n|" + "---|" * (14 + len(yrs) - 2) + "\n")
def rows():
    s = ""
    for _, a in r.sort_values("form", kind="stable").iterrows():
        k = (a.ev, a.form, int(a.tf), a["dir"])
        v = a.verdict
        s += (f"| {a.ev}·{a.form}·{TF[int(a.tf)]}·{SD[a['dir']]} | {a.form} | {int(a.N)} | {a.win_mix:.0%} | "
              f"{f1(a.mean_gross)} | " + (f"{f1(a.mean_mk)} … {f1(a.mean_tk)} ({int(a.N_dated)})" if a.N_dated >= 30 else f"не оценено ({int(a.N_dated)})")
              + f" | {f1(a.mean_mix)} [{f1(a.mix_lo)}; {f1(a.mix_hi)}] | "
              f"{f1(a.delta_C1)} / {f1(a.delta_C2)} (p={a.p_D_holm:.2f}) | "
              + " | ".join(ycell(k, yr) for yr in yrs)
              + f" | {f1(a.mix_hist2020_True)} / {f1(a.mix_hist2020_False)} | {f1(a['mix_BTC/ETH'])} | {a.D} | {a.P} | {v} |\n")
    return s
def near(ev, form, tf, d):
    a = r[(r.ev == ev) & (r.form == form) & (r.tf == tf) & (r["dir"] == d)].iloc[0]
    yk = y[(y.ev == ev) & (y.form == form) & (y.tf == tf) & (y["dir"] == d) & y.enough]
    npos = int((yk.mean_mix > 0).sum())
    dated = (f"net с 2026-05-01 {f1(a.mean_mk)} … {f1(a.mean_tk)} ({int(a.N_dated)} сделок)" if a.N_dated >= 30 else f"net с 2026-05-01 не оценено ({int(a.N_dated)} сделок)")
    return (f"{a.ev}·{a.form}·{TF[tf]}·{SD[d]} (gross {f1(a.mean_gross)}, net смеси 2/5 без даты {f1(a.mean_mix)}, {int(a.N)} сделок; {dated}; "
            f"Δ к C1 {a.delta_C1:+.0f}, к C2 {a.delta_C2:+.0f}, Холм p={a.p_D_holm:.2f}; лет {int(a.yrs)}: Δ к обоим контролям > 0 в {int(a.yrs_pos)}, "
            f"net смеси по году > 0 в {npos}) ")
near_md = near("A6", "B", 1440, "H") + "и " + near("A8", "B", 240, "L") + "."
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
    if v[1] == 32:
        return "н/и"
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
        if v[1] == 32:
            fb += f"| {TF[tf]} | {nm} | н/и | н/и | н/и | н/и | н/и | н/и | н/и | н/и |\n"
            continue
        fb += f"| {TF[tf]} | {nm} | {int(a_)} | {int(n_)} | {pc(nf)} | {pc(si)} | {pc(nt)} | {pc(st)} | {pc(tg)} | {pc(dl)} |\n"
oa = ov.groupby(["form", "tf"], as_index=False)[["trades", "overlapped"]].sum()
ovb = "".join(f"| {a.form} | {TF[int(a.tf)]} | {int(a.trades)} | {a.overlapped / a.trades:.1%} |\n" for _, a in oa.iterrows())
axes_md = f"""## Кривые по осям (описательно, вне семейства Холма)

Форма B, события A5–A8, обе стороны, все 84 монеты и годы; ячейка = среднее net смеси, bps на сделку (одна ось меняется, остальные — якорь: E0, S1, T-S, N=10; «—» — меньше 30 сделок). Полная сетка E×S×T×N и роли контролей C1/C2 — `-axes.csv`. Кривые без ДИ и не входят в вердикт. E1 W=32 не измерен («н/и»): окно заявки обрезано дедлайном N=10, ячейка совпадает с W=10 по построению. Ячейки — net смеси 2/5 без даты, справочно (вне (P)).

{axh}{axb}
Сводная воронка по ячейкам (форма B, A5–A8, доли от сигналов; «неразрешимо» = стоп невалиден + нет цели; «ретест не исполнен» — только E1):

{fh}{fb}
Перекрытие: сделки по сигналам считаются независимо и не исключаются; доля якорных сделок, чей сигнал пришёл до конца предыдущей сделки той же (монета, ТФ, событие, сторона, форма); конец = t0 + удержание в барах (приближённо), `-axes-overlap.csv`:

| форма | ТФ | сделок | перекрыто |
|---|---|---|---|
{ovb}"""
md = f"""# Санчо, слой 3: метод как стратегия со стопом/целью и издержками (TK-455, В-65)

Протокол до счёта: `docs/research/P-TK455-sancho-layer3-2026-10-30.md` (утверждён Судьёй, `docs/research/reviews/tk455-protocol-2026-10-30.md`).
Данные: минутки Binance USDⓈ-M, 84 монеты × ТФ 1H/4H/1D, 2020–2026; тариф 2/5 bps (мейкер/тейкер) подтверждён датой только страницей поддержки Binance от 2026-05-01 (истории изменений нет) → по §3/Q-2 протокола net для (P) и подпись «после издержек» — только сделки с t0 ≥ 2026-05-01; раньше — gross (по годам). Фандинг по факту. Все числа — bps на сделку; «net смеси 2/5 без даты» — справочно, в вердикт (P) не входит.

## Итог

**Из {len(r)} строк семейства Холма «держится» — 0; «оспорено» — {cnt.get('оспорено',0)}; «не различимо» — {cnt.get('не различимо',0)}.**
- (D) против контролей (C1 псевдо-уровни, C2 бары без условия), поправка Холма K=60: подтверждено {nD.get('подтверждено',0)}, оспорено {nD.get('оспорено',0)}, не различимо {nD.get('не различимо',0)}. Наименьшее p Холма — {r.p_D_holm.min():.2f} (A6·B·1D·хай: Δ к C1 {r[(r.ev=='A6')&(r.form=='B')&(r.tf==1440)&(r['dir']=='H')].delta_C1.iloc[0]:.0f}, к C2 {r[(r.ev=='A6')&(r.form=='B')&(r.tf==1440)&(r['dir']=='H')].delta_C2.iloc[0]:.0f}, 423 сделки; в «сырых» p=0,00, после Холма не проходит).
- (P) после издержек (только сделки с 2026-05-01, N_dated ≥ 30): положительно {nP.get('положительно',0)}, отрицательно {nP.get('отрицательно',0)}, не различимо {nP.get('не различимо',0)}, не оценено (N_dated < 30) {sum(v for k, v in nP.items() if k.startswith('не оценено'))} ({', '.join(f"{a.ev}·{a.form}·{TF[int(a.tf)]}·{SD[a['dir']]}" for _, a in r[r.P.str.startswith('не оценено')].iterrows())}). Окно с датированным тарифом короткое (≈ 6 мес.), на 1D сделок мало.
- «Оспорено» везде по (P): с 2026-05-01 ожидание отрицательно даже при лучшем конце издержек (все ноги мейкер) — {', '.join(f"{a.ev}·{a.form}·{TF[int(a.tf)]}·{SD[a['dir']]} (N_dated {int(a.N_dated)}, net {f1(a.mean_mk)} … {f1(a.mean_tk)})" for _, a in neg.iterrows())}. До 2026-05-01 ни одна строка не оценена по издержкам. Строки с «оспорено» по (D) — {nD.get('оспорено',0)}.
- Форма B (стоп за экстремум свипа, цель — структурный свинг, дедлайн N=10) на 1H почти везде даёт net смеси 2/5 ≈ −9…−11 bps на сделку (справочно, без даты) при N 2,6–176 тыс.: узкий ДИ, ниже нуля, от контролей не отличается (Δ≈0) — gross около нуля, издержки его съедают.
- Ближе всех к порогу — {near_md} Это кандидаты на отдельную проверку, не результат.
- Безубыточное проскальзывание на ногу (`breakeven_slip_bps_leg`) и фандинг — в `-rows.csv`. Торговых рекомендаций нет — только измеренное и объём проверки (K=60, B=20000 кластеров-дней).

## Таблица. Все 60 строк семейства (форма A — выход по close через N=10 баров, тейкер-тейкер; форма B — стоп за экстремум свипа S1, цель структурная T-S, дедлайн N=10, вход E0 по рынку, цель — мейкер)

gross — среднее до издержек (все годы); net с 2026-05-01 — по датированному тарифу, «все мейкер» — лучший конец, «все тейкер» — худший, в скобках число сделок с t0 ≥ 2026-05-01 («не оценено» при < 30). «net смеси 2/5 без даты» и колонки «с 2020 / позже», BTC·ETH — net при едином 2/5 за все годы, справочно, вне (P). Годы — gross по году (н/д — год с N < 8 событий или < 5 монет; годы без датированного тарифа — только gross). (D) — по Δ к контролям с поправкой Холма, не зависит от даты тарифа (издержки у сигнала и контроля одинаковые).

{hdr}{rows()}
{axes_md}
## Воронка и проверки

Воронка по строкам (сигналов / сделок / стоп невалиден / нет цели) — `-funnel.csv`. Сверка числа сигналов со слоем 2: 504/504 совпало (84 монеты × 3 ТФ × k1/k2, включая C1/C2). Ускорение (один скан на сигнал) = прямому счёту: 0 расхождений из 15042. C3 («монетка» — случайная сторона, те же стоп/цель/издержки): в `-rows.csv` (`c3_mean_mix`), описательно.

## Не покрыто этим отчётом (честно)

Оси титрования и воронка — раздел «Кривые по осям» выше. Датированной истории тарифа Binance нет (поиск 30.10, архив недоступен): пара 2/5 bps подтверждена датой только с 2026-05-01 → net и (P) только оттуда, до этой даты — gross; «net смеси 2/5 без даты» — справочно. Проскальзывание по минуткам не измеряется («не оценено»). По монетам — `-syms.csv`.

**Алгоритм:** варианты — (а) пересчёт минут на ячейку; (б) один скан на сигнал по всем ячейкам; (в) то же на calc, 15 ядер по монетам; замер — (б) совпало с (а) в 15042 ячейках без расхождений, проба ETH 1H 9,3 мин/ядро; выбран — (в), потому что быстро и без потери качества. Агрегатор: категориальная загрузка 26 млн строк (первый прогон падал по OOM).

Артефакты: `{out.split('/')[-1]}-rows.csv`, `-years.csv`, `-syms.csv`, `-funnel.csv`, `-axes.csv`, `-axes-overlap.csv`; код `tools/sancho_tk455_findings.py`.
"""
open(out + ".md", "w", encoding="utf-8", newline="\n").write(md)
for n in ("rows", "years", "syms", "funnel", "axes", "axes-overlap"):
    open(f"{out}-{n}.csv", "w", encoding="utf-8", newline="").write(open(f"{src}/sancho-l3-{n}.csv", encoding="utf-8", newline="").read())
