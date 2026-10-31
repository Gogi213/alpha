#!/usr/bin/env python3
"""TK-461: сведение D2b Санчо+bp+S1-S5 в одну главную таблицу. python tk461_final.py <findings-dir> <YYYY-MM-DD>"""
import sys
import pandas as pd

d, dt = sys.argv[1], sys.argv[2]
K = ["tf", "ev", "dir", "stop", "take", "par", "be_r", "ot", "otp"]
fam = {"sancho": "Санчо", "bp": "bp", "s": "S1-S5"}
cs, ys = [], []
for f, name in fam.items():
    c = pd.read_csv(f"{d}/tk461-d2b-{f}-cells-2026-10-31.csv")
    y = pd.read_csv(f"{d}/tk461-d2b-{f}-years-2026-10-31.csv")
    c["family"] = name
    y["family"] = name
    cs.append(c)
    ys.append(y)
c = pd.concat(cs, ignore_index=True)
y = pd.concat(ys, ignore_index=True)
for x in (c, y):
    for k in ("par", "be_r", "ot", "otp"):
        x[k] = x[k].astype(str)
K2 = ["family"] + K
yy = y[y.n >= 8]
c = c.merge(yy.groupby(K2).delta.min().rename("ymin").reset_index(), on=K2, how="left")
c = c.merge(yy.groupby(K2).apply(lambda g: " ".join(f"{int(r.year)}:{r.delta:.1f}" for r in g.sort_values("year").itertuples()))
            .rename("delta_по_годам").reset_index(), on=K2, how="left")
c["h_pos"] = (c.p_holm < 0.05) & (c.delta > 0)
c["h_neg"] = (c.p_holm < 0.05) & (c.delta < 0)
c["h_pos_yr"] = c.h_pos & (c.ymin > 0)
c["h_pos_yr_base"] = c.h_pos_yr & (c.base > 0)
c["verdict"] = "не различимо"
c.loc[c.h_pos_yr, "verdict"] = "лучше C-X (Холм, все годы)"
c.loc[c.h_pos_yr_base, "verdict"] = "лучше C-X (Холм, все годы), net>0"
c.loc[c.h_neg, "verdict"] = "хуже C-X (Холм)"
c["core"] = c["family"] + "|" + c["tf"].astype(str) + "|" + c["ev"] + "|" + c["dir"].astype(str) + "|" + c["side"].astype(str) + "|" + c["stop"] + "|" + c["take"] + "|" + c["par"] + "|" + c["be_r"]
cols = ["family", "tf", "ev", "dir", "side", "stop", "take", "par", "be_r", "ot", "otp", "n", "syms", "days", "base", "ctrl", "delta", "lo", "hi",
        "p", "p_holm", "ymin", "delta_по_годам", "small", "verdict"]
c.sort_values(["family", "tf", "ev", "dir", "side"])[cols].to_csv(f"{d}/sancho-bp-exits-{dt}.csv", index=False, float_format="%.4f")

top_all = c[c.h_pos_yr_base].sort_values("base", ascending=False)
top = top_all.drop_duplicates("core").head(10)
g = c.groupby("family")
t = pd.DataFrame({"вариантов": g.size(), "net>0": g.apply(lambda x: (x.base > 0).sum()),
                  "Δ>0 (точка)": g.apply(lambda x: (x.delta > 0).sum()), "Холм Δ>0": g.h_pos.sum(),
                  "Холм Δ<0": g.h_neg.sum(), "Холм Δ>0 + все годы": g.h_pos_yr.sum(), "…и net>0": g.h_pos_yr_base.sum(),
                  "независимых (без ОТ)": g.core.nunique(), "…из них с net>0 и Холм": g.apply(lambda x: x[x.h_pos_yr_base].core.nunique())})
t.loc["всего"] = [len(c), (c.base > 0).sum(), (c.delta > 0).sum(), c.h_pos.sum(), c.h_neg.sum(), c.h_pos_yr.sum(), c.h_pos_yr_base.sum(),
                  c.core.nunique(), c[c.h_pos_yr_base].core.nunique()]
