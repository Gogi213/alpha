#!/usr/bin/env bash
# TK-262 (В-19): «было/стало» на БОЕВОМ пути формы вместо стендовых волн (tk048-orch-*, выведены 16.10).
#   Гоняет тот же tools/launch/run-launch.sh -> day-cells.sh (xargs -P NX по суткам), тот же BIN (pgo из MANIFEST) и флаги, что ставит форма,
#   на фиксированном срезе: 10 клеток × 24 суток марта. Выход — только <каталог>/pass-<метка>/ (вне Летописи и канона; новых хешей нет,
#   postload упадёт без run.json — красная строка в <RUN>/postload.log, это ожидаемо).
#   bs-launch.sh prep <каталог>                  срез: plan/ + days.txt (идемпотентно)
#   bs-launch.sh pass <каталог> <метка>          один проход; ВНУТРИ окна замка: /data/benchrun.sh stand bash bs-launch.sh pass …
#   bs-launch.sh abba <каталог> "<метки>"        метки по очереди (A1 A2 B1 …), env BIN_<ЛАТ> (BIN_B=…) подменяет бинарь прохода метки на B*; EXTRA_<ЛАТ> — env прохода
# Срез: PLAN=p14f1 TK-231 (тот же план F1, что считала форма), месяц mar: клетки — первые 10 std-строк p14f1-2026-03.cells (tape/cxl нет — одна пачка);
#   сутки — day-order.sh (тяжёлые первыми), каждая 4-я пропущена: 24 из 31, веса 33…6 символов сохранены; диск — какой отдал ядро (sda/sdb в diskstats).
# Метрики pass: wall (до конца xargs и итого), grid-стена (последний done), хвост (последняя сутки одна), iowait/user/sys по /proc/stat,
#   байты sda/sdb (diskstats), занятых слотов (процессы bounce-grid, выборка 5 с), md5-список выходов и diff с эталоном (первый проход каталога).
set -uo pipefail
S=${SCRIPTS:-/data/tk0231}; SRC=${SRC_PLAN:-/data/tk0231/plan}; MON=mar; YM=2026-03
mode=$1; D=$2

prep() {
  mkdir -p "$D/plan"; [ -s "$D/days.txt" ] && return 0
  P=$D/plan
  grep -vE 'tape|cxl' "$SRC/p14f1-$YM.cells" | head -10 > "$P/L-$YM.cells"
  cp "$SRC/p14f1-$YM.args" "$P/L-$YM.args"; : > "$P/L.cells"; : > "$P/L.args"; echo "$YM" > "$P/months.txt"; echo r2 > "$P/L-$YM.bin"
  bash "$S/day-order.sh" "${B1:-/data/tk0115/delta/b1-symdays.csv}" "$MON" | awk 'NR%4!=0{print "'"$MON"'", $1}' > "$D/days.txt"
}

snap() { # метка -> файл: cpu-строка и сектора sda/sdb
  { head -1 /proc/stat; awk '$3=="sda"||$3=="sdb"{print $3, $6, $10}' /proc/diskstats; } > "$1"
}

