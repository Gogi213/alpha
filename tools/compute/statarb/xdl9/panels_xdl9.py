#!/usr/bin/env python3
"""TK-493 F4 (G3): тонкий адаптер выгрузок xdl (8 минутных бирж) к конвертеру панелей и ядру statarb — python 3, stdlib.

Формат xdl совместим с ядром без пересборки данных, поэтому адаптер ничего не переписывает, а готовит каталоги и проверяет:
  свечи   `candles/<SYM>.csv.gz`: `t_open_ms,o,h,l,c,v,quote_v` (CRLF) — `statarb convert` берёт первые 5 полей и 6-е (v):
          v = 0 и дыры → NaN (src/statarb/panel.rs, convert_csv_stats); заголовок пропускается на первой строке;
  фандинг `funding/<SYM>.csv.gz`: `time_ms,rate,interval_h` — `read_funding(dir, <SYM>)` из cli.rs читает `<dir>/<SYM>.csv.gz`,
          пропускает ПЕРВУЮ строку безусловно, парсит поля 0 и 1 как i64 и f64 без обрезки пробелов; первая же ошибка
          разбора обрывает загрузку всего набора, поэтому каждый файл проверяется по той же грамматике заранее.
  farm           симлинки <work>/src/<биржа>/<SYM>.csv.gz и <work>/funding/<биржа>/<SYM>.csv.gz → сырьё (0-байтные пропускаются)
  funding-check  разбор каждого файла фандинга по грамматике Rust + порядок/дубли минут + нефинитные ставки
  manifest       панели ↔ instruments.csv ↔ сверка со свечами ↔ фандинг → manifest.csv, сводки по биржам и классам, отказы
  selftest       проверка грамматики разбора на крайних случаях
Сырьё не изменяется. Классы — из instruments.csv как есть; укрупнение — по П-37 §4.1 (строки class_map_* в P-37-params)."""
import argparse
import csv
import datetime as dt
import gzip
import os
import re
import sys
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # tools/compute/statarb
import panels_manifest as pm  # noqa: E402
sys.path.insert(0, HERE)
import asset_class as AC  # noqa: E402  единая функция классификации (D-01, П-37 r3 §4.1): та же, что в asset_lib.py

GRID0_MS = 1_577_836_800_000
MINUTE_MS = 60_000
EXCHANGES = ["binance", "bybit", "okx", "bingx", "bitget", "kraken", "aster", "lighter"]
FUND_HEADER = "time_ms,rate,interval_h"
I64_MIN, I64_MAX = -(2**63), 2**63 - 1
# Грамматики Rust: i64::from_str и f64::from_str (без пробелов, без «_», регистр inf/nan не важен).
RE_I64 = re.compile(r"[+-]?[0-9]+\Z")
RE_F64 = re.compile(r"[+-]?(?:inf|infinity|nan|(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:e[+-]?[0-9]+)?)\Z", re.I)
# Укрупнение классов - asset_class.BIG (П-37 §4.1 / P-37-params class_map_*); other, пред-листинг, приватные базы, датированные - вне теста.
P37_CLASS = AC.BIG
OUT_OF_TEST = AC.OUT_OF_TEST
WINDOW_END_MS = 1_793_836_800_000  # 2026-11-05 00:00 UTC, конец окна данных (П-37 §3)


def utc(min_idx):
    t = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=int(min_idx))
    return t.strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- фандинг: грамматика parse_funding
def parse_funding_text(text):
    """Повтор cli.rs::parse_funding: → (строк с данными, ошибка|None, нефинитных, дублей минут, нарушений порядка, пропущенных коротких, заголовок_ок, первая_мин, последняя_мин)."""
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()  # str::lines не отдаёт хвостовую пустую строку
    lines = [p[:-1] if p.endswith("\r") else p for p in parts]  # str::lines снимает «\r» только перед «\n»
    header_ok = bool(lines) and lines[0] == FUND_HEADER
    rows = err_n = nonfinite = dup = unord = short = 0
    first_err = ""
    prev = None
    first_min = last_min = None
    for i, line in enumerate(lines[1:], start=2):
        f = line.split(",")
        if len(f) < 2:
            short += 1
            continue
        t, r = f[0], f[1]
        if not RE_I64.match(t) or not I64_MIN <= int(t) <= I64_MAX:
            err_n += 1
            first_err = first_err or "строка %d: время не i64: %r" % (i, t)
            continue
        if not RE_F64.match(r):
            err_n += 1
            first_err = first_err or "строка %d: ставка не f64: %r" % (i, r)
            continue
        rows += 1
        m = (int(t) - GRID0_MS) // MINUTE_MS  # div_euclid
        if float(r) in (float("inf"), float("-inf")) or float(r) != float(r):
            nonfinite += 1
        if prev is not None:
            if m == prev:
                dup += 1
            elif m < prev:
                unord += 1
        prev = m
        first_min = m if first_min is None else first_min
        last_min = m
    return rows, (first_err or None), err_n, nonfinite, dup, unord, short, header_ok, first_min, last_min


