#!/usr/bin/python3
"""TK-522: метки .ok L1 HL (src=rawcopy от p40-hl-rawconv) считали files= только по l1 (24), на диске l1+tr; orch.fetch_l1 строг (файлов по метке == на диске, размеры l1/tr).
Приводит files= к числу файлов на диске, если размеры l1 и tr по метке совпали с диском; несовпадение размеров — в отчёт, метка не трогается.
Запуск: fix_hl_ok.py <корень биржи, напр. /data/p40hl/hyperliquid_futures> [--dry]"""
import os, sys

root, dry = sys.argv[1], "--dry" in sys.argv
fixed = same = bad = 0
for sym in sorted(os.listdir(root)):
    for day in sorted(os.listdir(f"{root}/{sym}")):
        d = f"{root}/{sym}/{day}"
        try:
            kv = dict(x.split("=", 1) for x in open(f"{d}/.ok").read().split() if "=" in x)
        except OSError:
            continue
        fs = [f for f in os.listdir(d) if f.endswith((".l1.bin.zst", ".tr.zst"))]
        l1 = sum(os.path.getsize(f"{d}/{f}") for f in fs if f.endswith(".l1.bin.zst"))
        tr = sum(os.path.getsize(f"{d}/{f}") for f in fs if f.endswith(".tr.zst"))
        if int(kv.get("l1", -1)) != l1 or int(kv.get("tr", 0)) != tr:
            bad += 1
            print("РАЗМЕР не по метке", d, kv, "диск l1", l1, "tr", tr)
            continue
        if kv.get("files", "").split("/")[0] == str(len(fs)):
            same += 1
            continue
        kv["files"] = f"{len(fs)}/{len(fs)}"
        if not dry:
            tmp = f"{d}/.ok.tmp"
            open(tmp, "w").write(" ".join(f"{k}={v}" for k, v in kv.items()) + "\n")
            os.replace(tmp, f"{d}/.ok")
        fixed += 1
print(f"исправлено {fixed}, уже верных {same}, размер не по метке {bad}")
