#!/usr/bin/env python3
"""TK-383 (возврат, +OKX; копия tk383-funding-analyze.py): межбиржевой фандинг-арбитраж. Вход data/tk383/funding.csv; выход docs/findings/tk383-funding-arb-*.json/.md-таблицы (stdout).
Дневная сумма ставок по (биржа, монета) -> для каждой монеты, для дня d: выбор пары (шорт A / лонг B) по
ex-ante сигналу (разница сумм за день d-1), реализация — разница за день d. Комиссии — тариф VIP0 (public), тейкер/мейкер."""
import json, sys
import numpy as np, pandas as pd

D = "data/tk383"
df = pd.concat([pd.read_csv(f"{D}/funding.csv"), pd.read_csv(f"{D}/funding-okx.csv")])
L = json.load(open(f"{D}/lists.json")); L["okx"] = json.load(open(f"{D}/lists-okx.json"))
print("t range", pd.to_datetime(df.t_ms.min(), unit="ms"), pd.to_datetime(df.t_ms.max(), unit="ms"))
# интервал по медиане разности соседних записей
df = df.sort_values(["ex", "sym", "t_ms"])
df["dt_h"] = df.groupby(["ex", "sym"]).t_ms.diff() / 3.6e6
iv = df.groupby(["ex", "sym"]).dt_h.median().rename("iv").reset_index()
print("интервалы фандинга (медиана, ч), число инструментов:")
print(iv.groupby(["ex", "iv"]).size().unstack(0).fillna(0).astype(int).to_string())
df["day"] = pd.to_datetime(df.t_ms, unit="ms").dt.floor("D")
# бывают дубли монета/биржа (разные символы) — берём символ с макс. числом записей
cnt = df.groupby(["ex", "coin", "sym"]).size().rename("n").reset_index().sort_values("n").drop_duplicates(["ex", "coin"], keep="last")
df = df.merge(cnt[["ex", "coin", "sym"]], on=["ex", "coin", "sym"])
day = df.groupby(["coin", "ex", "day"]).rate.sum().unstack("ex")  # сумма ставок за сутки (доля)
nrec = df.groupby(["coin", "ex", "day"]).rate.size().unstack("ex")
# полные сутки: 2026-09-01 .. последняя полная
days = sorted(day.index.get_level_values("day").unique())
last_full = days[-2]
day = day[day.index.get_level_values("day") <= last_full]
print("последние полные сутки:", last_full.date())

EX = ["bybit", "binance", "gate", "bitget", "bingx", "okx"]
TAKER = dict(bybit=5.5, binance=5.0, gate=5.0, bitget=6.0, bingx=5.0, okx=5.0)  # бп, тариф VIP0 (публично)
MAKER = dict(bybit=2.0, binance=2.0, gate=1.5, bitget=2.0, bingx=2.0, okx=2.0)
HEAVY = {"bybit", "binance"}
cls = {}
for c in day.index.get_level_values("coin").unique():
    on = {ex for ex in EX if c in L[ex].values()}
    cls[c] = "heavy" if on & HEAVY else "light"  # light: только Gate/Bitget/BingX
print("монет по классам:", pd.Series(cls).value_counts().to_dict())

pairs = [(a, b) for a in EX for b in EX if a != b]  # шорт a, лонг b
rows = []
for coin, g in day.groupby(level="coin"):
    g = g.droplevel("coin")
    g = g.reindex(pd.date_range(g.index.min(), last_full, freq="D"))
    avail = [e for e in EX if g[e].notna().sum() >= 5]
    if len(avail) < 2:
        continue
    for a, b in [(a, b) for a in avail for b in avail if a < b]:
        diff = (g[a] - g[b]) * 1e4  # бп/сутки: шорт a, лонг b
        for d, v in diff.dropna().items():
            rows.append((coin, cls[coin], a, b, d, v))
P = pd.DataFrame(rows, columns=["coin", "cls", "a", "b", "day", "diff_bp"])
P["month"] = P.day.dt.strftime("%Y-%m")
print("пар-суток:", len(P), "пар монета-биржи:", P.groupby(["coin", "a", "b"]).ngroups)

