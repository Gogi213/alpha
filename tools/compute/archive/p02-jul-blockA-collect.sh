#!/usr/bin/env bash
# TK-018 (В-151): сборщик блока A П-02 на июле — после всех 31 суток `p02-jul-blockA-day.sh`. Те же шаги `scan`,
# что дали счёты авг/сен (пороги августа как есть, ничего не калибруется), но с июльскими каталогами В ТОМ ЖЕ вызове:
# авг/сен в новом файле — гейт «тот же код → те же счёты», сверка с прежними `data/p02/*-2026-09-26.json`
# (лежат в <T>/old/). Июль — описание, Холм не применяется.
#   Г-07 — `p02-wall2.py scan --jul-dir` (пороги `study/p02c/p02-wall2-thresholds.json`, как в авг/сен)
#   Г-46 — `p02-wave3-analyze.py scan --flow-dirs …`
#   Г-33 — `p02-g33-analyze.py scan --dirs …`
#   Г-36 — `p02-g36-causal.py scan --dirs <D20 эпох> --from 2026-07-01 --to 2026-09-23 --exclude-failed-dirs <touches>`
# Выход: ~/alpha/epochs/e-jul/study/p02jul/counts/{p02-wall2,p02-wave3,p02-g33,p02-g36}-counts-jul.json + gate.txt.
#
#   bash ~/alpha/tmp-p02jul/p02-jul-blockA-collect.sh
set -euo pipefail
H="$HOME/alpha"
T="${P02JUL_TOOLS:-$H/tmp-p02jul}"
J=epochs/e-jul/study/p02jul
C="$H/$J/counts"
cd "$H"
n=$(ls "$J"/2026-07-??.done 2>/dev/null | wc -l)
[ "$n" -eq 31 ] || { echo "готово суток $n из 31 — сборщик не запускается" >&2; exit 3; }
mkdir -p "$C"
PY="nice -n 15 python3"

$PY "$T/p02-wall2.py" scan --thresholds study/p02c/p02-wall2-thresholds.json \
  --aug-dir epochs/e-aug/study/p02c/aug --sept-dirs epochs/e-archive/study/p02c/sept,study/p02c/sept \
  --jul-dir "$J/touches" --out "$C/p02-wall2-counts-jul.json" 2> "$C/wall2.log"
$PY "$T/p02-wave3-analyze.py" scan \
  --flow-dirs epochs/e-aug/study/p02e/flow,epochs/e-archive/study/p02e/flow,study/p02e/flow,"$J/flow" \
  --out "$C/p02-wave3-counts-jul.json" 2> "$C/wave3.log"
$PY "$T/p02-g33-analyze.py" scan \
  --dirs epochs/e-aug/study/p02e/g33,epochs/e-archive/study/p02e/g33,study/p02e/g33,"$J/g33" \
  --out "$C/p02-g33-counts-jul.json" 2> "$C/g33.log"
$PY "$T/p02-g36-causal.py" scan \
  --dirs epochs/e-aug/study/approaches/D20,epochs/e-archive/study/approaches/D20,study/approaches/D20,epochs/e-jul/study/approaches/D20 \
  --from 2026-07-01 --to 2026-09-23 \
  --exclude-failed-dirs epochs/e-aug/study/p02c/aug,epochs/e-archive/study/p02c/sept,study/p02c/sept,"$J/touches" \
  --out "$C/p02-g36-counts-jul.json" 2> "$C/g36.log"

python3 - "$C" "$T/old" > "$C/gate.txt" <<'PY'
import json, os, sys
c, old = sys.argv[1], sys.argv[2]
pairs = [("p02-wall2", "p02-wall2-counts-2026-09-26.json"), ("p02-wave3", "p02-wave3-counts-2026-09-26.json"),
         ("p02-g33", "p02-g33-counts-2026-09-26.json"), ("p02-g36", "p02-g36-counts-2026-09-26.json")]
ok_all = True
for new, oldf in pairs:
    n = json.load(open(os.path.join(c, new + "-counts-jul.json"), encoding="utf-8"))
    o = json.load(open(os.path.join(old, oldf), encoding="utf-8"))
    dn, do = n["day_counts"], o["day_counts"]
    bad = [d for d in do if dn.get(d, {}).get("variants") != do[d].get("variants")]
    thr = ("thresholds" not in o) or (n.get("thresholds") == o["thresholds"])
    jul = sorted(d for d in dn if d.startswith("2026-07"))
    ok = not bad and thr and len(jul) == 31
    ok_all &= ok
    print(f"{new}: авг/сен суток {len(do)}, расхождений {len(bad)} {bad[:3]}; пороги равны {thr}; июль суток {len(jul)} -> {'ok' if ok else 'FAIL'}")
print("GATE", "ok" if ok_all else "FAIL")
PY
cat "$C/gate.txt"
grep -q "^GATE ok" "$C/gate.txt"