pass() {
  lab=$3; R=$D/pass-$lab
  [ -e "$R/metrics.txt" ] && { echo "pass-$lab уже есть" >&2; return 0; }
  mkdir -p "$R"; cp -r "$D/plan" "$R/plan"; cp "$D/days.txt" "$R/days.txt"; echo "$YM" > "$R/plan/months.txt"
  bin=${BIN:-/opt/alpha-compute/bin/alpha-tk231-pgo}
  case $lab in B*) [ -n "${BIN_B:-}" ] && bin=$BIN_B;; esac
  export BIN=$bin SCRIPTS=$S NX=${NX:-12}
  [ "${E2E:-0}" = 1 ] && { mkdir -p "$R/e2e"; export ALPHA_E2E=$R/e2e/all.jsonl; }  # E2E=1: учёт стадий монето-суток (TK-052), отчёт — tools/compute/archive/e2e-report.py $R/e2e
  [ "${DROP:-1}" = 1 ] && { sync; echo 3 > /proc/sys/vm/drop_caches; }
  ( while :; do echo "$(date +%s) $(pgrep -fc 'lob bounce-grid') $(head -1 /proc/stat)" >> "$R/samples.txt"; sleep 5; done ) & SP=$!
  snap "$R/s0.txt"; t0=$(date +%s.%N)
  bash "${RUN_LAUNCH:-/opt/alpha-launch/tools/launch/run-launch.sh}" "$R" > "$R/run.out" 2>&1; rc=$?
  t1=$(date +%s.%N); snap "$R/s1.txt"; kill $SP 2>/dev/null; wait $SP 2>/dev/null
  ( cd "$R/out" && find run-*/out -type f -print0 | xargs -0 -r md5sum | sort -k2 ) > "$R/outputs.md5"
  [ -e "$D/ref.md5" ] || cp "$R/outputs.md5" "$D/ref.md5"
  diff -q "$D/ref.md5" "$R/outputs.md5" > /dev/null && df=0 || df=$(diff "$D/ref.md5" "$R/outputs.md5" | grep -c '^[<>]')
  python3 - "$R" "$t0" "$t1" "$rc" "$df" "$bin" "$lab" > "$R/metrics.txt" <<'PY'
import sys, os, glob
R, t0, t1, rc, df, binp, lab = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4], sys.argv[5], sys.argv[6], sys.argv[7]
def cpu(f):
    v = [int(x) for x in open(f).readline().split()[1:9]]; return v  # user nice sys idle iowait irq softirq steal
a, b = cpu(R + "/s0.txt"), cpu(R + "/s1.txt"); d = [y - x for x, y in zip(a, b)]; tot = sum(d) or 1
disk = {}
for f in ("s0.txt", "s1.txt"):
    for l in open(R + "/" + f).read().split("\n")[1:]:
        p = l.split()
        if len(p) == 3: disk.setdefault(p[0], []).append((int(p[1]), int(p[2])))
dones = sorted(os.path.getmtime(x) for x in glob.glob(R + "/out/run-*/done"))
grid = (dones[-1] - t0) if dones else 0.0
tail = (dones[-1] - dones[-2]) if len(dones) > 1 else 0.0
sm = [l.split() for l in open(R + "/samples.txt") if len(l.split()) > 3]
act = [int(x[1]) for x in sm]
units = {}
for l in open(R + "/out/units.txt") if os.path.exists(R + "/out/units.txt") else []:
    p = l.split()
    if len(p) >= 3: units[p[0]] = int(p[2].split("=")[1])
print(f"pass {lab} bin {binp} rc {rc}")
print(f"wall_total_s {t1 - t0:.1f}  wall_grid_s {grid:.1f}  tail_last_alone_s {tail:.1f}  days_done {len(dones)}")
print(f"cpu_pct user {100 * (d[0] + d[1]) / tot:.1f} sys {100 * d[2] / tot:.1f} iowait {100 * d[4] / tot:.1f} idle {100 * d[3] / tot:.1f}")
print(f"slots_active mean {sum(act) / max(len(act), 1):.1f} max {max(act) if act else 0} (из NX={os.environ.get('NX', '12')})")
print(f"sum_grid_s {sum(units.values())}  slot_occupancy_pct {100 * sum(units.values()) / max(grid * int(os.environ.get('NX', '12')), 1):.1f}")
for k, v in sorted(disk.items()):
    if len(v) == 2: print(f"disk_{k} read_gb {(v[1][0] - v[0][0]) * 512 / 1e9:.1f} write_gb {(v[1][1] - v[0][1]) * 512 / 1e9:.2f}")
print(f"diff_vs_ref_lines {df}")
PY
  cat "$R/metrics.txt"
}

case $mode in
  prep) prep;;
  pass) prep; pass "$@";;
  abba) prep; for l in $3; do pass pass "$D" "$l" 2>&1 | tail -12; done; touch "$D/abba.done";;
  *) echo "prep|pass|abba" >&2; exit 2;;
esac
