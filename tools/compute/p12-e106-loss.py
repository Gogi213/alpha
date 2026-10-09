#!/usr/bin/env python3
"""TK-120: e106-nostop — макс. убыток сделки и доля выходов по дедлайну среди убыточных (по готовым rounds.csv, без пересчёта).
Запуск: p12-e106-loss.py <out.txt>"""
import csv, glob, os, sys
fs = sorted(glob.glob("/data/tk0115/pool/m-jan/r3c/*/e106-pop/rounds.csv") + glob.glob("/data/tk0115/delta/wave/*/e106/*/e106-pop/rounds.csv"))
rows = []
for f in fs:
    with open(f, encoding="utf-8") as ih:
        for r in csv.DictReader(l for l in ih if not l.startswith("#")):
            r["_f"] = f; rows.append(r)
out = [f"файлов {len(fs)} строк {len(rows)}"]
if rows:
    out.append("колонки: " + ",".join(k for k in rows[0] if k != "_f"))
    forms = sorted({r["form"] for r in rows})
    for fm in forms:
        rs = [r for r in rows if r["form"] == fm]
        out.append(f"== {fm}: {len(rs)}")
        for r in sorted(rs, key=lambda r: float(r["net_bps"]))[:20]:
            out.append("  " + ",".join(f"{k}={v}" for k, v in r.items() if k in ("symbol", "t0_ns", "qty", "entry_vwap", "net_bps", "exit_reason", "reason", "exit_kind", "net_usd", "pnl_usd")))
open(sys.argv[1], "w", encoding="utf-8").write("\n".join(out) + "\n")
open(sys.argv[1] + ".done", "w").write("ok")
