#!/usr/bin/env python3
"""TK-015, П-07 (правило Судьи TK-010 `413da17`, dc250dc): чтение одного месяца января–июня — тот же путь, что
`p07-jul-read.py` (импорт без копирования, подмена глобалов модуля на месяц):

    main    b5/p07m-main    главный В-104 — вердикт
    b-base  b5/p07b-base    база Г-85б — вердикт
    b-k1    b5/p07b-base + keep K<=1 — описание, парно к b-base
    a-fr1   b5/p07a-h2-fr1  Г-85а fr+1 — описание (базы Г-85а нет — стоп, без пары)

Монета без минутных свечей (dc250dc, «то же правило — для января–июня»; уточнение Судьи 225814e, п. 7): в сутки с
флагом `n_no_sigma` > 1 % у монеты сигналы без σ и ни одной строки σ₂₄₀ за сутки. Сутки раньше первой свечи
(+ 240 мин окна σ) — «рынка ещё не было»: монета вон из всех форм на весь месяц (`--drop`, как TRX), ворота
пересчитываются без неё, имя и первая свеча печатаются. Свечей нет/пусто/дыра — сбой light: стоп месяца (код 1).

    python3 p07-h1-read.py --month jan --data-gate
    python3 p07-h1-read.py --month jan --out ~/alpha/tmp-p07/h1-read/jan.json
    python3 p07-h1-read.py --half ~/alpha/tmp-p07/h1-read/half.json   # после всех шести `<мес>.json`

Сверх пути июля (поправка 7, Судья 2d8648b): пул месяца — монета с первых своих суток (`POOL_FILTER`); в json месяца —
`daily_usd` форм, пары по суткам (Δ$ бутстрепом, Δ доли и Δ доли без каждых суток: база Г-85б − главный, K≤1 − база
Г-85б; B1–B3 июля сняты — денежных вердиктов нет), монеты пула и поздние, холодный старт, строки п. 5; `--half` — F
и вердикт главного и базы Г-85б, сумма $ по 181 суткам (блок 6), месяцев в минусе.
"""
import calendar
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun"]


