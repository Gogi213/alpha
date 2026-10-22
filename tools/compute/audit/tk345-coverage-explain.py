#!/usr/bin/env python3
"""TK-345: объяснение дыр покрытия прохода валидности. Читает coverage.txt и grid.log единиц F1 (pass2+pass3):
для каждой (сутки, символ), которой нет в покрытии F1, ищет причину в логе (касаний нет / маркер verify не ok / fail-единица).
Печатает сводку: объяснено / не объяснено. Только чтение. Запуск: python3 tk345-coverage-explain.py [корень=/data/tk0345]."""
import collections, glob, os, re, sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else "/data/tk0345"

FAM = os.environ.get("FAM", "F1")
miss, day = set(), None
for line in open(f"{ROOT}/check/coverage.txt"):
    m = re.match(r"(2026-\d\d-\d\d): ожидается", line)
    if m:
        day = m.group(1)
        continue
    m = re.match(rf"\s+нет {FAM}: (.*)", line)
    if m:
        miss |= {(day, s) for s in m.group(1).split()}

reason = {}
for p in glob.glob(f"{ROOT}/pass2/*-{FAM}") + glob.glob(f"{ROOT}/pass3/*-{FAM}") + glob.glob(f"{ROOT}/pass4/*-{FAM}"):
    d = os.path.basename(p)[:10]
    if os.path.exists(p + "/fail"):
        s = os.path.basename(p)[11:].rsplit("-", 1)[0]
        reason[(d, s)] = "fail-единица"
        continue
    try:
        txt = open(p + "/grid.log").read()
    except OSError:
        continue
    for m in re.finditer(r"bounce-grid: (\w+) — касаний нет", txt):
        reason[(d, m.group(1))] = "касаний нет"
    for m in re.finditer(r"bounce-grid: (\w+) — маркер \S+ не `ok`", txt):
        reason[(d, m.group(1))] = "маркер verify не ok (K1)"

cnt = collections.Counter(reason.get(k, "НЕ ОБЪЯСНЕНО") for k in miss)
print(f"дыр {FAM} в покрытии: {len(miss)}")
for k, v in cnt.most_common():
    print(f"  {k}: {v}")
un = sorted(k for k in miss if k not in reason)
if un:
    print("не объяснено:", un[:20])
by_sym = collections.Counter(s for (_, s), r in reason.items() if (_, s) in miss and r == "маркер verify не ok (K1)")
print("K1 по символам:", dict(by_sym))
nt = collections.Counter(s for (d, s) in miss if reason.get((d, s)) == "касаний нет")
print("без касаний — символов:", len(nt), "суток на символ (макс):", max(nt.values()) if nt else 0)
print(" ".join(f"{s}:{n}" for s, n in sorted(nt.items())))

todo = sorted(k for k in miss if k not in reason)
if os.environ.get("OUT"):
    with open(os.environ["OUT"], "a") as f:
        for d, s_ in todo:
            f.write(f"{d} {s_}\n")
