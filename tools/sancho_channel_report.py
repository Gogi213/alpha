"""TK-454: сводка по минуткам для sancho-channel-<дата>.md. Запуск: python tools/sancho_channel_report.py <findings-dir>
Вход: sancho-trades-p1-all, sancho-trades-text. Исход по свечам (ε=0 / ε₁) для строки цепочки; без привязки к классу заявления."""
import csv, re, sys, collections
F = sys.argv[1] + "/"; S = "-2026-10-30.csv"
rd = lambda n: list(csv.DictReader(open(F + n + S, encoding="utf-8")))
p = rd("sancho-trades-p1-all"); tx = {t["trade_id"]: t for t in rd("sancho-trades-text")}
def cat(r):
    s = r["p1"]
    if s.startswith("нельзя: на снимках TV нет бокса"): return "нет бокса позиции на снимке"
    if s.startswith("не извлечено"): return "нет символа/уровней"
    if s.startswith("нельзя проверить"): return "нет данных свечей / времени"
    m = re.match(r"eps0:(\w+)@(\d*); eps1\([\d.]+ bps\):([\w ]+?)@", s + "@")
    if not m: return "нет минуты входа" if "нет минуты входа" in s else "нет времени входа"
    a, b = m.group(1), m.group(3)
    if a == "нет": return "нет минуты входа"
    if a == b: return "первым цель1" if a.startswith("цель") else ("первым стоп" if a == "стоп" else a)
    return "зависит от допуска (ε=0 ≠ ε₁)"
seen = {}
for r in p: seen.setdefault(r["trade_id"], r)
rows = list(seen.values())
yr = lambda r: tx[r["trade_id"]]["t_post_gmt4"][6:10]
tvset = [r for r in rows if r["claim_class"] == ""]
out = collections.Counter(cat(r) for r in rows)
print(len(rows), "цепочек (1 строка на цепочку)"); [print(" ", k, v) for k, v in out.most_common()]
print("по годам:")
for y in sorted({yr(r) for r in rows}):
    c = collections.Counter(cat(r) for r in rows if yr(r) == y); print(" ", y, sum(c.values()), dict(c))
# нулевая: без дрейфа P(цель1 раньше стопа) = стоп-расстояние / (стоп + цель) = 1/(1+RR) (мартингал); RR из уровней цепочки
lv = {}
for r in rd("sancho-trades") :
    try: lv.setdefault(r["trade_id"], (float(r["entry"]), float(r["stop"]), float(r["tp1"])))
    except ValueError: pass
for r in rd("sancho-trades-handtv"):
    try: lv.setdefault(r["trade_id"], (float(r["entry"]), float(r["stop"]), float(r["tp"])))
    except ValueError: pass
dec = [r for r in rows if cat(r) in ("первым цель1", "первым стоп") and r["trade_id"] in lv]
rr = lambda i: abs(lv[i][2] - lv[i][0]) / abs(lv[i][0] - lv[i][1])
null = sum(1 / (1 + rr(r["trade_id"])) for r in dec); k = sum(cat(r) == "первым цель1" for r in dec)
import math
var = sum((1 / (1 + rr(r["trade_id"]))) * (1 - 1 / (1 + rr(r["trade_id"]))) for r in dec)
print(f"решённых с RR: {len(dec)}; цель1 первым: {k}; нулевая сумма {null:.1f} ± {math.sqrt(var):.1f} (σ); средний RR {sum(rr(r['trade_id']) for r in dec)/len(dec):.2f}")
for y in sorted({yr(r) for r in dec}):
    d = [r for r in dec if yr(r) == y]; kk = sum(cat(r) == "первым цель1" for r in d)
    n0 = sum(1 / (1 + rr(r["trade_id"])) for r in d); print(" ", y, len(d), kk, f"нуль {n0:.1f}")