def check_funding_file(job):
    ex, sym, path = job
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="") as f:
            text = f.read()
    except Exception as e:  # noqa: BLE001 — любая ошибка чтения = причина, а не падение
        return [ex, sym, 0, 0, 1, "чтение: %s" % e, 0, 0, 0, 0, "", ""]
    rows, ferr, err_n, nonfinite, dup, unord, short, header_ok, fmin, lmin = parse_funding_text(text)
    return [ex, sym, rows, int(header_ok), err_n, ferr or "", nonfinite, dup, unord, short,
            "" if fmin is None else utc(fmin), "" if lmin is None else utc(lmin)]


FUND_COLS = ["exchange", "symbol", "rows", "header_ok", "errors", "first_error", "nonfinite", "dup_minutes",
             "unordered", "short_lines", "first_utc", "last_utc"]


def cmd_funding_check(a):
    jobs = []
    for ex in a.exchanges:
        d = os.path.join(a.work, "funding", ex)
        for name in sorted(os.listdir(d)):
            if name.endswith(pm.CSV_GZ):
                jobs.append((ex, name[: -len(pm.CSV_GZ)], os.path.join(d, name)))
    with Pool(a.procs) as p:
        res = p.map(check_funding_file, jobs, chunksize=16)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(FUND_COLS)
        w.writerows(res)
    bad = 0
    for ex in a.exchanges:
        r = [x for x in res if x[0] == ex]
        e = sum(1 for x in r if x[4] or x[6] or x[7] or x[8] or not x[3] and x[2])
        bad += e
        print("funding-check %-8s файлов %4d, строк %8d, без заголовка %d, ошибок разбора %d (файлов %d), нефинитных %d, "
              "дублей минут %d, нарушений порядка %d, коротких строк %d"
              % (ex, len(r), sum(x[2] for x in r), sum(1 for x in r if not x[3] and x[2]), sum(x[4] for x in r),
                 sum(1 for x in r if x[4]), sum(x[6] for x in r), sum(x[7] for x in r), sum(x[8] for x in r),
                 sum(x[9] for x in r)))
    print("funding-check: файлов с замечанием %d → %s" % (bad, a.out))
    return 1 if bad else 0


# ---------------------------------------------------------------- farm
def cmd_farm(a):
    for ex in a.exchanges:
        for sub, outsub in (("candles", "src"), ("funding", "funding")):
            d = os.path.join(a.root, ex, sub)
            names = [n for n in os.listdir(d) if n.endswith(pm.CSV_GZ)]
            zero = [n for n in names if os.path.getsize(os.path.join(d, n)) == 0]
            linked, _ = pm.make_farm(sorted(pm.candle_files(d)), d, os.path.join(a.work, outsub, ex))
            print("farm %-8s %-8s файлов %4d, ссылок %4d, пустых (0 байт) %d" % (ex, sub, len(names), len(linked), len(zero)))
    return 0


