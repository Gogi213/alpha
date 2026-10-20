"""TK-303: дельта Летописи F2 (B + R1 + первая подача) -> add-<имя>.jsonl; без аргументов — на dev: из git diff <база>..tk303, пишет в <каталог>;
на calc (--apply <каталог add-*>): дописью в /data/launch/registry без дублей (ключи как tk268-deploy-delta.py), бэкап /data/launch/prev-tk303/registry.
  dev:  python tk303-deploy-delta.py --make <каталог> [база=4e771ab1]
  calc: python3 tk303-deploy-delta.py --apply <каталог> [--dry]"""
import json, os, shutil, subprocess, sys

NAMES = ("cells", "run_cells", "results", "runs", "sieve", "plan_cells", "params", "rules")
KEYS = {
    "results": lambda d: (d["run_id"], d["cell_id"], d["month"]),
    "cells": lambda d: d["id"],
    "run_cells": lambda d: (d["run_id"], d["cell_id"]),
    "runs": lambda d: d["id"],
    "sieve": lambda d: json.dumps(d, sort_keys=True),
    "plan_cells": lambda d: json.dumps(d, sort_keys=True),
    "params": lambda d: json.dumps(d, sort_keys=True),
    "rules": lambda d: json.dumps(d, sort_keys=True),
}


def make(out, base):
    os.makedirs(out, exist_ok=True)
    for n in NAMES:
        d = subprocess.run(["git", "diff", base, "tk303", "--", f"docs/registry/{n}.jsonl"], capture_output=True, text=True, encoding="utf-8", check=True).stdout
        add = [l[1:] for l in d.splitlines() if l.startswith("+") and not l.startswith("+++")]
        with open(f"{out}/add-{n}.jsonl", "w", encoding="utf-8", newline="") as f:
            f.writelines(l.rstrip("\r") + "\n" for l in add)
        print(n, len(add))


def lines(p):
    return [l.rstrip("\r\n") for l in open(p, encoding="utf-8", newline="") if l.strip()]


def apply(a, dry=False):
    r = "/data/launch/registry"
    b = "/data/launch/prev-tk303/registry"
    if not dry:
        os.makedirs(b, exist_ok=True)
    for n in NAMES:
        if dry:
            break
        dst = f"{b}/{n}.jsonl"
        if not os.path.exists(dst):
            shutil.copy2(f"{r}/{n}.jsonl", dst)
    for n in NAMES:
        have = {KEYS[n](json.loads(l)) for l in lines(f"{r}/{n}.jsonl")}
        new = [l for l in lines(f"{a}/add-{n}.jsonl") if KEYS[n](json.loads(l)) not in have]
        if dry:
            print(n, "+", len(new), "из", len(lines(f"{a}/add-{n}.jsonl")))
            continue
        with open(f"{r}/{n}.jsonl", "ab") as f:
            if os.path.getsize(f.name) and open(f.name, "rb").read()[-1:] != b"\n":
                f.write(b"\n")
            f.write("".join(l + "\n" for l in new).encode("utf-8"))
        print(n, "+", len(new), "из", len(lines(f"{a}/add-{n}.jsonl")))


if __name__ == "__main__":
    if sys.argv[1] == "--make":
        make(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "4e771ab1")
    else:
        apply(sys.argv[2], dry=len(sys.argv) > 3 and sys.argv[3] == "--dry")