t = t.astype(int)
t2 = c.groupby(["family", "tf", "side"]).agg(вариантов=("n", "size"), net_плюс=("base", lambda x: int((x > 0).sum())), холм_плюс_все_годы_net_плюс=("h_pos_yr_base", "sum")).reset_index()
tc = top[["family", "tf", "ev", "dir", "side", "stop", "take", "par", "be_r", "ot", "otp", "n", "syms", "base", "ctrl", "delta", "lo", "hi", "ymin", "delta_по_годам"]].round(2)
tc.insert(0, "#", range(1, len(tc) + 1))
top_ev = top_all.groupby(["family", "tf", "ev", "dir", "side"]).size().reset_index(name="ячеек (с ОТ)").sort_values("ячеек (с ОТ)", ascending=False).head(15)
md = f"""# TK-461 · выходы сигналов Санчо + bp + S1–S5 (D2b), сведение ({dt})
Протокол `docs/research/P-TK461-exits-2026-10-30.md` (§12.1: K = 163 923, Холм одной семьёй). Таблица: `sancho-bp-exits-{dt}.csv` (одна строка = вариант: вход × ТФ × сторона × стоп × тейк × безубыток × ОТ). Скрипты: `tools/compute/tk461_final.py` (сведение) ← `tk461_d2b_report.py` (ячейки, годы, Холм) ← ядро TK-463/467/469.

## Итог
Проверено **{len(c)}** вариантов в 3 семьях (84 монеты, 2020–2026, ТФ 60/240/1440; лонг и шорт раздельно). После издержек (Bybit 3,15/1,26 bps по ногам, фандинг по факту) положительны **{(c.base>0).sum()}**; лучше случайного входа **с тем же выходом** (C-X) по Холму со знаком во всех годах — **{c.h_pos_yr.sum()}**, из них net>0 — **{c.h_pos_yr_base.sum()}** ({c[c.h_pos_yr_base].core.nunique()} независимых настроек без учёта вариантов ОТ). Хуже C-X по Холму: {c.h_neg.sum()}.

{t.to_markdown()}

«Холм Δ>0» = p_holm<0,05 и Δ>0; «все годы» = Δ>0 в каждом году с n≥8; «net>0» = ожидание net самого варианта >0. «Независимых» = вход×ТФ×сторона×стоп×тейк×безубыток без ОТ (варианты ОТ у одной настройки дают почти одинаковые числа).

## Лучшие 10 (net>0, Холм Δ>0, знак во всех годах; по net на сделку, bps; по одной на настройку)
{tc.to_markdown(index=False)}

`base` — net на сделку, bps; `ctrl` — net случайных входов с тем же выходом; `delta` = base − ctrl; `lo/hi` — 95 % интервал Δ (бутстрап по дням UTC); `delta_по_годам` — Δ по годам, bps; n — сделок.

## Где сидят выжившие (число ячеек с ОТ; топ-15 по сигналу)
{top_ev.to_markdown(index=False)}

## Разрез семья × ТФ × сторона
{t2.to_markdown(index=False)}

## Оговорки
- **Не вердикт по протоколу §7:** сверено только с C-X; контроли C1/C2 для этих семей не считались — слово «подтверждено» не используется. Число сделок не критерий: малые N остались в таблице (`small`), интервал широкий.
- Множественность: Холм по K=163 923, но варианты ОТ одной настройки сильно зависимы, независимых настроек меньше (см. столбец «независимых»); смещение выжившего (монеты, живые на 2026) не снималось; открытие k/M/v/x для БУ и ОТ — на чётных монетах ≤2023, вся таблица считается на всех монетах (перекрёстная проверка закономерностей — отдельный отчёт).
- Размер эффекта у верхних строк bp (net 90–176 bps/сделку на D1) велик: перед выводом — проверка Судьёй на заглядывание вперёд (события g2.*, ss/ot1 на t0−1) и сверка нескольких сделок вручную; пока это «отличается от C-X», не «работает».
- **S6** (цепочки канала, вход — сделки автора) — не бар-событие; в таблицу не входит, нужна отдельная постановка. Тики 1/5 мин драйвером не проверялись (ТФ только 60/240/1440).
- Проскальзывание не оценено. Торговых рекомендаций нет; область применимости — перпы Bybit 2020–2026 по пулу 84 монет, ТФ 1H/4H/1D.
- Алгоритм: варианты — Python-ядро (tk461_sim, 24 мин на D2a) / Rust-ядро по ячейкам / потоковое ядро с агрегацией; замер — D2a 69 с против 24 мин, ТФ60 BMT 242→57 с, потоковое ≈1,1 ГБ/пару, 15×; выбрано потоковое ядро (TK-469) — тот же результат байт в байт, в разы быстрее.
"""
open(f"{d}/sancho-bp-exits-{dt}.md", "w", encoding="utf-8", newline="\n").write(md)
