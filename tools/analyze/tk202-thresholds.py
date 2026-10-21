#!/usr/bin/env python3
"""TK-202 (В-227 пакет B, решение Судьи 12.10 04:14): пороги T_m(q) для осей usd_min_q и ctx_q по сигналам B1 (ключи b1keys-cap0.csv).

usd_min: usd = price_tick · tick_size · size_at_arm · qty_step — те же поля, что читает Rust-допуск (`admits_row`: price_tick·tick·size_at_touch·lot);
у подхода size_at_touch = size_at_arm (`touch_view_of_approach`), size_at_arm — /data/tk064/pool/m-<мес>/lvl/<сутки>.csv, price_tick — signals.csv, tick/lot — instruments.csv монеты.
ctx_q: значения осей pool1h / btc1h / btc4h (bps) на минуте t0 (floor до минуты, как `touch_contexts`) из <eNN>/study/regime/<сутки>.csv; пороги страт q20/40/60/80 (квинтили).
Окно — расширяющееся (как p12-r2b-thresholds.py): месяц m — сигналы B1 всех ПРЕДЫДУЩИХ полных месяцев; январь — по самому январю. Квантиль — ближайший ранг.
Нет size / price_tick / значения оси — сигнал вне популяции порога (счётчик в выходе).
behind_min (Судья 04:30): behind = depth_behind_lots·100/size_at_arm (поля Rust-допуска) из уцелевших подходов D20
<tk046>/<мес>/home/alpha/epochs/e-<мес>/study/approaches/D20/<сутки>/approaches-<SYM>.csv, ключ (side=bid, arm_ms=t0, price_tick из signals); читать только юнитом alsched.

Запуск: tk202-thresholds.py <b1keys.csv> <pool /data/tk064/pool> <корень tk046 /data/tk046> <instruments.csv> <выход.json> [--sample-day <сутки>] [--q 23,54,82]
--q — доп. процентили (целые 1…99, F2 П-14 §3.3: разрешимость 1) к QS; без --q выход прежний.
Выход: {"usd": {"ГГГГ-ММ": {"q": T, ...}}, "behind": {то же, целые %}, "ctx": {"<ось>": {"ГГГГ-ММ": {"q20": T, ...}}}, "n": {...}}"""
import csv, json, math, sys
from collections import defaultdict

MON = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct"]
QS = (5, 10, 20, 33, 50, 67, 80, 90, 95)
QUINT = (20, 40, 60, 80)
AXES = {"pool1h": "pool_ret_1h_bps", "btc1h": "btc_ret_1h_bps", "btc4h": "btc_ret_4h_bps"}


def rank(a, p):   # ближайший ранг без интерполяции
    a = sorted(a)
    return a[min(len(a) - 1, max(0, math.ceil(p / 100 * len(a)) - 1))]


def day_of(ms):
    import datetime
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")


def main():
    global QS
    keys_f, pool, t46, inst_f, out_f = sys.argv[1:6]
    if "--q" in sys.argv:
        QS = tuple(sorted(set(QS) | {int(x) for x in sys.argv[sys.argv.index("--q") + 1].split(",") if 1 <= int(x) <= 99}))
    keys = defaultdict(set)
    for r in csv.DictReader(open(keys_f, encoding="utf-8")):
        keys[r["month"]].add((r["symbol"], int(r["t0_ms"])))
    inst = {r["symbol"]: (float(r["tick_size"]), float(r["qty_step"])) for r in csv.DictReader(open(inst_f, encoding="utf-8"))}
    usd, behind, ctx, n = {}, {}, {a: {} for a in AXES}, {}
    for mo in sorted(keys):
        mon = MON[int(mo[5:]) - 1]
        bydays = defaultdict(set)
        for s, t in keys[mo]:
            bydays[day_of(t)].add((s, t))
        u, bh, c, miss = [], [], {a: [] for a in AXES}, defaultdict(int)
        for day, ks in sorted(bydays.items()):
            sig = {}
            for sub in ("signals", "signals-retry"):
                try:
                    with open(f"{pool}/m-{mon}/{sub}/{day}/t-bid-btc4h-q1/signals.csv", encoding="utf-8") as ih:
                        for r in csv.DictReader(l for l in ih if not l.startswith("#")):
                            sig[(r["symbol"], int(r["t0_ns"]) // 1000000)] = int(float(r["price_tick"]))
                except FileNotFoundError:
                    pass
            lv = {}
            try:
                for r in csv.DictReader(open(f"{pool}/m-{mon}/lvl/{day}.csv", encoding="utf-8")):
                    lv[(r["symbol"], int(float(r["arm_ms"])), int(float(r["price_tick"])))] = float(r["size_at_arm"] or 0)
            except FileNotFoundError:
                pass
            rg = {}
            try:
                for r in csv.DictReader(open(f"{t46}/{mon}/home/alpha/epochs/e-{mon}/study/regime/{day}.csv", encoding="utf-8")):
                    rg[int(r["minute_ms"])] = r
            except FileNotFoundError:
                pass
            apr = {}
            for s in {s for s, _ in ks}:
                try:
                    for r in csv.DictReader(open(f"{t46}/{mon}/home/alpha/epochs/e-{mon}/study/approaches/D20/{day}/approaches-{s}.csv", encoding="utf-8")):
                        if r["side"] == "bid":
                            apr[(s, int(r["arm_ms"]), int(r["price_tick"]))] = (float(r["size_at_arm"] or 0), float(r["depth_behind_lots"] or 0))
                except FileNotFoundError:
                    pass
            for s, t in ks:
                pt = sig.get((s, t))
                ap = apr.get((s, t, pt)) if pt is not None else None
                if ap and ap[0] > 0:
                    bh.append(ap[1] * 100 / ap[0])
                else:
                    miss["behind"] += 1
                sz = lv.get((s, t, pt)) if pt is not None else None
                if sz and sz > 0 and s in inst:
                    u.append(pt * inst[s][0] * sz * inst[s][1])
                else:
                    miss["usd"] += 1
                row = rg.get(t - t % 60000)
                for a, col in AXES.items():
                    v = row.get(col, "") if row else ""
                    if v != "":
                        c[a].append(float(v))
                    else:
                        miss[a] += 1
        usd[mo] = {"vals": u}
        behind[mo] = {"vals": bh}
        for a in AXES:
            ctx[a][mo] = {"vals": c[a]}
        n[mo] = {"signals": len(keys[mo]), "usd": len(u), "behind": len(bh), **{a: len(c[a]) for a in AXES}, "miss": dict(miss)}
    months = sorted(keys)
    res_usd, res_beh, res_ctx = {}, {}, {a: {} for a in AXES}
    for i, mo in enumerate(months):
        win = months[:i] if i else months[:1]   # январь — по самому январю
        uv = [v for m in win for v in usd[m]["vals"]]
        if uv:
            res_usd[mo] = {str(q): round(rank(uv, q)) for q in QS}
        bv = [v for m in win for v in behind[m]["vals"]]
        if bv:
            res_beh[mo] = {str(q): round(rank(bv, q)) for q in QS}
        for a in AXES:
            av = [v for m in win for v in ctx[a][m]["vals"]]
            if av:
                res_ctx[a][mo] = {f"q{q}": round(rank(av, q), 2) for q in QUINT}
    json.dump({"usd": res_usd, "behind": res_beh, "ctx": res_ctx, "n": n}, open(out_f, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(n, ensure_ascii=False))


main()
