#!/usr/bin/env bash
# TK-353: гейт «пары c3-*.csv байт в байт» для ускорений c3-controls (пред-отсев по ln σ, индекс used; Судья 22.10).
# Сборка головы tk353 (calc-build.sh над снимком push-head.sh) → c3-controls на кэше XRPUSDT×5 суток (tk353-slice.sh,
# журнал уже в кэше) с --events (каждая 2000-я строка approaches — только для механики, популяцию A_B не заменяет)
# без и с --no-prefilter. Зелёный = md5 всех c3-*.csv равны; счётчики отказов печатаются обе. Итог: $W/pairgate.txt.
#   alsched submit --cls prod --kind gate --name tk353-pairgate --max-runtime 1h --cores 4 --mem 24 -- bash /data/tk0353/tk353-pairgate.sh
set -uo pipefail
W=/data/tk0353; C=$W/slice-xrp/cache; O=${O:-$W/pg}; S=${S:-$W/pairgate.txt}; CAL=${CAL:-0.1}; : > "$S"; rm -rf "$O"; mkdir -p "$O"
E=/data/tk046/feb/home/alpha/epochs/e-feb; SYM=XRPUSDT; DAYS="2026-02-06 2026-02-07 2026-02-08 2026-02-09 2026-02-10"
[ -n "${SKIPBUILD:-}" ] || JOBS=4 bash /data/tk0346/calc-build.sh $W/new > $W/build-new2.log 2>&1 || { echo "build rc≠0" | tee -a "$S"; exit 70; }
echo "бинарь: $(cat $W/new/HEAD)" >> "$S"; BIN=$W/new/alpha-new
echo "arm_ms,side,price_tick" > $O/events.csv
for d in $DAYS; do awk -F, 'NR>1 && (NR%2000)==0 {print $5","$2","$3}' $C/$d/approaches-$SYM.csv >> $O/events.csv; done
echo "events: $(($(wc -l < $O/events.csv)-1))" >> "$S"
args=(); for d in $DAYS; do args+=(--day "$d=$E/study/root-$d/$SYM-$d.binlog"); done
for v in on off; do
  extra=(); [ $v = off ] && extra=(--no-prefilter)
  mkdir -p $O/$v; s=$(date +%s)
  "$BIN" lob c3-controls --symbol $SYM "${args[@]}" --cache $C --regime-from $E/study/regime --regime-edges-bps=-44.55 \
    --out $O/$v --horizon-s 1800 --tod-window-s 3600 --caliper-sd $CAL --k 1 --seed 20261022 --max-wait-s 3600 --stride-s 60 \
    --events $O/events.csv "${extra[@]}" > $O/$v.log 2>&1; echo "run $v rc=$? $(( $(date +%s)-s )) с" >> "$S"
done
( cd $O/on && md5sum c3-*.csv ) > $O/on.md5; ( cd $O/off && md5sum c3-*.csv ) > $O/off.md5
if cmp -s $O/on.md5 $O/off.md5; then echo "пары on/off: ИДЕНТИЧНЫ ($(wc -l < $O/on.md5) файлов)" >> "$S"; else echo "пары on/off: РАСХОЖДЕНИЕ" >> "$S"; diff $O/on.md5 $O/off.md5 | head >> "$S"; fi
for v in on off; do echo "== $v" >> "$S"; python3 - "$O/$v" >> "$S" <<'PY'
import json,sys,glob
for f in glob.glob(sys.argv[1]+"/c3-summary*.json"):
    j=json.load(open(f)); print({k:j.get(k) for k in ("walls","walls_matched","unmatched_share","controls","candidates","rejects")})
PY
done
touch $O.done