out = {"dist": {}, "strategy": {}}
# 1. распределение |разности| в бп/сутки
q = P.assign(ab=P.diff_bp.abs()).groupby(["cls", "month"]).ab.describe(percentiles=[.5, .9, .99])
print("\n|разность ставок| бп/сутки по классам и месяцам:\n", q.round(2).to_string())
out["dist"] = json.loads(q.reset_index().to_json(orient="records"))

# 2. persistence: корреляция знаков/значений день d-1 и d (по паре)
P = P.sort_values(["coin", "a", "b", "day"])
P["prev"] = P.groupby(["coin", "a", "b"]).diff_bp.shift(1)
ok = P.dropna(subset=["prev"])
ok = ok[ok.day == ok.groupby(["coin", "a", "b"]).day.shift(0)]  # заглушка
corr = ok.groupby(["cls", "month"]).apply(lambda x: x.diff_bp.corr(x.prev)).round(3)
print("\nкорреляция разности d-1 -> d:\n", corr.to_string())

# 3. стратегия: ex-ante вход, если вчерашняя |разность| > θ (бп/сутки), направление = знак; держим пока вчерашняя
#    разность того же знака > 0; комиссии: вход+выход, 4 ноги, тейкер (T) или мейкер (M)
def costs(a, b, kind):
    f = TAKER if kind == "T" else MAKER
    return 2 * (f[a] + f[b])  # открыть 2 ноги + закрыть 2 ноги, бп от номинала одной ноги

res = []
for (coin, a, b), g in P.groupby(["coin", "a", "b"]):
    g = g.set_index("day")
    ser = g.diff_bp.reindex(pd.date_range(g.index.min(), g.index.max(), freq="D"))
    c = g.cls.iloc[0]
    for th in (10, 20, 50, 100):
        pos = 0  # +1 шорт a/лонг b, -1 обратно
        open_day = None
        trades = []
        prev = np.nan
        for d, v in ser.items():
            # решение на начало дня d по вчерашнему prev
            if pos == 0 and not np.isnan(prev) and abs(prev) > th:
                pos = 1 if prev > 0 else -1
                open_day, acc, p0 = d, 0.0, pos
            elif pos != 0 and (np.isnan(prev) or prev * pos <= 0):
                trades.append((open_day, d, acc, p0))
                pos = 0
            if pos != 0 and not np.isnan(v):
                acc += pos * v
            prev = v
        if pos != 0:
            trades.append((open_day, ser.index[-1], acc, p0))
        for o, cl, gross, pp in trades:
            res.append((coin, c, a, b, th, o, cl, (cl - o).days, gross, costs(a, b, "T"), costs(a, b, "M"), pp))
R = pd.DataFrame(res, columns=["coin", "cls", "a", "b", "th", "open", "close", "days", "gross", "cT", "cM", "pos"])
R["netT"] = R.gross - R.cT
R["netM"] = R.gross - R.cM
R["month"] = R.open.dt.strftime("%Y-%m")
agg = R.groupby(["cls", "th", "month"]).agg(trades=("gross", "size"), coins=("coin", "nunique"), days=("days", "mean"),
                                             gross=("gross", "mean"), netT=("netT", "mean"), netM=("netM", "mean"),
                                             hitT=("netT", lambda x: (x > 0).mean()),
                                             sd=("netT", "std")).round(2)
print("\nсделки ex-ante, бп на сделку (номинал одной ноги), по классам/порогам θ/месяцам:\n", agg.to_string())
out["strategy"] = json.loads(agg.reset_index().to_json(orient="records"))
# топ монет по суммарной netT при θ=50
top = R[R.th == 50].groupby(["coin", "cls"]).agg(trades=("netT", "size"), netT=("netT", "sum"), gross=("gross", "sum")).sort_values("netT", ascending=False).head(15)
print("\nтоп-15 монет по сумме netT (θ=50 бп/сут):\n", top.round(1).to_string())
# концентрация: доля суммарной netT>0 в топ-5 монет
pos = R[(R.th == 50)].groupby("coin").netT.sum()
print("\nθ=50: монет с положит. суммой:", int((pos > 0).sum()), "из", len(pos), "; сумма всех:", round(pos.sum(), 0),
      "; без топ-5:", round(pos.sort_values().iloc[:-5].sum(), 0))
R.to_csv(f"{D}/trades-okx.csv", index=False)
json.dump(out, open("docs/findings/tk383-funding-okx-2026-10-24.json", "w"), ensure_ascii=False)
