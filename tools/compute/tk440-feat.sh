#!/bin/bash
# TK-440 шаг 5: признаки v11 (v_in/v_pre, sf_corr, oi_in и др.) на кубах янв–июль: выборка ≤600/ячейку (сид 433) -> контроли ±30 -> tk422-features (куб свечей, без куба сделок, OI/фандинг Bybit tk428) -> merge.
# Метка feat.done, код feat.rc; по месяцу feat/f-ММ/features-2026-ММ.csv. Готовые месяцы пропускаются.
#   alsched submit --kind prod --name tk440-feat --cores 15 --max-runtime 150m --est 60m -- bash /data/tk0440/tk440-feat.sh
cd /data/tk0440 || exit 9
PY=/data/tk421/venv/bin/python3
rm -f feat.done feat.rc; mkdir -p feat
for M in 01 02 03 04 05 06 07; do
  O=feat/f-$M
  [ -s $O/features-2026-$M.csv ] && continue
  mkdir -p $O
  CUT=$(date -u -d "2026-$M-01 -1 month" +%s); CUT=$((CUT/60))
  FROM=$(date -u -d "2026-$M-01 -1 month" +%Y-%m-%dT00:00)
  $PY /data/tk0440/tk440-sample.py ev$M/windows-2026-$M.csv $O/legs.csv /data/tk0440 > $O/sample.log 2>&1 || { echo "$M s" > feat.rc; exit 1; }
  $PY /data/tk0436/tk433-features.py windows $O/legs.csv $O/legs.csv - - $O 2026-$M --cube cube$M --cut $CUT --jobs 15 > $O/windows.log 2>&1 || { echo "$M w" > feat.rc; exit 2; }
  $PY /data/tk0439/tk422-features-seq.py $O/windows.csv $O/tk422-features.csv --procs 15 --aug-candles /srv/alpha-sda/candles-1m-aug --tk415 /srv/alpha-sda/tk428/bybit-oi --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube cube$M --cube-from $FROM > $O/feat.log 2> $O/feat.err || { echo "$M f" > feat.rc; exit 3; }
  $PY /data/tk0436/tk433-features.py merge $O/windows.csv $O/own.csv $O/tk422-features.csv $O/features-2026-$M.csv > $O/merge.log 2>&1 || { echo "$M m" > feat.rc; exit 4; }
done
echo 0 > feat.rc; touch feat.done
