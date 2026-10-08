#!/usr/bin/env python3
"""TK-063 П-12 R2: равная экспозиция §6(2) и подсчёт срабатываний механики §8.
Читает сырые деревья волны (s-v, s-vt: все сигналы) для нотионала базы и busy-replay'нутые (v-b, vt-b) для сделок клетки;
пишет vn-b/vtn-b (qty × m для семей g92/g93: m = N_B1/N_peak; g94/g87: m = min(1, N_ref/N_peak); N_peak = qty*entry_vwap
сделки — qty строки rounds уже суммарный по добавкам, выход один; N_ref = qty/fill_frac*entry_vwap строки базы того же
сигнала = плановая полная лестница) и expo.json (срабатывания механики по форме и месяцу, средний m).
Срабатывание: g92/g93/g94/g87 — qty или entry_vwap сделки отличается от базы того же сигнала (сработала добавка/доля);
e119 — reason == converge; e114/e117 — выход (exit_ns/reason) отличается от базы того же сигнала.
Запуск на сервере счёта: p12-r2-expo.py /data/p12r2"""
import csv
import glob
import json
import os
import sys
from collections import defaultdict

O = sys.argv[1]
B1 = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
B3 = "ladder3x0..0.0409sw2-pct2-1to1-14400-ttl1800"
FAM = {"pyre": "g92", "pynw": "g93", "pyeat": "g94", "pyfresh": "g87", "conv": "e119", "halfstop": "e114", "halflevel": "e114", "tsl": "e117"}
FIELDS = "symbol,day_utc,form,signal_index,t0_ns,dir,entry_px,exit_px,qty,net_bps,reason,exit_ns,fill_frac,entry_vwap,legs_filled,legs_rejected".split(",")


def fam_of(form, base):
    if not form.startswith(base + "-"):
        return None
    suf = form[len(base) + 1:]
    for k, f in FAM.items():
        if suf.startswith(k):
            return f
    return None


def read(path):
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    head = [l for l in lines if l.startswith("#")]
    return head, list(csv.DictReader(l for l in lines if not l.startswith("#")))


fired = defaultdict(lambda: defaultdict(int))
msum = defaultdict(lambda: defaultdict(float))
mn = defaultdict(lambda: defaultdict(int))
miss = defaultdict(int)
for rawd, bd, base in (("s-v", "v-b", B1), ("s-vt", "vt-b", B3)):
    for f in sorted(glob.glob(f"{O}/{bd}/20*/20*/*/rounds.csv")):
        parts = f.split("/")
        mo, day, st = parts[-4], parts[-3], parts[-2]
        _, rraw = read(f"{O}/{rawd}/{mo}/{day}/{st}/rounds.csv")
        braw = {(r["symbol"], r["signal_index"]): r for r in rraw if r["form"] == base}
        head, rows = read(f)
        outrows = []
        for r in rows:
            fam = fam_of(r["form"], base)
            b = braw.get((r["symbol"], r["signal_index"]))
            if fam and b is None:
                miss[fam] += 1
            if fam and b is not None:
                if fam in ("g92", "g93", "g94", "g87"):
                    diff = abs(float(r["qty"]) - float(b["qty"])) > 1e-9 or abs(float(r["entry_vwap"]) - float(b["entry_vwap"])) > 1e-12
                elif fam == "e119":
                    diff = r["reason"] == "converge"
                else:
                    diff = r["exit_ns"] != b["exit_ns"] or r["reason"] != b["reason"]
                if diff:
                    fired[r["form"]][mo] += 1
                if fam in ("g92", "g93", "g94", "g87"):
                    npk = float(r["qty"]) * float(r["entry_vwap"])
                    if fam in ("g92", "g93"):
                        m = float(b["qty"]) * float(b["entry_vwap"]) / npk if npk else 1.0
                    else:
                        nref = float(b["qty"]) / (float(b["fill_frac"]) or 1.0) * float(b["entry_vwap"])
                        m = min(1.0, nref / npk) if npk else 1.0
                    r = dict(r)
                    r["qty"] = "%.10f" % (float(r["qty"]) * m)
                    msum[fam][mo] += m
                    mn[fam][mo] += 1
            outrows.append(r)
        od = f"{O}/{bd[:-2]}n-b/{mo}/{day}/{st}"
        os.makedirs(od, exist_ok=True)
        with open(f"{od}/rounds.csv", "w", encoding="utf-8", newline="") as fh:
            fh.writelines(head)
            w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
            w.writeheader()
            w.writerows(outrows)
json.dump({"fired": fired, "m_mean": {f: {mo: msum[f][mo] / mn[f][mo] for mo in mn[f]} for f in mn}, "m_n": mn, "base_missing": miss},
          open(f"{O}/expo.json", "w"), ensure_ascii=False, indent=1)
print("ok", dict(miss))
