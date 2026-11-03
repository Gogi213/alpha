#!/usr/bin/env python3
"""TK-487: панели .sap по вселенной П-32 — каталог символов, оценка места, манифест панелей (чистый python, без numpy).

  farm      --union CSV --candles DIR --out DIR [--missing-out CSV]   каталог симлинков <SYM>.csv.gz → свечи
            (символы union-<биржа>.csv; --all-files — все непустые файлы каталога, для спота без таблицы вселенной);
            сырьё не трогается, только ссылки.
  estimate  --pair UNION[,SOURCES] [--pair …] [--full N] --free-path PATH [--frac 0.8] [--end-day ГГГГ-ММ-ДД]
            оценка места сверху: 24 + 16·минут на панель (спан sources first_day..last_day × 1440; без SOURCES и для
            --full N символов — весь спан 2020-01-01..end-day); код 3, если сумма > frac · свободного.
  manifest  --panels ex=DIR [--panels ex=DIR …] --out CSV   список панелей: exchange,symbol,first_day,last_day,minutes,bytes
            (читает только заголовки .sap, 24 байта на файл; порядок по бирже и символу).
Формат .sap — src/statarb/panel.rs: SAP1, u32 резерв, i64 первая минута от 2020-01-01 UTC, u64 минут, open[n], close[n] (f64 LE)."""
import argparse
import csv
import datetime as dt
import os
import struct
import sys

GRID0 = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
HEADER = 24
MAGIC = b"SAP1"
BYTES_PER_MIN = 16
CSV_GZ = ".csv.gz"


def read_header(path):
    """(первая минута от GRID0, число минут) из заголовка .sap; размер файла сверяется с 24 + 16·n."""
    size = os.path.getsize(path)
    if size < HEADER:
        raise ValueError("%s: %d байт — короче заголовка %d" % (path, size, HEADER))
    with open(path, "rb") as f:
        h = f.read(HEADER)
    if h[:4] != MAGIC:
        raise ValueError("%s: не панель SAP1" % path)
    first_min, n = struct.unpack("<qQ", h[8:24])
    if size != HEADER + BYTES_PER_MIN * n:
        raise ValueError("%s: размер %d ≠ 24 + 16·%d" % (path, size, n))
    return first_min, n


def minute_day(m):
    return (GRID0 + dt.timedelta(minutes=m)).date().isoformat()


def manifest_rows(exchange, panels_dir):
    """Строки манифеста по каталогу панелей; пустая панель (n = 0) — в строке с minutes = 0, не пропускается."""
    rows = []
    for name in sorted(os.listdir(panels_dir)):
        if not name.endswith(".sap"):
            continue
        first, n = read_header(os.path.join(panels_dir, name))
        if n == 0:
            rows.append([exchange, name[:-4], "", "", 0, HEADER])
            continue
        rows.append([exchange, name[:-4], minute_day(first), minute_day(first + n - 1), n, HEADER + BYTES_PER_MIN * n])
    return rows


def write_manifest(panels, out):
    """panels: [(биржа, каталог)] → CSV; возвращает {биржа: число панелей}."""
    counts = {}
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["exchange", "symbol", "first_day", "last_day", "minutes", "bytes"])
        for ex, d in panels:
            rows = manifest_rows(ex, d)
            counts[ex] = len(rows)
            w.writerows(rows)
    return counts


def union_symbols(path):
    with open(path, encoding="utf-8", newline="") as f:
        return [r["symbol"].strip() for r in csv.DictReader(f) if r["symbol"].strip()]


def candle_files(candles_dir):
    """{символ: путь} непустых `<SYM>.csv.gz`; пустой (0 байт) файл — не свеча. Header-only gz остаётся: его
    «пустоту» определяет convert (панель не пишется), а не размер файла."""
    out = {}
    for name in sorted(os.listdir(candles_dir)):
        p = os.path.join(candles_dir, name)
        if name.endswith(CSV_GZ) and os.path.getsize(p) > 0:
            out[name[: -len(CSV_GZ)]] = p
    return out


