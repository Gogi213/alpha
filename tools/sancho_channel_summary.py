"""TK-454: единая сводка чисел для sancho-channel-<дата>.md из csv (одно число — один источник).
Запуск: python tools/sancho_channel_summary.py <findings-dir>. Цепочка = первая строка trade_id в p1-all (как в sancho_channel_report.py).
Вход: p1-all, text, meta, t0, claim-tv, handtv. Печатает: охват уровней, исход свечей, сверка заявления, срезы NY/история на решённых, вход раньше бокса."""
import csv, sys, collections, datetime as dt, importlib.util, os
F = sys.argv[1] + "/"; S = "-2026-10-30.csv"
rd = lambda n: list(csv.DictReader(open(F + n + S, encoding="utf-8")))
sp = importlib.util.spec_from_file_location("rep", os.path.join(os.path.dirname(__file__), "sancho_channel_report.py"))
src = open(sp.origin, encoding="utf-8").read().split("seen = {}")[0].replace("sys.argv[1]", "sys.argv[1]")
ns = {}; exec(compile(src, "rep", "exec"), ns); cat = ns["cat"]
p, text, meta, t0 = rd("sancho-trades-p1-all"), rd("sancho-trades-text"), {m["trade_id"]: m for m in rd("sancho-trades-meta")}, rd("sancho-trades-t0")
seen = {}
for r in p: seen.setdefault(r["trade_id"], r)
rows = list(seen.values()); ids = {t["trade_id"] for t in text}
print(f"цепочек в text: {len(ids)}; в p1-all: {len(rows)} (строк {len(p)}); без строки: {sorted(ids - set(seen))}")
nob = sum(cat(r) == "нет бокса позиции на снимке" for r in rows); nol = sum(cat(r) == "нет символа/уровней" for r in rows)
print(f"уровни прочитаны: {len(rows) - nob - nol} (нет бокса на снимке {nob}, нет символа/уровней {nol})")
c = collections.Counter(cat(r) for r in rows); print("исход по свечам:", dict(c.most_common()))
print("сверка заявления (match, 1 строка на цепочку):", dict(collections.Counter(r["match"].split(":")[0] if r["match"].startswith(("совпало", "не совпало", "зависит")) else r["match"][:45] for r in rows).most_common()))
dec = [r for r in rows if cat(r) in ("первым цель1", "первым стоп")]
print(f"решённых (первым цель1/стоп): {len(dec)}: цель1 {sum(cat(r)=='первым цель1' for r in dec)}")
for key, name in (("session", "сессия NY"), ("history_since_2020", "история с 2020")):
    print(name + ":")
    for v in sorted({meta[r['trade_id']][key] for r in dec}):
        d = [r for r in dec if meta[r["trade_id"]][key] == v]; print(f"  {v or '—'}: решённых {len(d)}, цель1 первым {sum(cat(r)=='первым цель1' for r in d)}")
post = lambda s: int(dt.datetime.strptime(s, "%d.%m.%Y %H:%M").replace(tzinfo=dt.timezone(dt.timedelta(hours=4))).timestamp() * 1000)
edge = {(t["trade_id"], t["image_file"]): t for t in t0}
tfm = {"1m": 6e4, "5m": 3e5, "15m": 9e5, "30m": 18e5, "1h": 36e5, "2h": 72e5, "4h": 144e5, "1d": 864e5}
tot = inzone = 0
for r in p:
    if r["t0_source"] != "б" or not r["entry_min"] or r["entry_min"].split("|")[0] in ("", "None"): continue
    e = edge.get((r["trade_id"], r["image_file"]))
    if not e: continue
    tot += 1; tf = tfm.get(e["tf"], 36e5)
    if int(r["entry_min"].split("|")[0]) < post(e["box_left_gmt4"]): inzone += 1
print(f"вход раньше бокса: строк ветки б с минутой входа {tot}; вход раньше края бокса (в пределах −1 бар ТФ) {inzone}; раньше t0=край−1 бар — 0 по построению (окно П3 начинается в t0; глубже назад не искали — ширины окна в протоколе нет)")
