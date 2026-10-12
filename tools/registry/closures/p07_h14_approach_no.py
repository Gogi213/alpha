"""П-07 H14 (Г-138/143/142): номер подхода стены на входе. Плагин KIND='keep' (М5, В-227 пакет F), логика p07-h9r-h14.py (archive).
Номер = approach_index + 1 строки кэша подходов D20 (side=bid, arm_ms == t0_ns // 1e6, тот же price_tick). Правило: k1/k2/k3 — «только
первые K» (номер <= K), n2/n3/n4 — «с N-го и дальше» (номер >= N). Сигнал без связи со стеной не режется ни одним правилом (как в §7
retries-2026-09-27). Какая Г какой клетке соответствует — по спеке (Г-142 = k1); соответствие остальных — Судье при составлении сетки."""
NAME = "p07_h14"
KIND, SOURCES = "keep", ("approaches",)
# e<k> (Г-138, спека: k {1,2,3,4,5,6,8,10}, 10 = «10+»): ровно k-й подход (e10 — десятый и дальше)
PARAMS = {"rule": ("k1", "k2", "k3", "n2", "n3", "n4", "e1", "e2", "e3", "e4", "e5", "e6", "e8", "e10")}


def run(data, ctx, rule):
    k = int(rule[1:])
    cache, keep = {}, []
    for d in data:
        key = (d["symbol"], d["day_utc"])
        if key not in cache:
            cache[key] = ctx.approaches(*key)
        wall = cache[key].get((int(d["t0_ns"]) // 1_000_000, d["price_tick"]))
        no = None if wall is None else int(wall["approach_index"]) + 1
        ok = None if no is None else (no <= k if rule[0] == "k" else no >= k if rule[0] == "n" else (no >= k if k == 10 else no == k))
        if no is None or ok:
            keep.append((d["symbol"], d["day_utc"], d["form"], d["signal_index"]))
    return keep
