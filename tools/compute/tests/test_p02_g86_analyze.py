"""p02-g86-analyze.py: парная разность «рынок − лимит» по суткам закрытия на синтетических кругах."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "p02-g86-analyze.py")
HEAD = "symbol,day_utc,form,signal_index,t0_ns,dir,entry_px,exit_px,qty,net_bps,reason,exit_ns,fill_frac,entry_vwap,legs_filled,legs_rejected"
NS = 1_000_000_000
D1 = 1_785_542_400  # 2026-08-01 00:00 UTC


def write_run(home, run, form, trades):
    for day, rows in trades.items():
        d = os.path.join(home, run, day, "t-bid-btc4h-q1")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "rounds.csv"), "w", encoding="utf-8") as f:
            f.write("# lob bounce-grid: synthetic\n" + HEAD + "\n")
            for sym, t0, t1, net in rows:
                f.write(f"{sym},{day},{form},0,{t0},1,1.0,1.0,500,{net},take,{t1},1.0,1.0,1,0\n")
        open(os.path.join(home, run, day + ".done"), "w").close()


def test_paired_delta(tmp_path):
    home = str(tmp_path)
    day1, day2 = "2026-08-01", "2026-08-02"
    t = lambda s: (D1 + s) * NS
    write_run(home, "b5/p02-h9e899-aug-limit", "ladder3x2..20w2-pct2-tr1x1-14400-ttl1800",
              {day1: [("AAAUSDT", t(3600), t(7200), 10.0)], day2: []})
    write_run(home, "b5/p02-h9e899-aug-market", "market-pct2-tr1x1-14400-ttl1800",
              {day1: [("AAAUSDT", t(3600), t(7200), 30.0), ("TRXUSDT", t(3600), t(7200), 500.0)],
               day2: [("BBBUSDT", t(90000), t(93600), -20.0)]})
    out = os.path.join(home, "r.json")
    subprocess.run([sys.executable, SCRIPT, "--home", home, "--out", out], check=True, capture_output=True)
    r = json.load(open(out, encoding="utf-8"))["август"]
    # $500 × bps: сутки 1 — (30 − 10) → +$1,00; сутки 2 — −20 → −$1,00; TRX выброшен
    assert r["daily_delta"] == {day1: 1.0, day2: -1.0}
    assert r["delta_total"] == 0.0
    assert r["cells"]["рынок"]["rounds"] == 2 and r["cells"]["лимит"]["rounds"] == 1
