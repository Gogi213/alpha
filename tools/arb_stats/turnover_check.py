"""Сверка оборота из записанных сделок (turnover.py) с 24-часовыми тикерами бирж.

python3 turnover_check.py win <tickers.json> <arb.json> <окна.json>   — окна для turnover.py (page — как у arb.json; chk — окно тикера: Binance/BingX — openTime…closeTime символа,
                                                                        Bybit/OKX — [время ответа − 24 ч, время ответа])
python3 turnover_check.py cmp <tickers.json> <turnover.json> [монеты через запятую] — наш оборот против тикера: по монетам и суммой по записанным символам.
Тикер: Binance/BingX — quoteVolume, Bybit — turnover24h, Gate — volume_24h_quote ($); OKX отдаёт только объём в монете (volCcy24h) — сверка по нему.
"""
import json
import sys
from pathlib import Path

DAY = 86_400_000


def tick(ex, d):
    """{символ: (окно_с, окно_по, $ или None, объём в монете или None)} — что биржа сообщает за 24 ч."""
    b = d[ex]["body"]
    out = {}
    if ex == "binance":
        for x in b:
            out[x["symbol"]] = (int(x["openTime"]), int(x["closeTime"]), float(x["quoteVolume"]), float(x["volume"]))
    elif ex == "bingx":
        for x in b["data"]:
            ct = int(x.get("closeTime") or 0)
            if ct:
                out[x["symbol"]] = (int(x["openTime"]), ct, float(x["quoteVolume"]), float(x["volume"]))
    elif ex == "bybit":
        t = int(b["time"])
        for x in b["result"]["list"]:
            out[x["symbol"]] = (t - DAY, t, float(x["turnover24h"]), float(x["volume24h"]))
    elif ex == "okx":
        for x in b["data"]:
            t = int(x["ts"])
            out[x["instId"]] = (t - DAY, t, None, float(x["volCcy24h"]))
    elif ex == "gate":                      # скользящие 24 ч на момент ответа; $ — volume_24h_quote, монеты — volume_24h_base
        t = int(d[ex]["t_resp"])
        for x in b:
            out[x["contract"]] = (t - DAY, t, float(x["volume_24h_quote"]), float(x["volume_24h_base"]))
    return out


def win(tk, arb, out):
    d, a = json.loads(Path(tk).read_text()), json.loads(Path(arb).read_text())
    chk = {}
    for ex in ("binance", "bybit", "okx", "bingx"):
        t = tick(ex, d)
        chk[ex] = {"def": [d[ex]["t_resp"] - DAY - 5000, d[ex]["t_resp"] - 5000], "sym": {s: [v[0], v[1]] for s, v in t.items()}}
    Path(out).write_text(json.dumps({"page": [a["t0"], a["t1"]], "chk": chk}, separators=(",", ":")))
    print({"page": [a["t0"], a["t1"]], "symbols": {ex: len(c["sym"]) for ex, c in chk.items()}})


def cmp(tk, to, coins):
    d, r = json.loads(Path(tk).read_text()), json.loads(Path(to).read_text())
    from common import norm
    print("биржа символ | окно тикера (с) | наш $ | тикер $ | наш/тикер | наш base | тикер base | наш/тикер base")
    tot = {}
    for ex in ("binance", "bybit", "okx", "bingx"):
        t = tick(ex, d)
        ours = r["sym"][ex]
        s_us = s_tk = s_ub = s_tb = 0.0
        n = 0
        for s, v in t.items():
            o = ours.get(s)
            if o is None:
                continue
            n += 1
            s_us += o[3]
            s_ub += o[4]
            s_tk += v[2] or 0.0
            s_tb += v[3]
            c = norm(ex, s, {s: {"displayName": s}})[0]
            if c in coins:
                print(f"{ex:8s} {s:16s} | {(v[1] - v[0]) / 1000:8.0f} | {o[3]:16,.0f} | {('—' if v[2] is None else format(v[2], ',.0f')):>16} | "
                      f"{('—' if v[2] is None else format(o[3] / v[2], '.4f')):>7} | {o[4]:16,.2f} | {v[3]:16,.2f} | {o[4] / v[3]:.4f}")
        tot[ex] = (n, s_us, s_tk, s_ub, s_tb)
    print("\nсумма по записанным символам (есть и в тикере):")
    for ex, (n, us, tk_, ub, tb) in tot.items():
        print(f"{ex:8s} символов {n:4d} | наш $ {us:18,.0f} | тикер $ {tk_:18,.0f} | " + (f"наш/тикер {us / tk_:.4f}" if tk_ else "—") + f" | base наш/тикер {ub / tb:.4f}")


if __name__ == "__main__":
    if sys.argv[1] == "win":
        win(*sys.argv[2:5])
    else:
        sys.path.insert(0, str(Path(__file__).parent))
        cmp(sys.argv[2], sys.argv[3], set((sys.argv[4] if len(sys.argv) > 4 else "BTC,SOL").split(",")))
