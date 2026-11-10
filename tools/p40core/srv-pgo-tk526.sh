#!/bin/bash
# TK-526 (Судья 11.11 пп. 1–4): (1) смок orch --cells/--pre-days на инструментированном ядре — он же обучающий прогон PGO (В-32); (2) PGO-сборка и гейт «байт в байт» PGO против не-PGO на 2 сут x 20 активов
# (без P40_RULE и с P40_RULE=a3 W=6ч k=3); (3) время ядра на пара-сутки на крупном активе (BTC, asset 244): не-PGO, PGO, PGO + 6 клеток. Запуск на srv: systemd-run --unit=p40-pgo526 bash <этот файл>; итог $O/summary.txt, маркер $O/PGO526_DONE.
export PATH=$PATH:/root/.cargo/bin
D=${D:-/root/p40core-tk526}; W=/tmp/pgo526; O=${O:-/root/p40pgo526}
LP=/root/.rustup/toolchains/stable-x86_64-unknown-linux-gnu/lib/rustlib/x86_64-unknown-linux-gnu/bin/llvm-profdata
PAIRS=/root/p40core/pairs_d1_v3_nobitget.csv
rm -rf $W $O; mkdir -p $O $W/cd; : > $O/summary.txt
cat > $O/cells.txt <<'EOC'
a2w1p95q05 a2,phi=20,w=3600,p=0.95 0.5
a2w24p99q0 a2,phi=29,w=86400,p=0.99 0
a3w6k3q05 a3,phi=20,w=21600,k=3 0.5
a3w24k5q025 a3,phi=29,w=86400,k=5 0.25
a4hl6p95q05 a4,phi=20,hl=21600,p=0.95 0.5
a5w24k3q05 a5,phi=20,w=86400,k=3 0.5
EOC
ASSETS8=$(python3 - "$PAIRS" <<'PY'
import csv, sys, collections
n = collections.Counter(r["asset"] for r in csv.DictReader(open(sys.argv[1])))
print(",".join([a for a, c in n.items() if c <= 2][:8]))
PY
)
cd $D/rs || exit 1
cargo build --release 2>&1 | tail -1; cp target/release/p40core $O/p40core-nopgo
RUSTFLAGS="-Cprofile-generate=$W/cd" cargo build --release --target-dir $W/cgen 2>&1 | tail -1
orch() {  # core out shm_frac args...
  core=$1; out=$2; frac=$3; shift 3
  P40_CORE=$core P40_L1=1 P40_NOTRADE=through P40_SHM_FRAC=$frac P40_L1_CACHE=/data/p40l1c nice -n 19 python3 $D/orch.py $PAIRS $out --jobs ${JOBS:-3} --l1-p 2 --box-conn 4 --age --l1-trades-raw --shm /dev/shm/p40pgo-$(basename $out) "$@" > $out.log 2>&1
}
# (1) смок клеток и предыстории: 4 суток, куски по 2, предыстория 1 сутки, общее множество с 18.09
LLVM_PROFILE_FILE=$W/cd/p-%p-%m.profraw orch $W/cgen/release/p40core $O/smoke 0.3 --days 2026-09-17,2026-09-18,2026-09-19,2026-09-20 --chunk 2 --pre-days 1 --ep-from 2026-09-18 --cells $O/cells.txt --assets $ASSETS8
echo "смок orch --cells --pre-days: rc=$?" >> $O/summary.txt
python3 - $O/smoke >> $O/summary.txt <<'PY'
import calendar, collections, csv, glob, os, re, sys, time
o = sys.argv[1]
day = lambda d: calendar.timegm(time.strptime(d, "%Y-%m-%d")) * 1000
cells = list(csv.DictReader(open(f"{o}/cells.csv")))
specs = sorted(glob.glob(f"{o}/specs_c_*.txt"))
bad, n, thetas = [], collections.Counter(), collections.defaultdict(set)
for f in glob.glob(f"{o}/*/*/*"):
    m = re.search(r"/(\d+)/([\d-]+\+?\d*)/(\d+)\.(c_\w+)$", f)
    if not m:
        continue
    cid, cell = m.group(2), m.group(4)
    lo = max(day("2026-09-18"), day(cid.split("+")[0]))
    for r in csv.DictReader(open(f)):
        n[cell] += 1
        thetas[cell].add(r["theta"])
        if int(r["t0_ms"]) < lo:
            bad.append((f, r["t0_ms"], lo))
print(f"клеток в cells.csv {len(cells)}, specs_c_* {len(specs)}; эпизодов по клеткам {dict(n)}; различных θ_t на клетку {({k: len(v) for k, v in thetas.items()})}; эпизодов раньше нижней границы суток куска/18.09: {len(bad)} {bad[:2]}")
PY
$LP merge -o $W/m.profdata $W/cd/*.profraw || { echo "profdata: сбой" >> $O/summary.txt; exit 1; }
RUSTFLAGS="-Cprofile-use=$W/m.profdata" cargo build --release --target-dir $W/cuse 2>&1 | tail -1; cp $W/cuse/release/p40core $O/p40core-pgo
# (2) гейт PGO против не-PGO
for v in norule rule; do
  if [ $v = rule ]; then export P40_RULE=a3,phi=20,w=21600,k=3; else unset P40_RULE; fi
  OLD=$O/p40core-nopgo NEW=$O/p40core-pgo O=$O/gate_$v SHM_FRAC=0.3 bash $D/srv-gate-tk526.sh > $O/gate_$v.out 2>&1
  n=$(grep -v -E 'orch\.csv|log$' $O/gate_$v/diff.txt | wc -l)
  echo "гейт PGO против не-PGO ($v): различий вне orch.csv: $n; $(grep 'файлов old' $O/gate_$v.out)" >> $O/summary.txt
done
unset P40_RULE
# (3) время на крупном активе (BTC, 244), сутки 17.09, без клеток: не-PGO и PGO; затем PGO с клетками
JOBS=1 orch $O/p40core-nopgo $O/big_nopgo 0.6 --days 2026-09-17 --assets 244
JOBS=1 orch $O/p40core-pgo $O/big_pgo 0.6 --days 2026-09-17 --assets 244
JOBS=1 orch $O/p40core-pgo $O/big_pgo_cells 0.6 --days 2026-09-17 --assets 244 --cells $O/cells.txt
for v in nopgo pgo pgo_cells; do echo "BTC 17.09 $v: $(tail -1 $O/big_$v/orch.csv) [asset,day,pairs,legs,bytes,fetch,convert,convert_cpu,core_s,core_cpu_s,aux_cpu_s,total]" >> $O/summary.txt; done
date > $O/PGO526_DONE
