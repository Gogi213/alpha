#!/usr/bin/env python3
"""TK-483 проверка приёмки без тяжёлого счёта: итог гейта gate.py на calc (юнит tk483-gate4) и coverage в git.
По ssh читает только rc-файл и лог гейта (не обходит /data): rc == 0 и в логе «нарушений 0»; локально — coverage-csv:
3400 строк, у каждой «есть» ненулевые строки и спот-символ, спот-символов «есть» ровно 510 (мин. один файл на символ).
usage: remote_check.py [ХОСТ=root@89.163.242.211] [RC=/data/sched/rc/tk483-gate4] [ЛОГ=/data/tk0483/gate4.log]"""
import csv, os, subprocess, sys

HOME_SSH = os.path.join(os.path.expanduser("~"), ".ssh")
COV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "docs", "findings",
                   "spot-candles-coverage-2026-11-02.csv")
PERPS, SYMBOLS = 3400, 510


def remote(host, rc, log):
    """rc-файл и последняя строка лога гейта с calc → (rc, строка лога)."""
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "UserKnownHostsFile=" + os.path.join(HOME_SSH, "known_hosts")]
    key = os.path.join(HOME_SSH, "id_rsa")
    if os.path.exists(key):
        cmd += ["-i", key]
    cmd += [host, "cat %s; tail -n 1 %s" % (rc, log)]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60)
    if out.returncode != 0:
        raise RuntimeError("ssh rc %d: %s" % (out.returncode, out.stderr.strip()))
    lines = out.stdout.strip().splitlines()
    return lines[0].strip(), lines[-1]


def coverage(path):
    """Нарушения coverage-csv: строк != 3400, «есть» без строк/символа, символов «есть» != 510."""
    bad, syms = [], set()
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if len(rows) != PERPS:
        bad.append("строк %d != %d" % (len(rows), PERPS))
    for r in rows:
        if r["status"] == "есть":
            if int(r["rows"]) <= 0 or not r["spot_symbol"]:
                bad.append("%s: есть без строк/символа" % r["perp_symbol"])
            syms.add(r["spot_symbol"])
    if len(syms) != SYMBOLS:
        bad.append("спот-символов 'есть' %d != %d" % (len(syms), SYMBOLS))
    return bad, len(rows), len(syms)


def main(argv):
    host = argv[1] if len(argv) > 1 else "root@89.163.242.211"
    rc = argv[2] if len(argv) > 2 else "/data/sched/rc/tk483-gate4"
    log = argv[3] if len(argv) > 3 else "/data/tk0483/gate4.log"
    code, last = remote(host, rc, log)
    bad = []
    if code != "0":
        bad.append("rc гейта %s != 0" % code)
    if "нарушений 0" not in last:
        bad.append("лог гейта: %s" % last)
    cov_bad, n, s = coverage(COV)
    bad += cov_bad
    print("гейт rc %s, лог: %s | coverage строк %d, спот-символов %d" % (code, last, n, s))
    for b in bad:
        print("НАРУШЕНИЕ:", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
