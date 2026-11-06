#!/usr/bin/env python3
"""TK-493 w3 (М-01/F-06, П-37 r3 §5 и §6.3): разностный ряд фандинга для D2 — d = r_Y − r_X на метках выплат ноги Y.

Зачем. `Rule::Funding` ядра решает вход и выход по трейлинг-среднему ставок ОДНОЙ ноги (Y), а в P&L платит обе ноги
(kernel.rs, `fund_y` и `fund_x`). Если ставки одного актива на двух биржах положительно коррелируют, вход «по ставке Y»
набирает позиции, где чистый фандинг около нуля. Протокол П-37 r3 требует вход и выход D2 по разности ставок. Ядро v3
умеет это без правки: подать разность как ставку ноги Y (`--funding-y`), а ноге X дать пустой каталог ставок
(`--funding-x`, нет файла у символа — нога без фандинга). Тогда каждая выплата считается ровно один раз — внутри разности.

Что делает. Для каждой пары (Y, X) читает `<Y>.csv.gz` из `--fund-y` и `<X>.csv.gz` из `--fund-x` (формат ядра:
`time_ms,rate,interval_h`, первая строка — заголовок; `rate` — доля номинала за выплату, `+` — лонги платят; единицы и знак
всех бирж сверены G7) и пишет `<Y>.csv.gz` в `OUT/shardN/` с теми же `time_ms` и `interval_h` ноги Y и ставкой d.

Правило привязки (без двойного счёта и без заглядывания вперёд). Минута выплаты — `funding_min(time_ms)` ядра
(E-010). Выплата X на минуте m относится к ПЕРВОЙ метке Y с минутой >= m, то есть к интервалу (метка_{k-1}, метка_k];
выплата X на минуте метки Y относится к этой метке. Следствия:
  * каждая выплата X попадает ровно в одну метку Y или в учтённый остаток («до первой метки», «после последней»);
  * разность не появляется раньше, чем произошла выплата X (трейлинг-среднее ядра не видит будущего);
  * Σd = Σr_Y − Σ(r_X, привязанные) тождественно — проверяется на каждой паре (допуск 1e-12), иначе код возврата 1.
Равные сетки (обе биржи платят на одних и тех же минутах): d_k = r_Y,k − r_X,k точно. Неравные сетки (например Y раз в
8 ч, X раз в час): в метку Y падает СУММА выплат X за интервал; сумма сохраняется, но момент начисления сдвинут не позже,
чем на один интервал Y; у пар с неравными сетками ряд d неравномерен (`grid_equal=0` в манифесте) — отчёт D2 печатает
их долю.

Остаток. Выплаты X до интервала первой метки Y (первый интервал — `interval_h` первой строки Y) и после последней метки Y
в ряд не входят (позиция Y не держится через метку, которой нет); они посчитаны в манифесте (`x_head`, `x_tail`).

Погрешность модели (второй порядок). Ядро платит `−qty·price(t)·rate` по каждой ноге. Разность на ноге Y даёт
−N_Y·(r_Y − r_X) вместо −(N_Y·r_Y + N_X·r_X); расхождение = −(N_X + N_Y)·r_X, где N_X + N_Y — разность номиналов ног,
то есть накопленный валовый ход спреда (десятки bps номинала) на ставку X (единицы bps): порядка 1e-3 bps на типичную
выплату. Меньше круга издержек 12,6 bps на три порядка.

Общий символ у нескольких партнёров. Ядро ищет ставки по имени панели Y, поэтому один файл `<Y>.csv.gz` может хранить
только одного партнёра X. Если Y входит в пары с несколькими X (контракты Bitget с суффиксом HKD, П-37 §4.2), пары
раскладываются по подкаталогам: shard0 — первый партнёр каждого Y, shard1 — второй и т. д.; список заданий шарда
(`shardN.jobs.csv`) содержит только его пары, и `statarb run` запускается на каждый шард отдельно.

Запуск (ядро; `--max-hold 0` — D2 без выхода по сроку, П-37 r3):
  python3 d2_diff_funding.py --pairs pairs.csv --fund-y ~/xdl9/funding/binance --fund-x ~/xdl9/funding/bybit --out ~/xdl9/d2/binance_bybit [--from-day 2479 --to-day 2500]
  statarb run --kind funding-pp --panels PY --panels-b PX --funding-y OUT/shard0 --funding-x OUT/empty_x \
      --rule funding --funding-k K --hold-payments H --max-hold 0 --jobs OUT/shard0.jobs.csv ...
В журнале ядра должна быть строка «фандинг: ног со ставками Y n/n, X 0/n» — это штатный вид режима (X без своих ставок).

G7-совместимость. Формат, единицы и знак — те же, что у файлов, прошедших G7 (§3.3 отчёта ворот); файл разности читают
`check_run_ext.py` и `g7_hand.py` без правок с `--funding-y OUT/shardN --funding-x OUT/empty_x`; сделку D2 руками сверяют
как раньше: начисление −dir·qty·price·d за метки Y строго между входом и выходом. Строки Binance TradFi с «лишней»
отрицательной ставкой (G7 §3.5, решает CEO) скрипт НЕ чистит: что лежит во входе, то и идёт в разность.

Вход пар: csv с колонками `y,x` (имена файлов без `.csv.gz`, т. е. имена панелей); лишние колонки (ex_y, ex_x) игнорируются.
Только стандартная библиотека (на Deck нет numpy).
"""
import argparse
import bisect
import csv
import gzip
import math
import os
import sys

