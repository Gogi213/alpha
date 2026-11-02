#!/usr/bin/env python3
"""TK-483: статус спота по каждому перпу вселенной TK-476. Вход: <w>/universe.csv + <w>/cov/*.tsv (hist.py: sym,rows,first_ms,last_ms,запросов).
Выход <w>/coverage.csv: perp_exchange,perp_symbol,base,mult,spot_symbol,spot_live,spot_archive,rows,first_utc,last_utc,status,reason.
status: «есть» (свечи REST лежат), «нет в архиве» (по критерию тикета: пары на споте нет или свечей нет) — причина в reason.
usage: status.py <w> [out.csv] [cov-каталог, по умолчанию <w>/cov; на calc — /srv/alpha-sda/candles-1m-483/cov]"""
import csv, os, sys, time


def ts(ms):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(ms) / 1000)) if ms else ""


def classify(live, arch, rows):
    """-> (status, reason)."""
    if rows > 0:
        return "есть", "REST kline spot" + ("" if live else " (пара снята, REST отдаёт историю)")
    if live:
        return "нет в архиве", "пара идёт на споте, REST пуст за всё время"
    if arch:
        return "нет в архиве", "пара снята; REST пуст; в архиве сделок spot есть — свечи только сборкой из сделок (с 2022-11)"
    return "нет в архиве", "пары на споте Bybit нет (ни сейчас, ни в архиве сделок)"


def main():
    w = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else w + "/coverage.csv"
    cdir = sys.argv[3] if len(sys.argv) > 3 else w + "/cov"
    cov = {}
    for fn in os.listdir(cdir):
        p = open(cdir + "/" + fn).read().rstrip("\n").split("\t")
        cov[p[0]] = (int(p[1]), p[2], p[3])
    n = {"есть": 0, "нет в архиве": 0}
    with open(out, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["perp_exchange", "perp_symbol", "base", "mult", "spot_symbol", "spot_live", "spot_archive",
                     "rows", "first_utc", "last_utc", "status", "reason"])
        for r in csv.DictReader(open(w + "/universe.csv")):
            rows, first, last = cov.get(r["spot_symbol"], (0, "", ""))
            st, why = classify(int(r["spot_live"]), int(r["spot_archive"]), rows)
            n[st] += 1
            wr.writerow([r["perp_exchange"], r["perp_symbol"], r["base"], r["mult"], r["spot_symbol"], r["spot_live"],
                         r["spot_archive"], rows, ts(first), ts(last), st, why])
    print("перпов %d: есть %d, нет в архиве %d" % (sum(n.values()), n["есть"], n["нет в архиве"]))


if __name__ == "__main__":
    main()
