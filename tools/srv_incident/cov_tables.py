#!/usr/bin/env python3
"""TK-581: таблицы §6.3 / §6.4 отчёта из выходов cov_report.py и cov_sym.py по `cov-<биржа>.json` (аргументы окон — `cov_args.txt`).
Запуск рядом со скриптами: python3 cov_tables.py <каталог с cov-*.json> <биржа> [<биржа> ...]
Медленные потоки (mark Gate — блок 5 с, mark Bybit — блок 10 с) считаются отдельным вызовом cov_sym.py с --block."""
import re, subprocess, sys, os

here = os.path.dirname(os.path.abspath(__file__))
d = sys.argv[1]
exs = sys.argv[2:]
args = open(os.path.join(here, "cov_args.txt")).read().split()
BLOCK = {("gate", "m"): 5, ("bybit", "m"): 10}
num = lambda s: int(s.replace(" ", ""))


def run(script, extra):
    out = subprocess.run([sys.executable, "-X", "utf8", "-I", os.path.join(here, script)] + extra, capture_output=True, text=True, encoding="utf-8").stdout
    return out


rep_re = re.compile(r"\[(эпизод|контроль)\] (\S+) \((\d+) с\)\s+(.*)")
kind_re = re.compile(r"([btm]): rx\s+([\d ]+?) E\s+([\d ]+?) ожид\s+([\d ]+?) охват E\s+([\d.]+)%")
sym_re = re.compile(r"(эпизод|контроль)\s+(\S+) \(\s*(\d+) с\): ликвидных\s+(\d+); пустых символо-с\s+(\d+) =\s+([\d.]+) %; самая длинная дыра\s+(\d+) с; символов с дырой ≥ 5 с:\s+(\d+); без записей всё окно:\s+(\d+); записей\s+(\d+) из\s+(\d+) = \s*([\d.]+) %")

for ex in exs:
    path = os.path.join(d, f"cov-{ex}.json")
    cov = {}  # (tag, window) -> {kind: pct}
    for line in run("cov_report.py", [path] + args).splitlines():
        m = rep_re.search(line)
        if m:
            cov[(m.group(1), m.group(2))] = {k: (num(e), num(x), float(p)) for k, _, e, x, p in kind_re.findall(m.group(4))}
    print(f"\n#### {ex}: охват E-секунд (записей / ожидание, %)")
    eps = [(w, v) for (t, w), v in cov.items() if t == "эпизод"]
    ctl = [v for (t, w), v in cov.items() if t == "контроль"]
    for k, kn in (("b", "книга"), ("t", "сделки"), ("m", "mark")):
        ctl_p = [v[k][2] for v in ctl if k in v]
        print(f"- {kn}: эпизоды " + " / ".join(f"{v[k][2]:.1f}" for _, v in eps if k in v) + f"; контроль {min(ctl_p):.1f}–{max(ctl_p):.1f}")
    for k, kn in (("b", "книга"), ("m", "mark")):
        blk = BLOCK.get((ex, k), 1)
        sym = {}
        for line in run("cov_sym.py", [path] + args + ["--types", k, "--block", str(blk)]).splitlines():
            m = sym_re.search(line)
            if m:
                sym.setdefault(m.group(1), []).append(tuple(m.groups()[1:]))
        print(f"- дыры у ликвидных, {kn} (единица {blk} с): ")
        for w, dur, n, emp, pct, lg, g5, allz, act, exp, rp in sym.get("эпизод", []):
            print(f"    {w} ({dur} с): ликвидных {n}, пустых {emp} = {pct} %, дыра ≤ {lg} с, символов с дырой ≥ 5 с: {g5}, молчали всё окно: {allz}; записей {act} из {exp} = {rp} %")
        c = sym.get("контроль", [])
        if c:
            print(f"    контроль (9 окон): пустых {min(float(x[4]) for x in c):.1f}–{max(float(x[4]) for x in c):.1f} %, дыра {min(int(x[5]) for x in c)}–{max(int(x[5]) for x in c)} с, ликвидных {min(int(x[2]) for x in c)}–{max(int(x[2]) for x in c)}, записей/ожид. {min(float(x[10]) for x in c):.0f}–{max(float(x[10]) for x in c):.0f} %")