GRID0_MS = 1_577_836_800_000  # 2020-01-01 00:00 UTC, начало сетки панелей (cli::GRID0_MS)
MINUTE_MS = 60_000
MASS_TOL = 1e-12

MANIFEST_COLS = [
    "shard", "y", "x", "status", "n_y", "n_x", "x_attached", "x_head", "x_tail", "grid_equal",
    "sum_y", "sum_x", "sum_x_attached", "sum_d", "mass_err",
]


def funding_min(time_ms):
    """Минута сетки панелей для времени выплаты (как cli::funding_min: floor-деление)."""
    return (time_ms - GRID0_MS) // MINUTE_MS


def read_funding(path):
    """`time_ms,rate,interval_h` (первая строка — заголовок) -> список (time_ms:int, rate:float, interval_txt:str, time_txt:str)."""
    rows = []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        rd = csv.reader(fh)
        next(rd, None)
        for r in rd:
            if len(r) < 2:
                continue
            rows.append((int(r[0]), float(r[1]), r[2] if len(r) > 2 else "", r[0]))
    rows.sort(key=lambda z: z[0])
    return rows


def diff_series(ry, rx):
    """Разность d_k = r_Y,k − Σ(r_X, привязанные к метке k). Возвращает (rows, stat).

    rows — [(time_txt, d, interval_txt)] по меткам Y (тот же порядок и те же time_ms/interval_h, что у Y);
    stat — словарь счётчиков и сумм для манифеста.
    """
    my = [funding_min(t) for t, _, _, _ in ry]
    n = len(ry)
    attach = [[] for _ in range(n)]     # индексы строк X, привязанных к метке k
    head = tail = 0
    sum_head = sum_tail = 0.0
    first_interval_min = None   # интервал перед первой меткой: interval_h первой строки Y, в минутах; нет значения — голова не отсекается
    if n:
        try:
            first_interval_min = int(round(float(ry[0][2]) * 60))
        except ValueError:
            pass
    for j, (tx, rate_x, _, _) in enumerate(rx):
        mx = funding_min(tx)
        if n == 0:
            tail += 1
            sum_tail += rate_x
            continue
        k = bisect.bisect_left(my, mx)
        if k == n:
            tail += 1
            sum_tail += rate_x
        elif k == 0 and first_interval_min is not None and mx <= my[0] - first_interval_min:
            head += 1
            sum_head += rate_x
        else:
            attach[k].append(j)
    rows = []
    exact = 0
    for k, (ty, rate_y, itv, ttxt) in enumerate(ry):
        sx = math.fsum(rx[j][1] for j in attach[k])
        rows.append((ttxt, rate_y - sx, itv))
        if len(attach[k]) == 1 and funding_min(rx[attach[k][0]][0]) == my[k]:
            exact += 1
    sum_y = math.fsum(r[1] for r in ry)
    sum_x = math.fsum(r[1] for r in rx)
    sum_att = math.fsum(rx[j][1] for a in attach for j in a)
    sum_d = math.fsum(r[1] for r in rows)
    mass_err = max(abs(sum_d - (sum_y - sum_att)), abs(sum_x - (sum_att + sum_head + sum_tail)))
    return rows, {
        "n_y": n, "n_x": len(rx), "x_attached": sum(len(a) for a in attach), "x_head": head, "x_tail": tail,
        "grid_equal": int(n > 0 and exact == n),
        "sum_y": sum_y, "sum_x": sum_x, "sum_x_attached": sum_att, "sum_d": sum_d, "mass_err": mass_err,
    }


