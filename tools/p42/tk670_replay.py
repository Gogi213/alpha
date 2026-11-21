#!/usr/bin/env python3
"""П-42 §13.70 (TK-670), G-670a: `D_S` (E4 / D шорт: ключи `S_base` + `lx=1 tf=60`) в выборке (wa / wb / wc, h775, сутки ≤ 13.11) бинарём, замороженным в `tk670-frozen.json`, побайтно как выходы TK-668
(`/data/p42-tk668/e4/cells/<окно>/S_lx1/short-{trades,daily,coinday,entryrej,sigsx}.csv`). Сутки ≥ 14.11 не читаются (изоляция G6 `tk604_stats.check_dates`, деревья окон кончаются 13.11).
  python3 tk670_replay.py --bin /data/p42-tk670/bin/alpha-lx.<md5> --keys "<ключи `D_S` из tk670-frozen.json>" [--root /data/p42-tk670/replay] [--ref /data/p42-tk668/e4/cells] [--out gate-670a.json]
Ключи — замороженные (без `lw`); TK-668 печатал `lw` по T0 окна — совпадение выходов доказывает, что при `lx = 1` `lw` на счёт не влияет.
Итог — `--out` (по умолчанию `<root>/gate-670a.json`); различие или нет файла — код 3."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FILES = ("short-trades.csv", "short-daily.csv", "short-coinday.csv", "short-entryrej.csv", "short-sigsx.csv")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def run(a):
    import tk604_stats as st
    import tk625_run as r5
    import tk668_e4 as e4
    if not a.bin or not os.path.isfile(a.bin):
        raise SystemExit("--bin: нет файла %r" % a.bin)
    if not a.keys or "lx=1" not in a.keys.split() or "tf=60" not in a.keys.split():
        raise SystemExit("--keys: нужны замороженные ключи `D_S` (lx=1 tf=60): %r" % a.keys)
    e4.BIN = a.bin
    u = r5.Uni("h775")
    vid = e4.vid("S", 1)
    res = {"bin": a.bin, "bin_sha256": sha256_file(a.bin), "cell": "D_S", "label": vid, "keys": a.keys, "windows": {}}
    for w in st.TRADE_WINDOWS:
        out = os.path.join(a.root, w.name)
        if not os.path.exists(out + "/DONE"):
            e4.tick(u, w, [(vid, a.keys)], out, {"P42_SIGSX": e4.SIGSX}, os.path.join(a.root, "log-%s.txt" % w.name))
            open(out + "/DONE", "w").close()
        files = {}
        for f in FILES:
            x, y = os.path.join(out, vid, f), os.path.join(a.ref, w.name, vid, f)
            sx, sy = (sha256_file(x) if os.path.exists(x) else None), (sha256_file(y) if os.path.exists(y) else None)
            files[f] = {"sha256": sx, "ref_sha256": sy, "equal": sx is not None and sx == sy}
        res["windows"][w.name] = {"t0": w.t0, "tk668_lw": e4.lw_of(w), "tk668_line": e4.e4_line(u, "S", 1, w), "files": files, "equal": all(v["equal"] for v in files.values())}
    res["ok"] = all(v["equal"] for v in res["windows"].values())
    outp = a.out or os.path.join(a.root, "gate-670a.json")
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("G-670a: %s (%s); %s" % ("ПРОЙДЕНЫ" if res["ok"] else "НЕ ПРОЙДЕНЫ", ", ".join("%s %s" % (n, "=" if v["equal"] else "≠") for n, v in res["windows"].items()), outp))
    return 0 if res["ok"] else 3


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bin", default="")
    ap.add_argument("--keys", default="")
    ap.add_argument("--root", default="/data/p42-tk670/replay")
    ap.add_argument("--ref", default="/data/p42-tk668/e4/cells")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    os.makedirs(a.root, exist_ok=True)
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
