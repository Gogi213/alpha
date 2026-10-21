#!/usr/bin/env bash
# TK-289: psim F1 П-18 ось 8 (σ-окна 60…960, клетка B1, по проходу на окно: /data/tk0289/f8/<W>/out, f8 rc0) -> /data/tk0289/f8/<W>/psim/closes-p14f1-cap{0,3}-<мес>.json;
#   склейка по вариантам (имя p18a8v<W> различает окна) -> /data/tk0289/f8/psim/ и сводка f8-sum.csv (sum_closes_f1.py). PM=1. Как f6b-psim.sh.
set -u
export PM=1
R=/data/tk0289/f8; WS="60 90 120 180 360 480 720 960"
for w in $WS; do
  cp /data/tk0341/keep/days9.txt "$R/$w/days.txt"
  bash /opt/alpha-launch/tools/launch/psim-launch.sh "$R/$w" || exit $?
done
mkdir -p "$R/psim"
python3 - "$R" $WS <<'PY' || exit 7
import json, sys
R, ws = sys.argv[1], sys.argv[2:]
for m in range(1, 10):
    for cap in (0, 3):
        fn = f"closes-p14f1-cap{cap}-2026-{m:02d}.json"; out = {}
        for w in ws:
            d = json.load(open(f"{R}/{w}/psim/{fn}", encoding="utf-8"))
            dup = set(d) & set(out)
            assert not dup, (w, fn, dup)
            out.update(d)
        json.dump(out, open(f"{R}/psim/{fn}", "w", encoding="utf-8"), separators=(",", ":"))
PY
python3 /data/tk0231/sum_closes_f1.py "$R/psim" /data/tk0289/f8-sum.csv closes-p14f1