def write_funding(path, rows):
    tmp = path + ".part"
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as fh:
        fh.write("time_ms,rate,interval_h\n")
        for ttxt, d, itv in rows:
            fh.write("%s,%s,%s\n" % (ttxt, repr(d), itv))
    os.replace(tmp, path)


def read_pairs(path):
    with open(path, encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh)
        if "y" not in (rd.fieldnames or []) or "x" not in rd.fieldnames:
            raise SystemExit("в %s нужны колонки y,x" % path)
        return [(r["y"].strip(), r["x"].strip()) for r in rd if r["y"].strip() and r["x"].strip()]


def run(pairs, fund_y, fund_x, out, from_day=None, to_day=None):
    """Пишет шарды и манифест; возвращает (манифест — список словарей, код возврата)."""
    os.makedirs(out, exist_ok=True)
    os.makedirs(os.path.join(out, "empty_x"), exist_ok=True)   # --funding-x режима: у символов X нет своих ставок
    seen = {}
    cache = {}
    man = []
    shard_jobs = {}
    rc = 0
    for y, x in pairs:
        shard = seen.get(y, 0)
        seen[y] = shard + 1
        py = os.path.join(fund_y, y + ".csv.gz")
        px = os.path.join(fund_x, x + ".csv.gz")
        rec = {c: "" for c in MANIFEST_COLS}
        rec.update({"shard": shard, "y": y, "x": x})
        if not os.path.exists(py) or not os.path.exists(px):
            rec["status"] = "нет файла ставок: " + ("Y" if not os.path.exists(py) else "") + ("X" if not os.path.exists(px) else "")
            man.append(rec)
            continue
        for p in (py, px):
            if p not in cache:
                cache[p] = read_funding(p)
        rows, st = diff_series(cache[py], cache[px])
        sd = os.path.join(out, "shard%d" % shard)
        os.makedirs(sd, exist_ok=True)
        write_funding(os.path.join(sd, y + ".csv.gz"), rows)
        rec.update(st)
        rec["status"] = "ok" if st["mass_err"] <= MASS_TOL else "МАССА НЕ СОШЛАСЬ"
        if st["mass_err"] > MASS_TOL:
            rc = 1
        man.append(rec)
        shard_jobs.setdefault(shard, []).append((y, x))
    if from_day is not None and to_day is not None:
        for shard, prs in shard_jobs.items():
            with open(os.path.join(out, "shard%d.jobs.csv" % shard), "w", encoding="utf-8", newline="\n") as fh:
                fh.write("y,x,from_day,to_day\n")
                for y, x in prs:
                    fh.write("%s,%s,%d,%d\n" % (y, x, from_day, to_day))
    with open(os.path.join(out, "manifest.csv"), "w", encoding="utf-8", newline="\n") as fh:
        w = csv.writer(fh)
        w.writerow(MANIFEST_COLS)
        for rec in man:
            w.writerow([rec[c] if not isinstance(rec[c], float) else repr(rec[c]) for c in MANIFEST_COLS])
    return man, rc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pairs", required=True, help="csv с колонками y,x (имена панелей без .csv.gz)")
    ap.add_argument("--fund-y", required=True, help="каталог ставок биржи ноги Y (<Y>.csv.gz)")
    ap.add_argument("--fund-x", required=True, help="каталог ставок биржи ноги X (<X>.csv.gz)")
    ap.add_argument("--out", required=True, help="выходной каталог: shardN/, empty_x/, manifest.csv")
    ap.add_argument("--from-day", type=int, help="пишет shardN.jobs.csv (y,x,from_day,to_day), если заданы оба")
    ap.add_argument("--to-day", type=int)
    a = ap.parse_args(argv)
    man, rc = run(read_pairs(a.pairs), os.path.expanduser(a.fund_y), os.path.expanduser(a.fund_x),
                  os.path.expanduser(a.out), a.from_day, a.to_day)
    ok = [m for m in man if m["status"] == "ok"]
    ge = sum(1 for m in ok if m["grid_equal"] == 1)
    print("пар %d, записано %d, равные сетки %d, неравные %d, без ставок %d; макс. ошибка массы %.3e; остаток X: голова %d, хвост %d"
          % (len(man), len(ok), ge, len(ok) - ge, len(man) - len(ok), max([m["mass_err"] for m in ok] or [0.0]),
             sum(m["x_head"] for m in ok), sum(m["x_tail"] for m in ok)))
    return rc


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.exit(main())
