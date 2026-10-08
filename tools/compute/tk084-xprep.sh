#!/bin/bash
# tk084-xprep.sh: вход счёта b14 для XAUUSDT/CLUSDT (401 сутки, мар…окт) в отдельном корне /data/tk084/x (боевые дома /data/tk046 не трогаем).
# Копии скриптов TK-046 с подменой корня и вердикта; D20 + свечи/σ + дом по месяцам (PREP_ONLY). Режим из пула — копия regime/ боевого дома месяца
# (ход пула без XAU/CL — 2 из ~100 монет; приближение, отмечено в тикете). Запуск: alsched submit --cls prod ... -- bash /data/tk084/x/xprep.sh
X=/data/tk084/x; B=$X/bin; V=/data/tk084/v171c/verdict-xaucl.csv; MONTHS=${MONTHS:-mar apr may jun jul aug sep oct}
mkdir -p $B $X/verify /data/progress
for s in XAUUSDT CLUSDT; do echo ok > $X/verify/verify-$s.status; done
for f in tk046-mnew.py tk046-derive.sh tk046-ap1.sh tk046-klines.sh tk046-day.sh tk046-month.sh tk046-home.py; do
  sed -e 's#/data/tk046#/data/tk084/x#g' -e "s#/data/tk044/final3/verdict.csv#$V#g" -e 's#/data/tk037/vroots/e-#/data/tk037/roots/e-#g' \
      -e 's#/opt/alpha-compute/bin/tk046-ap1.sh#/data/tk084/x/bin/tk046-ap1.sh#' -e 's#^MON=\$1; export MON;#MON=$1; export MON;#' \
      -e 's#^MON=\$1; export MON; YM=\(.*\); B=/opt/alpha-compute/bin;#MON=$1; export MON; YM=\1; B=/data/tk084/x/bin;#' \
      -e 's#cp -n /data/alpha/root/instruments.csv#cp -n /data/tk037/instruments.csv#' /opt/alpha-compute/bin/$f > $B/$f
done
python3 - <<'PY'
import re
p="/data/tk084/x/bin/tk046-home.py"; t=open(p).read()
t=t.replace('v = f"{src_dir}/verify-{s}.status"','v = f"/data/tk084/x/verify/verify-{s}.status"')
a=t.index('for ref in ("BTCUSDT"'); b=t.index('print("дней:"')
t=t[:a]+'shutil.copytree(f"/data/tk046/{MON}/home/alpha/epochs/e-{MON}/study/regime", f"{E}/study/regime", dirs_exist_ok=True)\n'+t[b:]
open(p,"w").write(t)
PY
chmod +x $B/*
for m in $MONTHS; do
  [ -e $X/$m/.prep_done ] && continue
  PREP_ONLY=1 P=8 bash $B/tk046-month.sh $m || { echo "$m prep FAIL rc $?" >> $X/xprep.err; exit 1; }
done
touch $X/xprep.done
