#!/bin/bash
# TK-190: psim R2-B за авг и сен с 3 досчитанными символо-сутками (ADAUSDT/GRAMUSDT 08-31, STXUSDT 09-14; выход /data/tk0190/fix) —
# строки rounds.csv/signals.csv символо-суток дописаны к выходу суток R2-B (busy-replay и psim считают по символам независимо),
# затем как tk120-r2b-psim.sh (busy-replay, cap 0 и 3, drop TRUMP/TRX/BCH) -> /data/p12r3a/closes-r2bf-cap<N>-<мес>.json. Метка psim-r2bf.done.
set -u
O=/data/p12r3a; R=$O/r2b; F=/data/tk0190/fix; TB=/data/tk083/tools; T=/data/tk0113/tools; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT; S=t-bid-btc4h-q1
cd $O || exit 2; rm -f psim-r2bf.done r2bf-fail.txt; rm -rf s-rf r-bf
for mo in 2026-08 2026-09; do mkdir -p s-rf/$mo
  for d in $(ls s-r/$mo); do cp -rs $R/$d/out/$d s-rf/$mo/; done
done
python3 - <<'PY' || { echo FAILMERGE >> r2bf-fail.txt; exit 3; }
import os, shutil
O = "/data/p12r3a"; F = "/data/tk0190/fix"; S = "t-bid-btc4h-q1"
for d, syms in (("2026-08-31", ("ADAUSDT", "GRAMUSDT")), ("2026-09-14", ("STXUSDT",))):
    dst = f"{O}/s-rf/{d[:7]}/{d}"; src = f"{O}/r2b/{d}/out/{d}"
    os.unlink(dst); shutil.copytree(src, dst, symlinks=False)
    for fn in ("rounds.csv", "signals.csv"):
        p = f"{dst}/{S}/{fn}"; hdr = [l for l in open(p, encoding="utf-8") if not l.startswith("#")][0]
        with open(p, "a", encoding="utf-8", newline="") as out:
            for s in syms:
                lines = [l for l in open(f"{F}/{d}-{s}/out/{d}/{S}/{fn}", encoding="utf-8") if not l.startswith("#")]
                assert lines[0] == hdr, (d, s, fn)
                out.writelines(lines[1:])
PY
for mo in 2026-08 2026-09; do
  python3 $TB/busy-replay.py s-rf/$mo r-bf/$mo > busy-rf-$mo.log 2>&1 || { echo "FAILBUSY $mo" >> r2bf-fail.txt; continue; }
  d1=$(ls s-rf/$mo | head -1); args=()
  while read f s; do args+=(--variant "$s@$f=$s/$f"); done < $R/$d1/cells.txt
  for cap in 0 3; do
    python3 $T/portfolio-sim.py --epoch "$mo=$O/r-bf:$mo" "${args[@]}" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos $cap \
      --closes-out closes-r2bf-cap$cap-$mo.json > psim-r2bf-cap$cap-$mo.txt 2> psim-r2bf-cap$cap-$mo.log || echo "FAIL r2bf $cap $mo" >> r2bf-fail.txt
  done
done
touch psim-r2bf.done
