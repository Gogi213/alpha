#!/usr/bin/env python3
"""TK-083: построчная выгрузка клеток П-12 (/data/tk084/w-<сутки>) и B1 (TK-065 + досчёт ws-) -> stdout csv"""
import csv, glob, sys

days = [l.split() for l in open("/data/tk065/days/days.tsv")]
B1 = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"


def read(f):
    return [r for r in csv.DictReader(l for l in open(f, encoding="utf-8") if not l.startswith("#"))]


out = csv.writer(sys.stdout)
out.writerow("src,day,symbol,form,n_signals,n_submitted,n_fills,sum_net_bps,n_stop,n_take,n_deadline,incomplete".split(","))
for d, _m in days:
    for src, pat in (
        ("p12", f"/data/tk084/w-{d}/b5/p12/{d}/t-bid-btc4h-q1/forms.csv"),
        ("b1", f"/data/tk065/w-{d}/b5/r2/{d}/t-bid-btc4h-q1/forms.csv"),
        ("b1sup", f"/data/tk065/ws-{d}/b5/r2/{d}/t-bid-btc4h-q1/forms.csv"),
    ):
        for f in glob.glob(pat):
            for r in read(f):
                if src != "p12" and r["form"] != B1:
                    continue
                out.writerow([src, d, r["symbol"], r["form"], r["n_signals"], r["n_submitted"], r["n_fills"],
                              r["sum_net_bps"], r["n_stop"], r["n_take"], r["n_deadline"], r["incomplete"]])
