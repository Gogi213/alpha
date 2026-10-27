"""TK-432: выбор 24 кандидатов (по 6 с рынка: 3 вверх / 3 вниз) по хешу id; зерно tk432."""
import csv, hashlib, sys
ev = list(csv.DictReader(open("docs/findings/pump-events-2026-10-28.csv", encoding="utf-8")))
bad = {r["id"] for r in csv.DictReader(open("docs/findings/pump-labels-2026-10-28.csv", encoding="utf-8")) if r["метка"]}
out = []
for ex, mk in (("bybit", "linear"), ("bybit", "spot"), ("gate", "futures_usdt"), ("gate", "spot")):
    for sg in ("+", "-"):
        c = [r for r in ev if r["биржа"] == ex and r["рынок"] == mk and r["знак"] == sg and r["L4"] == "ok" and r["id"] not in bad]
        c.sort(key=lambda r: hashlib.md5(("tk432:" + r["id"]).encode()).hexdigest())
        out += c[:3]
w = csv.writer(open(sys.argv[1], "w", newline="", encoding="utf-8"))
w.writerow(["n", "id", "биржа", "рынок", "монета", "знак", "t_start_ms", "t_peak_ms", "t_end_ms", "ход_%", "длит_мин"])
for i, r in enumerate(out, 1):
    w.writerow([i, r["id"], r["биржа"], r["рынок"], r["монета"], r["знак"], r["t_start_ms"], r["t_peak_ms"], r["t_end_ms"], r["ход_%"], r["длит_мин"]])
