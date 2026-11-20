#!/usr/bin/env python3
"""П-42 §13.36 / заморозка `tk638-frozen.json` (TK-638): ворота G-638a — клетка `L_n24` (`L_base` + `t24=12` + `fa=N24_q60 fo=lo fv=0.5`, таблица `--ftab` h775) замороженным бинарём `alpha-mk.396989e9`
побайтно равна прогону TK-638 (`alpha-master a112684a`): `long-{trades,daily,coinday,entryrej}.csv` × окна wa / wb / wc. Читает только 136 суток 01.07–13.11 (сутки ≥ 14.11 не читаются).
  python3 tk638_gate.py   (srv; юнит `systemd-run`, без pkill)  → /data/p42-tk638/gate-638a/gate.json
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tk604_run as r4  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk638_run as t8  # noqa: E402

FROZEN_BIN = "/data/p42-tk616/alpha-mk.396989e9"
FROZEN_MD5 = "396989e9d0ad34d19f9b512a659c8e8e"
CID = "N24_q60"
KEYS = "%s t24=12 fa=%s fo=lo fv=0.5" % (r5.BASE["L"], CID)
FILES = ("long-trades.csv", "long-daily.csv", "long-coinday.csv", "long-entryrej.csv")


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def main():
    if md5(FROZEN_BIN) != FROZEN_MD5:
        sys.exit("бинарь не замороженный")
    r4.BIN = FROZEN_BIN
    u = r5.Uni("h775")
    out_root = t8.q("gate-638a", "x")
    res = {"bin_md5": FROZEN_MD5, "keys": KEYS, "cell": CID, "windows": {}, "ok": True}
    for w in st.TRADE_WINDOWS:
        ftab = os.path.join(t8.ROOT, t8.sub(u, "ftab38"), "%s-L.csv" % w.name)
        out = os.path.join(os.path.dirname(out_root), "%s-L" % w.name)
        r5.tick(u, w, [(CID, KEYS)], out, {}, t8.q("log", "gate638a-%s.log" % w.name), ftab=ftab)
        ref = os.path.join(t8.ROOT, t8.sub(u, "ladd"), "%s-L" % w.name, CID)
        files = {}
        for f in FILES:
            a, b = os.path.join(out, CID, f), os.path.join(ref, f)
            files[f] = {"sha256": sha(a), "ref_sha256": sha(b), "equal": sha(a) == sha(b)}
        res["windows"][w.name] = {"ftab_sha256": sha(ftab), "files": files, "equal": all(x["equal"] for x in files.values())}
        res["ok"] &= res["windows"][w.name]["equal"]
        t8.say("%s: %s" % (w.name, "равно" if res["windows"][w.name]["equal"] else "РАСХОЖДЕНИЕ"))
    json.dump(res, open(os.path.join(os.path.dirname(out_root), "gate.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    t8.say("G-638a:", "пройдены" if res["ok"] else "НЕ пройдены")
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
