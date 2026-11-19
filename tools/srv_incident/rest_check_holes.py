#!/usr/bin/env python3
"""TK-581: остаточные дыры Binance (после вычитания `rt`, вывод holes_vs_bf.py) против REST `/fapi/v1/aggTrades`: есть ли в окне ±3 с вокруг дыры агрегат, содержащий номера дыры.
Если нет ни одного агрегата, чей [f, l] покрывает номера дыры, — номеров нет и на бирже (не потеря). Выборка: все строки с >= 2 номерами + равномерно по времени остальные, не более N запросов, 1 запрос/с.
python3 rest_check_holes.py <файл вывода holes_vs_bf.py> <N> [исключить HH:MM:SS-HH:MM:SS,...]
— исключаются строки, созданные в окнах после пауз (запись пришла с лагом до 50–90 с, окно REST ±3 с вокруг приёма её не накроет)."""
import sys, re, json, time, urllib.request
rows = []
for ln in open(sys.argv[1]):
    m = re.match(r"\s+остаток: (\S+) (\S+) (\d+) (\d+) (\d+) (\d+) (\d+)", ln)
    if m:
        rows.append((m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5)), int(m.group(7))))
N = int(sys.argv[2])
if len(sys.argv) > 3:
    h = lambda t: int(t[:2]) * 3600 + int(t[3:5]) * 60 + int(t[6:])
    ex = [(h(w.split('-')[0]), h(w.split('-')[1])) for w in sys.argv[3].split(',')]
    rows = [r for r in rows if not any(a <= (r[4] // 1000) % 86400 <= b for a, b in ex)]
multi = [r for r in rows if r[3] >= 2]
single = [r for r in rows if r[3] < 2]
step = max(1, len(single) // max(1, N - len(multi)))
pick = multi + single[::step]
pick = pick[:N]
print(f"остаточных строк {len(rows)} (>=2 номеров: {len(multi)}); проверяется {len(pick)}", flush=True)
res = {"exists_on_exchange": 0, "not_on_exchange": 0, "err": 0, "ids_exists": 0, "ids_not": 0}
for sym, lo, hi, n, cr in pick:
    url = f"https://fapi.binance.com/fapi/v1/aggTrades?symbol={sym}&startTime={cr - 3000}&endTime={cr + 1000}&limit=1000"
    time.sleep(1.0)
    try:
        data = json.load(urllib.request.urlopen(url, timeout=20))
    except Exception as e:
        res["err"] += 1; print(sym, lo, hi, "ошибка", e, flush=True); continue
    found = sum(max(0, min(hi, x["l"]) - max(lo, x["f"]) + 1) for x in data)
    near = [(x["f"], x["l"]) for x in data if x["l"] < lo][-1:] + [(x["f"], x["l"]) for x in data if x["f"] > hi][:1]
    if found:
        res["exists_on_exchange"] += 1; res["ids_exists"] += found
        print(f"{sym} {lo}-{hi}: НА БИРЖЕ {found} из {n}; соседи {near}", flush=True)
    else:
        res["not_on_exchange"] += 1; res["ids_not"] += n
        if not data:
            print(f"{sym} {lo}-{hi}: REST пуст (окно не найдено)", flush=True)
print("ИТОГО", res)
