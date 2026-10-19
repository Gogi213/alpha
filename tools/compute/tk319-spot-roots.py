#!/usr/bin/env python3
"""TK-319 (П-21 спот B1): корни для day-cells.sh на спот-бинлогах (/srv/alpha-sda/binlog-spot/e-2026-MM, verify TK-312).
Фьючерсные корни day-cells (/data/tk046/<мес>/home, verdict.csv, b1-symdays.csv) не трогаются — спот лежит в /data/tk0319.

    tk319-spot-roots.py inst                 /data/tk0319/instruments.csv  (REST instruments-info category=spot, шапка как у пула)
    tk319-spot-roots.py verdict              /data/tk0319/verdict.csv, b1-symdays.csv, syms.txt  (verify-<SYM>.status == ok → «пускаем»)
    tk319-spot-roots.py epoch <мес> [сутки]  /data/tk0319/e-<мес>: root/ (симлинки бинлогов + instruments.csv), study/root-<сутки>/, study/sigma240 → общая
Дальше (скрипты рядом): ref-klines.py --category spot → sigma-table.py → tk319-jrn-day.sh (касания D20) → regime.py (BTCUSDT спот, В-… Q-2 Судьи).
"""
import csv, glob, json, os, sys, urllib.request

SRC = "/srv/alpha-sda/binlog-spot"
D = os.environ.get("TK319", "/data/tk0319")
MON = {"jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06", "jul": "07", "aug": "08", "sep": "09"}
HEAD = ["symbol", "tick_size", "min_order_qty", "qty_step", "min_notional_value", "h3_lots", "k", "median_trade_lots",
        "window_start_utc_ms", "window_secs", "depth_check"]


def inst():
    with urllib.request.urlopen("https://api.bybit.com/v5/market/instruments-info?category=spot&limit=1000", timeout=30) as r:
        lst = json.load(r)["result"]["list"]
    os.makedirs(D, exist_ok=True)
    n = 0
    with open(f"{D}/instruments.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(HEAD)
        for x in sorted(lst, key=lambda x: x["symbol"]):
            if x.get("quoteCoin") != "USDT":
                continue
            lf, pf = x["lotSizeFilter"], x["priceFilter"]
            w.writerow([x["symbol"], pf["tickSize"], lf["minOrderQty"], lf["basePrecision"], lf["minOrderAmt"]] + [""] * 6)
            n += 1
    print(f"instruments.csv: {n} USDT-пар → {D}/instruments.csv")


def verdict():
    os.makedirs(D, exist_ok=True)
    rows, sd, syms = [], [], set()
    for mon, mm in MON.items():
        e = f"{SRC}/e-2026-{mm}"
        ok = {os.path.basename(p)[len("verify-"):-len(".status")] for p in glob.glob(f"{e}/verify-*.status")
              if open(p, encoding="utf-8").read().strip() == "ok"}
        for p in sorted(glob.glob(f"{e}/*-2026-{mm}-??.binlog")):
            b = os.path.basename(p)[:-len(".binlog")]
            sym, day = b[:-11], b[-10:]
            good = sym in ok
            rows.append([sym, day, f"2026-{mm}", "пускаем" if good else "не пускаем", "" if good else "verify-status не ok"])
            if good:
                sd.append([mon, day, sym]); syms.add(sym)
    with open(f"{D}/verdict.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(["sym", "day", "month", "verdict", "reason"]); w.writerows(rows)
    with open(f"{D}/b1-symdays.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(["month", "day", "symbol"]); w.writerows(sd)
    open(f"{D}/syms.txt", "w", encoding="utf-8").write("".join(s + "\n" for s in sorted(syms)))
    print(f"verdict.csv: {len(rows)} символо-суток, пускаем {len(sd)}, символов {len(syms)}")


def epoch(mon, only=None):
    mm = MON[mon]
    e, src = f"{D}/e-{mon}", f"{SRC}/e-2026-{mm}"
    os.makedirs(f"{e}/root", exist_ok=True); os.makedirs(f"{e}/study", exist_ok=True)
    inst_p = f"{D}/instruments.csv"
    files = sorted(glob.glob(f"{src}/*-2026-{mm}-??.binlog"))
    for p in files:
        q = f"{e}/root/{os.path.basename(p)}"
        if not os.path.lexists(q):
            os.symlink(p, q)
    if not os.path.lexists(f"{e}/root/instruments.csv"):
        os.symlink(inst_p, f"{e}/root/instruments.csv")
    ver = sorted(glob.glob(f"{src}/verify-*.status"))  # К1 (В-56): без маркера ok `lob touches` отказывает
    for p in ver:
        q = f"{e}/root/{os.path.basename(p)}"
        if not os.path.lexists(q):
            os.symlink(p, q)
    sj = '{"start_hour_utc":0,"closed":true,"binlog_files":[]}\n'  # как у фьючерсных корней: lob bounce-grid читает session.json корня
    for dd in (f"{e}/root",):
        if not os.path.lexists(f"{dd}/session.json"):
            open(f"{dd}/session.json", "w").write(sj)
    if not os.path.lexists(f"{e}/study/sigma240"):
        os.symlink(f"{D}/sigma240", f"{e}/study/sigma240")
    days = sorted({os.path.basename(p)[-17:-7] for p in files})
    for d in days:
        if only and d != only:
            continue
        r = f"{e}/study/root-{d}"
        os.makedirs(r, exist_ok=True)
        for p in files:
            if os.path.basename(p).endswith(f"-{d}.binlog") and not os.path.lexists(f"{r}/{os.path.basename(p)}"):
                os.symlink(p, f"{r}/{os.path.basename(p)}")
        for p in ver + [inst_p]:
            if not os.path.lexists(f"{r}/{os.path.basename(p)}"):
                os.symlink(p, f"{r}/{os.path.basename(p)}")
        if not os.path.lexists(f"{r}/session.json"):
            open(f"{r}/session.json", "w").write(sj)
    print(f"{e}: суток {len(days)}, бинлогов {len(files)}")


if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else ""
    if c == "inst": inst()
    elif c == "verdict": verdict()
    elif c == "epoch" and len(sys.argv) > 2: epoch(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    else: sys.exit(__doc__)