def make_farm(symbols, candles_dir, out_dir):
    """Симлинки <SYM>.csv.gz → свечи для символов, у которых файл есть; возвращает (связано, нет файла)."""
    files = candle_files(candles_dir)
    os.makedirs(out_dir, exist_ok=True)
    linked, missing = [], []
    for s in symbols:
        if s not in files:
            missing.append(s)
            continue
        dst = os.path.join(out_dir, s + CSV_GZ)
        if os.path.lexists(dst):
            os.remove(dst)  # только ссылка нашей же фермы; сырьё — цель ссылки — не затрагивается
        os.symlink(files[s], dst)
        linked.append(s)
    return linked, missing


def span_days(first_day, last_day):
    a = dt.date.fromisoformat(first_day)
    b = dt.date.fromisoformat(last_day)
    return max(0, (b - a).days + 1)


def estimate_bytes(symbols, sources_csv, end_day, start_day="2020-01-01"):
    """Верхняя оценка суммы размеров панелей: 24 + 16·1440·дней спана на символ. Нет строки в sources (или sources
    не задан) — спан целиком от start_day до end_day."""
    spans = {}
    if sources_csv:
        with open(sources_csv, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if int(r["days_with_candle"] or 0) > 0:
                    spans[r["symbol"].strip()] = span_days(r["first_day"], r["last_day"])
    full = span_days(start_day, end_day)
    total = 0
    for s in symbols:
        days = spans.get(s, full if not sources_csv else 0)
        if days:
            total += HEADER + BYTES_PER_MIN * 1440 * days
    return total


def free_bytes(path):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def cmd_farm(a):
    if a.all_files:
        syms = sorted(candle_files(a.candles))
    else:
        syms = union_symbols(a.union)
    linked, missing = make_farm(syms, a.candles, a.out)
    if a.missing_out:
        with open(a.missing_out, "w", encoding="utf-8") as f:
            f.write("".join(s + "\n" for s in missing))
    print("farm: символов %d, ссылок %d, без файла свечей %d" % (len(syms), len(linked), len(missing)))
    return 0


def cmd_estimate(a):
    est = a.full * estimate_bytes(["_"], None, a.end_day)
    for pair in a.pair:
        union, _, sources = pair.partition(",")
        est += estimate_bytes(union_symbols(union), sources or None, a.end_day)
    free = free_bytes(a.free_path)
    ok = est <= a.frac * free
    print("estimate: оценка сверху %.1f ГБ, свободно %.1f ГБ, порог %.0f%% → %s"
          % (est / 1e9, free / 1e9, a.frac * 100, "можно" if ok else "ОТКАЗ"))
    return 0 if ok else 3


def cmd_manifest(a):
    panels = []
    for p in a.panels:
        ex, _, d = p.partition("=")
        if not d:
            print("--panels ожидает биржа=каталог: %r" % p, file=sys.stderr)
            return 2
        panels.append((ex, d))
    counts = write_manifest(panels, a.out)
    print("manifest: " + ", ".join("%s %d" % kv for kv in counts.items()) + " → " + a.out)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("farm")
    f.add_argument("--union")
    f.add_argument("--all-files", action="store_true")
    f.add_argument("--candles", required=True)
    f.add_argument("--out", required=True)
    f.add_argument("--missing-out")
    f.set_defaults(fn=cmd_farm)
    e = sub.add_parser("estimate")
    e.add_argument("--pair", action="append", default=[])
    e.add_argument("--full", type=int, default=0)
    e.add_argument("--free-path", required=True)
    e.add_argument("--frac", type=float, default=0.8)
    e.add_argument("--end-day", default=dt.date.today().isoformat())
    e.set_defaults(fn=cmd_estimate)
    m = sub.add_parser("manifest")
    m.add_argument("--panels", action="append", required=True)
    m.add_argument("--out", required=True)
    m.set_defaults(fn=cmd_manifest)
    a = p.parse_args(argv)
    if a.cmd == "farm" and not (a.union or a.all_files):
        p.error("farm: нужен --union или --all-files")
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
