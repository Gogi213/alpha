#!/usr/bin/env python3
"""TK-383 (возврат): агрегат 1м-базиса (data/tk383/basis1m-raw.csv) -> stdout; сверка с часовым basis-raw.csv; цикл «24 %»."""
import numpy as np, pandas as pd
D = "data/tk383"
X = pd.read_csv(f"{D}/basis1m-raw.csv", parse_dates=["open", "close"])
H = pd.read_csv(f"{D}/basis-raw.csv", parse_dates=["open", "close"])
X["month"] = X.open.dt.strftime("%Y-%m"); X["bas"] = X.b0 - X.b1; X["tot"] = X.netT + X.bas
print("сделок с 1м-базисом:", len(X), "из", len(H), "; покрытие минут p10/p50:", X.cover.quantile([.1, .5]).round(3).tolist())
g = X.groupby(["cls", "month"]).agg(n=("tot", "size"), coins=("coin", "nunique"), fund_net=("netT", "mean"), basis=("bas", "mean"),
    basis_med=("bas", "median"), tot=("tot", "mean"), tot_med=("tot", "median"), hit=("tot", lambda x: (x > 0).mean()),
    mae_p50=("up", lambda x: x.quantile(.5)), mae_p90=("up", lambda x: x.quantile(.9)), mae_p99=("up", lambda x: x.quantile(.99)))
print(g.round(1).to_string())
c = X.groupby("coin").tot.sum().sort_values()
print("монет с tot>0:", int((c > 0).sum()), "из", len(c), "сумма", round(c.sum()), "без топ-5", round(c.iloc[:-5].sum()), "медиана на сделку", round(X.tot.median(), 1))
print("топ:", c.tail(5).round(0).to_dict())
M = X.merge(H[["coin", "a", "b", "open", "close", "b0", "b1", "up"]], on=["coin", "a", "b", "open", "close"], suffixes=("", "_h"))
print("\nсверка 1м vs 1ч на тех же сделках (n=%d): базис-PnL средн 1м %.1f / 1ч %.1f; MAE p50 %.0f/%.0f p90 %.0f/%.0f p99 %.0f/%.0f; сумма tot 1м %.0f / 1ч %.0f" % (
    len(M), M.bas.mean(), (M.b0_h - M.b1_h).mean(), M.up.quantile(.5), M.up_h.quantile(.5), M.up.quantile(.9), M.up_h.quantile(.9), M.up.quantile(.99), M.up_h.quantile(.99),
    M.tot.sum(), (M.netT + M.b0_h - M.b1_h).sum()))
# MAE относительно фандинга сделки: доля сделок, где MAE > gross
print("доля сделок с MAE > gross фандинга:", round((X.up > X.gross).mean(), 3), "; MAE>100 бп:", round((X.up > 100).mean(), 3), "; >500:", round((X.up > 500).mean(), 3))