def load_jul():
    for d in (HERE, os.path.expanduser("~/alpha/tmp-p07")):
        p = os.path.join(d, "p07-jul-read.py")
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location("p07julread", p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    raise SystemExit("нет p07-jul-read.py")


def sigma_days(jr, sym):
    """Сутки (YYYY-MM-DD), в которых у монеты есть хоть одна строка σ₂₄₀."""
    import datetime as dt
    sp = os.path.join(jr.JUL_HOME, "study", "sigma240", f"sigma-{sym}.csv")
    if not os.path.exists(sp):
        return set()
    with open(sp, encoding="utf-8") as fh:
        ms = {int(l.split(",", 1)[0]) // 86_400_000 for l in fh.read().splitlines()[1:] if l[:1].isdigit()}
    return {dt.datetime.fromtimestamp(d * 86400, dt.timezone.utc).strftime("%Y-%m-%d") for d in ms}


def first_candle_ms(jr, sym):
    """Первая минутная свеча монеты в `study/klines/ref-<SYM>-1m.csv` (None — файла нет или он пуст)."""
    p = os.path.join(jr.JUL_HOME, "study", "klines", f"ref-{sym}-1m.csv")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            if line[:1].isdigit():
                return int(line.split(",", 1)[0])
    return None


def no_candle_coins(jr):
    """Поправка 7 п. 7 (Судья 225814e) к dc250dc: в сутки с флагом n_no_sigma монета с сигналами без σ и без σ₂₄₀ за
    сутки. «Рынка ещё не было» — сутки начались раньше первой свечи + 240 мин (окно σ₂₄₀; премаркет/листинг, как KORU):
    монета вон на месяц. Иначе (файла свечей нет, пуст, или свечи были до этих суток — дыра) — сбой light: стоп месяца.
    -> ({монета: первая свеча UTC}, [причины стопа])."""
    import datetime as dt
    drop, stop = {}, []
    d20 = os.path.join(jr.JUL_HOME, "study", "approaches", "D20")
    for day in jr.JUL_DAYS:
        if "n_no_sigma > 1 % сигналов" not in jr.day_gate(day):
            continue
        d0 = int(calendar.timegm(tuple(map(int, day.split("-"))) + (0, 0, 0))) * 1000
        for f in os.listdir(os.path.join(d20, day)):
            if not (f.startswith("approaches-") and f.endswith(".csv")):
                continue
            sym = f[len("approaches-"):-4]
            if sym in jr.DROP_SET or jr.dropped_no_sigma(sym, day)[0] == 0 or day in sigma_days(jr, sym):
                continue
            fc = first_candle_ms(jr, sym)
            if fc is None:
                stop.append(f"{sym} {day}: нет свечей (сбой light)")
            elif d0 < fc + 240 * 60_000:
                drop[sym] = dt.datetime.fromtimestamp(fc / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
            else:
                stop.append(f"{sym} {day}: свечи с {dt.datetime.fromtimestamp(fc / 1000, dt.timezone.utc):%Y-%m-%d %H:%M}, "
                            "в сутках σ нет — дыра (сбой light)")
    return drop, stop


def setup(jr, mon):
    m = MONTHS.index(mon) + 1
    first = f"2026-{m:02d}-01"
    nxt = f"2026-{m + 1:02d}-01"
    home = os.path.join(jr.HOME, f"epochs/e-{mon}")
    jr.JUL_HOME = home
    jr.OUT_ROOT = os.path.join(jr.HOME, "tmp-p07/h1-read", mon)
    jr.JUL_DAYS = [f"2026-{m:02d}-{d:02d}" for d in range(1, calendar.monthrange(2026, m)[1] + 1)]
    jr.FORMS = [f for f in jr.FORMS if f[0] != "a-base"]
    jr.CELL_DIRS = sorted({c for _, c, _, _ in jr.FORMS})
    jr.PAIRS = []  # пары — в post_month (поправка 7 п. 4)
    remap = {"2026-08-01": first, "2026-09-24": nxt, "2026-09-01": nxt}

    def patch(kn):
        orig = kn.ms
        kn.ms = lambda day: orig(remap.get(day, day))
        return orig
    jr.patch_july = patch

    jr.GRID_PREFIX = mon  # grid-лог суток: серия `p07-h1-cells.py` — `<мес>-<сутки>`
    return home


def first_days(jr):
    """Монета -> первые сутки месяца, где у неё есть бинлог (пул месяца — поправка 7 п. 2)."""
    first = {}
    for day in jr.JUL_DAYS:
        root = os.path.join(jr.JUL_HOME, "study", f"root-{day}")
        if not os.path.isdir(root):
            continue
        suf = f"-{day}.binlog"
        for f in os.listdir(root):
            if f.endswith(suf):
                first.setdefault(f[:-len(suf)], day)
    return first


def cold_days(jr):
    """Сутки без довеска следующих (`--carry-root` не нашёл частей) — по grid-логу серии; 1-е число — без
    переноса с предыдущих по построению (архив месяца отдельной эпохой)."""
    out = [jr.JUL_DAYS[0]]
    for day in jr.JUL_DAYS:
        log = os.path.join(jr.GRID_LOGS, f"{jr.GRID_PREFIX}-{day}.grid.log")
        if os.path.exists(log) and "довесок" not in open(log, encoding="utf-8", errors="replace").read():
            out.append(day)
    return sorted(set(out))


PAIRS7 = [("b-base", "main"), ("b-k1", "b-base")]  # поправка 7 п. 4: описание, первая минус вторая
NOTES7 = [
    "пул выбран в сентябре (В-95): на январе — июне меньше монет (январь 54 против 73 в июле), монеты, снятые с "
    "торгов до сентября, в пул не попали — отбор выживших, вывод не сильнее",
    "суждение по точке на одном месяце, не статистика (§10)",
]


def closes_of(jr, name):
    with open(os.path.join(jr.OUT_ROOT, name, "ps-closes.json"), encoding="utf-8") as fh:
        c = json.load(fh)
    return sorted(tuple(x) for x in c.get("cell", {}).get("июль", {}).get("0", []))


def post_month(jr, mon, path, extra, first):
    """Дописать в json месяца сверх пути июля (поправка 7): $/сутки, пары, пул, холодный старт, строки п. 5."""
    pr = jr.load_mod("p07-read.py", "p07read")
    kn = pr.load_mod(pr.KN_PATH, "kn")
    orig_ms = jr.patch_july(kn)
    with open(path, encoding="utf-8") as fh:
        res = json.load(fh)
    closes = {}
    for name, *_ in jr.FORMS:
        closes[name] = closes_of(jr, name)
        res[name]["daily_usd"] = pr.daily_series(closes[name], jr.JUL_DAYS)[0]
        res[name].pop("vs_base", None)  # B1–B3 июля — не здесь: поправка 7 — денежных вердиктов нет
    pairs = {}
    for a, b in PAIRS7:
        ua, ub = res[a]["daily_usd"], res[b]["daily_usd"]
        fa = (res[a]["kpi"] or {}).get("frac_gt_5d")
        fb = (res[b]["kpi"] or {}).get("frac_gt_5d")
        rows = jr.paired_b2(kn, orig_ms, closes[a], closes[b])
        pairs[f"{a} - {b}"] = {
            "d_usd": pr.block_boot([x - y for x, y in zip(ua, ub)]),
            "d_frac": None if fa is None or fb is None else round(fa - fb, 3),
            "d_frac_without_day": [{"day": r["day"], "d": None if r["cell"] is None or r["base"] is None
                                    else round(r["cell"] - r["base"], 3)} for r in rows],
        }
    res["pairs"] = pairs
    pool = sorted(s for s in first if s not in jr.DROP_SET)
    res["_meta"].update({
        "protocol": "П-07 поправка 7", "month": mon, "drop_no_candles": extra,
        "pool_coins": len(pool), "pool_late": {s: d for s, d in first.items() if s in pool and d != jr.JUL_DAYS[0]},
        "cold_start_days": cold_days(jr), "notes": NOTES7,
        "hours_grid": f"{jr.JUL_DAYS[0]}..{len(jr.JUL_DAYS)} сут (все часы месяца)",
    })
    with open(path, "w", encoding="utf-8", newline="") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)


def half(out):
    """Полугодие (поправка 7 п. 3–4, п. 7): F по прочитанным месяцам у главного и базы Г-85б; месяц с GATE-STOP — вне F,
    по имени; «июль — исключение» только при F ≤ 1 и ≥ 5 прочитанных. Сумма $ по прочитанным суткам (блок ⌈n^⅓⌉ —
    6 при 181), месяцев в минусе."""
    jr = load_jul()
    pr = jr.load_mod("p07-read.py", "p07read")
    root = os.path.join(jr.HOME, "tmp-p07/h1-read")
    months, unread = {}, []
    for mon in MONTHS:
        p = os.path.join(root, f"{mon}.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                months[mon] = json.load(fh)
        elif os.path.exists(os.path.join(root, f"GATE-STOP-{mon}")):
            unread.append(mon)
        else:
            raise SystemExit(f"нет {p} и нет GATE-STOP-{mon} — месяц ещё не дочитан")
    read = [m for m in MONTHS if m in months]
    res = {"_meta": {"protocol": "П-07 поправка 7", "months_read": read, "months_gate_stop": unread, "notes": NOTES7}}
    for name in ("main", "b-base", "b-k1", "a-fr1"):
        units = [x for mon in read for x in months[mon][name]["daily_usd"]]
        states = {mon: (months[mon][name]["kpi"] or {}).get("state") for mon in read}
        month_usd = {mon: months[mon][name]["usd"]["est"] for mon in read}
        row = {"usd": pr.block_boot(units), "months_minus": sum(v < 0 for v in month_usd.values()),
               "month_usd": month_usd, "states": states}
        if name in ("main", "b-base"):
            f = sum(v == "не проходит" for v in states.values())
            row["F"] = f
            row["verdict"] = ("не держит вне выборки" if f >= 3 else
                              "июль — исключение" if f <= 1 and len(read) >= 5 else "не ясно")
        res[name] = row
    with open(out, "w", encoding="utf-8", newline="") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print(out)
    print("готово")


def main():
    args = sys.argv[1:]
    if "--half" in args:
        return half(args[args.index("--half") + 1])
    if "--month" not in args:
        raise SystemExit("нужен --month jan|feb|mar|apr|may|jun")
    i = args.index("--month")
    mon = args[i + 1]
    if mon not in MONTHS:
        raise SystemExit(f"месяц {mon}: только {MONTHS}")
    del args[i:i + 2]
    jr = load_jul()
    setup(jr, mon)
    first = first_days(jr)
    jr.POOL_FILTER = lambda pool, day, root: [x for x in pool if x in first and first[x] <= day]
    extra, stop = no_candle_coins(jr)
    if stop:
        print(f"{mon}: СТОП ворот — сбой подготовки σ (поправка 7 п. 7), не dc250dc: " + "; ".join(stop))
        sys.exit(1)
    if extra:
        jr.DROP = ",".join(sorted(jr.DROP_SET | set(extra)))
        jr.DROP_SET = set(jr.DROP.split(","))
        print(f"{mon}: рынка ещё не было (dc250dc) — вон на весь месяц: "
              + " ".join(f"{k} (первая свеча {v} UTC)" for k, v in sorted(extra.items())))
    sys.argv = [sys.argv[0]] + args
    try:
        jr.main()
    except SystemExit as e:
        if e.code not in (None, 0) or "--out" not in args:
            raise
    if "--out" in args:
        post_month(jr, mon, args[args.index("--out") + 1], extra, first)


if __name__ == "__main__":
    main()
