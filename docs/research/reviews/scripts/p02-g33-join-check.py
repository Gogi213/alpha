"""Судья, П-02 Г-33 (2026-09-26): независимая сверка причинной цепочки §12 на одних монето-сутках.

Своя реализация правила §12 (dadd428 + дописанное по проверке): звено P касаемого уровня L — уровень той же
стороны, умерший в кадре рождения L, на |P.tick − L.tick| = 1, repriced = true; кандидатов на обеих соседних ценах
или двое на одной — разрыв; в кадре смерти P родились уровни и на P.tick+1, и на P.tick−1 — разрыв; traded_lots > 0
у единственного кандидата — не звено. Длина — без L. Сравнивает chain_len/chain_stop построчно с компакт-файлом
счёта (тот же порядок строк, что в touches-<SYM>.csv).
  python3 p02-g33-join-check.py levels.csv touches.csv compact.csv
"""
import csv, sys
from collections import defaultdict


def rows(path):
    f = open(path, encoding="utf-8", newline="")
    first = f.readline()
    if not first.startswith("#"):
        f.seek(0)
    return csv.DictReader(f)


lev, tou, comp = sys.argv[1:4]
died = defaultdict(lambda: defaultdict(list))   # (side, death_ms) -> tick -> [(repriced, traded, birth)]
born = defaultdict(set)                          # (side, birth_ms) -> {tick}
for r in rows(lev):
    k = (r["side"], int(r["death_ms"]))
    died[k][int(r["price_tick"])].append((r["repriced"] == "true", float(r["traded_lots"]), int(r["birth_ms"])))
    born[(r["side"], int(r["birth_ms"]))].add(int(r["price_tick"]))


def step(side, tick, birth):
    frame = died.get((side, birth), {})
    lo = [c for c in frame.get(tick - 1, []) if c[0]]
    hi = [c for c in frame.get(tick + 1, []) if c[0]]
    if not lo and not hi:
        return "none", None
    if (lo and hi) or len(lo) + len(hi) > 1:
        return "ambiguous", None
    ptick = tick - 1 if lo else tick + 1
    _, traded, pbirth = (lo or hi)[0]
    nb = born.get((side, birth), set())
    if ptick - 1 in nb and ptick + 1 in nb:
        return "ambiguous", None
    if traded > 0:
        return "nonlink", None
    return "link", (ptick, pbirth)


cr = rows(comp)
n = mism = 0
by_len = defaultdict(int)
bounced = defaultdict(lambda: [0, 0])
for t, c in zip(rows(tou), cr):
    side, tick, birth = t["side"], int(t["price_tick"]), int(t["birth_ms"])
    L, stop, cur = 0, None, (tick, birth)
    while True:
        kind, nxt = step(side, *cur)
        if kind != "link":
            stop = kind
            break
        L += 1
        cur = nxt
    n += 1
    if (c["side"], c["price_tick"], c["touch_index"]) != (t["side"], t["price_tick"], t["touch_index"]):
        print("ПОРЯДОК строк разошёлся на", n); break
    if int(c["chain_len"]) != L or c["chain_stop"] != stop:
        mism += 1
        if mism <= 5:
            print("расхождение:", t["side"], tick, birth, "моё", L, stop, "счёт", c["chain_len"], c["chain_stop"])
    b = "0" if L == 0 else ("1" if L == 1 else "2+")
    by_len[b] += 1
    bounced[b][0] += t["ended_by_death"] == "false"
    bounced[b][1] += 1
print(f"касаний {n}, расхождений {mism}; длины {dict(by_len)}; доля bounced " +
      ", ".join(f"{k}: {v[0]}/{v[1]} = {v[0] / v[1] * 100:.1f} %" for k, v in sorted(bounced.items())))
