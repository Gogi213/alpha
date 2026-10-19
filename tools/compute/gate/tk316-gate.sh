#!/usr/bin/env bash
# TK-316: гейт «байт в байт» правки qty_unit_e9/лоты/CompactEvent f64 (спот: размер в лотах + единица). Запуск на calc ТОЛЬКО через alsched
# (kind build, 1 ядро — 16-е ядро при идущем счёте): сборка головы (push-head.sh → /data/tk316) → спот-сверка → BONK → фьючерс (gate-families base).
#   python3 /data/sched/alsched.py submit --kind build --cores 1 --mem 12 --cwd /root --adhoc tk316-gate --reason "гейт TK-316" --max-runtime 4h --name tk316-gate -- bash /data/tk316/tk316-gate.sh
# Спот: старый = alpha-tk312-pgo (код = master по src после PR #71), новый = сборка tk316; сутки SOLUSDT 2026-01-05 из binlog-spot/e-2026-01,
# cmp rounds.csv/forms.csv + manifest без env-строк. BONKUSDT 2026-01-01 (в i64 не помещался) — только новый: rc=0, forms.csv непуст.
# Фьючерс: gate-families.sh base — новый против опорного b14flag на 2 сутках января (unit=1 путь).
# Итог: $W/summary.txt (строка на проверку), маркер $W/gate.done; зелёный = все rc=0 и cmp равны.
set -uo pipefail
W=/data/tk316; OLD=/opt/alpha-compute/bin/alpha-tk312-pgo; NEW=$W/alpha-new; S=$W/spot; SP=/srv/alpha-sda/binlog-spot/e-2026-01; EX=/srv/alpha-sda/binlog-spot-excluded/e-2026-01
rm -f "$W/summary.txt" "$W/gate.done"; mkdir -p "$S"
say() { echo "$*" | tee -a "$W/summary.txt"; }
grid() {  # $1 бинарь $2 корень $3 символ $4 сутки $5 лот e9 $6 порог usd $7 выход
  "$1" lob bounce-grid --root "$2" --symbol "$3" --day "$4" --queue-model prob:3 \
    --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 \
    --stop-form at --stop-form pct1.5 --take-form 1to1 --take-form tr1x1 --deadline-secs 3600 \
    --h3-mode notional --h3-usd "$6" --order-qty-e9 "$5" --threads 1 --driver setups --out-dir "$7" > "$7.log" 2>&1
}
root_of() {  # $1 каталог частей $2 символ $3 сутки $4 корень
  rm -rf "$4"; mkdir -p "$4"; ln -s "$1/$2-$3.binlog" "$4/"; ln -s "$1/verify-$2.status" "$4/"
  echo '{"start_hour_utc":0,"closed":true,"binlog_files":[]}' > "$4/session.json"; [ -f "$1/instruments.csv" ] && ln -s "$1/instruments.csv" "$4/"
}
# 0) старый на споте (проверяет и сами аргументы до долгой сборки)
root_of "$SP" SOLUSDT 2026-01-05 "$S/rt-sol"; rm -rf "$S/old-sol"
grid "$OLD" "$S/rt-sol" SOLUSDT 2026-01-05 1000000000 5000 "$S/old-sol"; rc0=$?
say "spot-old rc=$rc0 forms=$(wc -l < "$S/old-sol/forms.csv" 2>/dev/null || echo 0)"
[ "$rc0" = 0 ] || { tail -5 "$S/old-sol.log" | tee -a "$W/summary.txt"; touch "$W/gate.done"; exit 1; }
# 1) сборка головы
[ -f "$W/BUILT-$(cat "$W/HEAD")" ] || JOBS=1 bash "$W/calc-build.sh" "$W" 2>&1 | tail -3 | tee -a "$W/summary.txt"
[ -x "$NEW" ] || { say "build FAIL"; touch "$W/gate.done"; exit 1; }
# 2) спот: новый против старого
rm -rf "$S/new-sol"; grid "$NEW" "$S/rt-sol" SOLUSDT 2026-01-05 1000000000 5000 "$S/new-sol"; rc1=$?
bad=0; for f in rounds.csv forms.csv; do cmp -s "$S/old-sol/$f" "$S/new-sol/$f" || { bad=1; say "spot-sol $f РАЗНЫЕ"; }; done
diff <(grep -v '^env' "$S/old-sol/manifest.txt" | sed "s#old-sol#X#g") <(grep -v '^env' "$S/new-sol/manifest.txt" | sed "s#new-sol#X#g") > "$S/manifest.diff" || say "spot-sol manifest отличается (см. $S/manifest.diff)"
say "spot-sol new rc=$rc1 cmp_bad=$bad rounds=$(wc -l < "$S/new-sol/rounds.csv") forms=$(wc -l < "$S/new-sol/forms.csv")"
# 3) BONK: только новый (раньше не открывался)
root_of "$EX" BONKUSDT 2026-01-01 "$S/rt-bonk"; rm -rf "$S/new-bonk"
grid "$NEW" "$S/rt-bonk" BONKUSDT 2026-01-01 1000000000000000 1000 "$S/new-bonk"; rc2=$?
say "spot-bonk new rc=$rc2 forms=$(wc -l < "$S/new-bonk/forms.csv" 2>/dev/null || echo 0) rounds=$(wc -l < "$S/new-bonk/rounds.csv" 2>/dev/null || echo 0)"
# 4) фьючерс: gate-families base (опорный b14flag)
GATE_TAG=316 bash /data/tk164/gate-families.sh "$NEW" base > "$W/gate-fam.log" 2>&1; rc3=$?
say "futures gate-families base rc=$rc3: $(cat /data/tk164/gate-316/summary.txt 2>/dev/null | tr '\n' ' ')"
touch "$W/gate.done"
[ "$rc0$rc1$rc2$rc3$bad" = 00000 ]