# ---------------------------------------------------------------- manifest
def read_instruments(root, ex):
    with open(os.path.join(root, ex, "instruments.csv"), encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def p37_class(row, private_bases=None, resolution=None):
    """Укрупнённый класс П-37 §4.1 -> (класс, пред-листинг/приватная база 0|1, датированный 0|1): ровно asset_class.classify,
    та же функция, что в asset_lib.py (D-01). Столбец манифеста `pre_ipo` = пред-листинг по первичным полям или приватная база."""
    c = AC.classify(row, private_bases, resolution)
    return c.big, c.pre, c.dated


def load_private_bases(root):
    """Приватные базы по instruments.csv ВСЕХ бирж корня выгрузки (в том числе Hyperliquid, если он там есть)."""
    rows = []
    for ex in sorted(os.listdir(root)):
        p = os.path.join(root, ex, "instruments.csv")
        if os.path.isfile(p):
            rows.extend(read_csv(p))
    return AC.derive_private_bases(rows)


def refusal_detail(r):
    """Что известно по полям instruments.csv о символе без файла свечей (не причина закачки, а признаки в списке биржи)."""
    note = r.get("note", "")
    if r.get("listed_ms") and int(r["listed_ms"]) >= WINDOW_END_MS:
        return "листинг после конца окна"
    if "dated future" in note:
        return "датированный фьючерс, не перпетуал (не качали)"
    if note.startswith("spot"):
        return "спот-рынок (не качали)"
    return "статус %s; причина по полям списка не определяется" % (r.get("status") or "?")


def timing(logdir, ex):
    """Время convert: с — из строки Rust, wall — из .time (секунды оболочки)."""
    s = {"convert_threads": "", "convert_s": "", "convert_wall_s": "", "convert_rc": "", "convert_errors": "",
         "convert_rows": "", "convert_zero": "", "convert_minutes": "", "convert_panels": "", "convert_empty": ""}
    p = os.path.join(logdir, "convert-%s.log" % ex)
    if os.path.exists(p):
        m = re.search(r"convert: файлов (\d+) \(панелей (\d+), пустых (\d+), ошибок (\d+)\), строк csv (\d+), с объёмом 0 → NaN (\d+), "
                      r"минут в панелях (\d+), ([\d.]+) с", open(p, encoding="utf-8").read())
        if m:
            s.update(convert_panels=m.group(2), convert_empty=m.group(3), convert_errors=m.group(4), convert_rows=m.group(5),
                     convert_zero=m.group(6), convert_minutes=m.group(7), convert_s=m.group(8))
    p = os.path.join(logdir, "convert-%s.time" % ex)
    if os.path.exists(p):
        rc, t0, t1, th = open(p).read().split()
        s.update(convert_rc=rc, convert_wall_s="%.1f" % (float(t1) - float(t0)), convert_threads=th)
    return s


MAN_COLS = ["exchange", "symbol", "base", "quote", "class", "class_src", "p37_class", "pre_ipo", "dated", "status",
            "listed_ms", "delisted_ms", "candle_file", "csv_rows", "zero_volume", "panel", "panel_first_utc",
            "panel_last_utc", "panel_minutes", "panel_valid", "panel_nan", "panel_bytes", "verify_diffs", "refusal",
            "funding_file", "funding_rows", "funding_errors", "funding_nonfinite", "funding_dup_min", "funding_unordered"]


def cmd_manifest(a):
    sweep = {(r["exchange"], r["symbol"]): r for r in read_csv(a.sweep)}
    pbases = load_private_bases(a.root)
    resol = AC.load_resolution()
    fund = {(r["exchange"], r["symbol"]): r for r in read_csv(a.funding_check)}
    out_dir = os.path.join(a.work, "panels")
    man, summ_ex, summ_cl, refusals = [], [], {}, []
    for ex in a.exchanges:
        inst = read_instruments(a.root, ex)
        cdir = os.path.join(a.root, ex, "candles")
        files = {n[: -len(pm.CSV_GZ)]: os.path.getsize(os.path.join(cdir, n)) for n in os.listdir(cdir) if n.endswith(pm.CSV_GZ)}
        fdir = os.path.join(a.root, ex, "funding")
        ffiles = {n[: -len(pm.CSV_GZ)] for n in os.listdir(fdir) if n.endswith(pm.CSV_GZ)}
        known = {r["symbol"] for r in inst}
        rows = list(inst) + [{"symbol": s, "base": "", "quote": "", "class": "", "class_src": "", "status": "",
                              "listed_ms": "", "delisted_ms": "", "note": "нет в instruments.csv"} for s in sorted(set(files) - known)]
        acc = dict(instruments=len(inst), candle_files=len(files), panels=0, bytes=0, no_file=0, header_only=0, all_zero=0,
                   convert_fail=0, orphans=len(set(files) - known), csv_rows=0, zero=0, minutes=0, nan=0, diffs=0)
        for r in rows:
            sym = r["symbol"]
            cls, pre, dated = p37_class(r, pbases, resol) if r["class"] else (OUT_OF_TEST, 0, 0)
            sw = sweep.get((ex, sym))
            pth = os.path.join(a.work, "panels", ex, sym + ".sap")
            has_panel = os.path.exists(pth)
            first = last = n = nvalid = nnan = size = ""
            if has_panel:
                f0, n = pm.read_header(pth)
                size = pm.HEADER + pm.BYTES_PER_MIN * n
                first, last = utc(f0), utc(f0 + n - 1)
                nnan = sw["panel_nan"] if sw else ""
                nvalid = n - int(nnan) if nnan != "" else ""
                acc["panels"] += 1
                acc["bytes"] += size
                acc["minutes"] += n
                acc["nan"] += int(nnan or 0)
            refusal = ""
            if not has_panel:
                if sym not in files:
                    refusal = "нет файла свечей в выгрузке"
                    acc["no_file"] += 1
                elif sw and int(sw["csv_rows"]) == 0:
                    refusal = "файл без строк данных (только заголовок)"
                    acc["header_only"] += 1
                elif sw and int(sw["csv_rows"]) == int(sw["zero_volume"]):
                    refusal = "все строки с объёмом 0"
                    acc["all_zero"] += 1
                else:
                    refusal = "ОШИБКА: есть строки с объёмом ≠ 0, панели нет"
                    acc["convert_fail"] += 1
            if sw:
                acc["csv_rows"] += int(sw["csv_rows"])
                acc["zero"] += int(sw["zero_volume"])
                acc["diffs"] += int(sw["diffs"])
            fr = fund.get((ex, sym))
            m = [ex, sym, r["base"], r["quote"], r["class"], r["class_src"], cls, pre, dated, r["status"], r["listed_ms"],
                 r["delisted_ms"], int(sym in files), sw["csv_rows"] if sw else "", sw["zero_volume"] if sw else "",
                 int(has_panel), first, last, n, nvalid, nnan, size, sw["diffs"] if sw else "", refusal,
                 int(sym in ffiles), fr["rows"] if fr else "", fr["errors"] if fr else "", fr["nonfinite"] if fr else "",
                 fr["dup_minutes"] if fr else "", fr["unordered"] if fr else ""]
            man.append(m)
            if refusal:
                detail = refusal_detail(r) if refusal.startswith("нет файла") else ""
                refusals.append([ex, sym, r["class"], r["status"], r["listed_ms"], r["delisted_ms"], refusal, detail,
                                 r.get("note", "")[:120]])
            k = (ex, r["class"] or "(нет в instruments)", cls)
            c = summ_cl.setdefault(k, [0, 0, 0, 0, 0])
            c[0] += 1
            c[1] += int(has_panel)
            c[2] += int(nvalid or 0) if has_panel else 0
            c[3] += int(nnan or 0) if has_panel else 0
            c[4] += int(n or 0) if has_panel else 0
        t = timing(os.path.join(a.work, "log"), ex)
        summ_ex.append([ex, acc["instruments"], acc["candle_files"], acc["panels"], acc["bytes"], acc["no_file"],
                        acc["header_only"], acc["all_zero"], acc["convert_fail"], acc["orphans"], acc["csv_rows"], acc["zero"],
                        acc["minutes"], acc["nan"], acc["diffs"], t["convert_threads"], t["convert_s"], t["convert_wall_s"],
                        t["convert_rc"], t["convert_errors"], t["convert_rows"], t["convert_zero"], t["convert_minutes"],
                        t["convert_panels"], t["convert_empty"]])
    os.makedirs(out_dir, exist_ok=True)

    def dump(name, cols, data):
        with open(os.path.join(out_dir, name), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(cols)
            w.writerows(data)

    dump("manifest.csv", MAN_COLS, man)
    dump("summary_exchange.csv",
         ["exchange", "instruments", "candle_files", "panels", "panel_bytes", "no_candle_file", "header_only", "all_zero_volume",
          "convert_failed", "orphan_files", "csv_rows", "zero_volume_rows", "panel_minutes", "panel_nan", "verify_diffs",
          "threads", "convert_s_rust", "convert_wall_s", "convert_rc", "rust_errors", "rust_rows", "rust_zero", "rust_minutes",
          "rust_panels", "rust_empty"], summ_ex)
    dump("summary_class.csv", ["exchange", "class", "p37_class", "instruments", "panels", "panel_valid_minutes", "panel_nan",
                               "panel_minutes"],
         [[k[0], k[1], k[2]] + v[:2] + v[2:5] for k, v in sorted(summ_cl.items())])
    dump("refusals.csv", ["exchange", "symbol", "class", "status", "listed_ms", "delisted_ms", "reason", "detail", "note"], refusals)
    dump("symbol_classes.csv", ["exchange", "symbol", "class", "p37_class", "pre_ipo", "dated", "status", "panel",
                                "panel_minutes", "panel_nan"],
         [[m[0], m[1], m[4], m[6], m[7], m[8], m[9], m[15], m[18], m[20]] for m in man])
    print("manifest: строк %d, панелей %d, отказов %d → %s" % (len(man), sum(r[3] for r in summ_ex), len(refusals), out_dir))
    return 0


# ---------------------------------------------------------------- selftest
def cmd_selftest(_a):
    ok = 0

    def eq(got, want, what):
        nonlocal ok
        if got != want:
            print("selftest ОШИБКА %s: %r ≠ %r" % (what, got, want))
            sys.exit(1)
        ok += 1

    good = FUND_HEADER + "\r\n1790812800001,0.00005000,4\r\n1790827200000,-1.2e-05,4\r\n"
    r = parse_funding_text(good)
    eq((r[0], r[1], r[2], r[3], r[4], r[5], r[7]), (2, None, 0, 0, 0, 0, True), "норма")
    eq(utc(r[8]), "2026-10-01 00:00", "минута 1-й выплаты (+1 мс → вниз)")
    eq(parse_funding_text(FUND_HEADER + "\n1,nan,8\n2,inf,8\n")[3], 2, "нефинитные")
    eq(parse_funding_text(FUND_HEADER + "\n5,1,8\n5,1,8\n")[4], 1, "дубль минуты")
    eq(parse_funding_text(FUND_HEADER + "\n" + str(GRID0_MS + 120_000) + ",1,8\n" + str(GRID0_MS) + ",1,8\n")[5], 1, "порядок")
    eq(parse_funding_text(FUND_HEADER + "\n 5,1,8\n")[2], 1, "пробел в времени — ошибка Rust")
    eq(parse_funding_text(FUND_HEADER + "\n5, 1,8\n")[2], 1, "пробел в ставке — ошибка Rust")
    eq(parse_funding_text(FUND_HEADER + "\n5,1_0,8\n")[2], 1, "подчёркивание — ошибка Rust")
    eq(parse_funding_text(FUND_HEADER + "\n5,,8\n")[2], 1, "пустая ставка")
    eq(parse_funding_text(FUND_HEADER + "\n1.5,1,8\n")[2], 1, "дробное время")
    eq(parse_funding_text(FUND_HEADER + "\n5,.5,8\n5,5.,8\n5,1E-5,8\n")[2], 0, "грамматика f64: .5 5. 1E-5 допустимы")
    eq(parse_funding_text(FUND_HEADER + "\n\n7\n")[6], 2, "пустая и короткая строки пропускаются")
    eq(parse_funding_text("1,1,8\n2,1,8\n")[0], 1, "без заголовка первая строка теряется (ядро пропускает её безусловно)")
    eq(parse_funding_text("1,1,8\n")[7], False, "заголовок не найден")
    print("selftest: %d проверок ок" % ok)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def ex_arg(q):
        q.add_argument("--exchanges", type=lambda s: s.split(","), default=EXCHANGES)

    f = sub.add_parser("farm")
    f.add_argument("--root", required=True)
    f.add_argument("--work", required=True)
    ex_arg(f)
    f.set_defaults(fn=cmd_farm)
    c = sub.add_parser("funding-check")
    c.add_argument("--work", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--procs", type=int, default=8)
    ex_arg(c)
    c.set_defaults(fn=cmd_funding_check)
    m = sub.add_parser("manifest")
    m.add_argument("--root", required=True)
    m.add_argument("--work", required=True)
    m.add_argument("--sweep", required=True)
    m.add_argument("--funding-check", required=True)
    ex_arg(m)
    m.set_defaults(fn=cmd_manifest)
    s = sub.add_parser("selftest")
    s.set_defaults(fn=cmd_selftest)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
