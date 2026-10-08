#!/bin/bash
# tk048-tbin-gate.sh: гейт и пара скорости ALPHA_TOUCH_BIN на сутках 2026-01-01 (бинарник alpha-b16tbin, env как alpha-b14flag).
# Порядок: B (флаг, холодно: пишет touches .abin + сверка) → A (флаг выкл.) и C (флаг, тепло) одновременно по 400 %.
# Выход /data/tk048/tbin-gate/<A|B|C>/{metrics.txt,gate.txt}; маркер /data/tk048/tbin-gate/done.
D=${D:-2026-01-01}; MON=jan; BIN=${BIN:-alpha-b16tbin}
H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; O=/data/tk048/tbin-gate; AB=/data/tk048/abin-t46m
mkdir -p $O
run() { # tag touchbin(0|1)
  tag=$1; tb=$2; S=/data/tk048/tbin-gate-sb-$tag; R=$O/$tag
  rm -rf $S $R; mkdir -p $S/b5 $R $S/bin
  for x in $E/* $E/.[!.]*; do b=$(basename $x); case $b in b5|b5-solo|.day-*) continue;; esac; [ -e "$x" ] && ln -s $x $S/$b; done
  { echo '#!/bin/bash'
    echo 'export ALPHA_SKIP_NOSIGNAL=1 ALPHA_FAST_HOLD=1 ALPHA_EVENT_STEPS=entry ALPHA_SIG_CACHE=3000000 ALPHA_APPROACH_BIN=1 ALPHA_ADMIT_CACHE=1 ALPHA_HOLDS_MEMO=1 ALPHA_ADMIT_SOA=1 ALPHA_BAND_COUNT_OFF=1'
    echo "export ALPHA_APPROACH_BIN_DIR=$AB"
    [ "$tb" = 1 ] && echo 'export ALPHA_TOUCH_BIN=1'
    echo "exec /opt/alpha-compute/bin/$BIN \"\$@\""; } > $S/bin/wrap.sh
  chmod +x $S/bin/wrap.sh; ln -s wrap.sh $S/bin/alpha-tk044k1-new
  s=$(date +%s.%N)
  ( cd $S && export HOME=$H && systemd-run --pipe --wait --collect --quiet -p CPUQuota=400% --unit tk048-tbin-$tag -E HOME=$H --working-directory=$S bash -c "time bash $H/alpha/tmp-p07/cells-by-day/jall-$MON-$D.sh" > $R/run.out 2> $R/run.err )
  e=$(date +%s.%N); echo "wall_s $(echo "$e - $s" | bc)" > $R/metrics.txt; grep -E "^(real|user|sys)" $R/run.err >> $R/metrics.txt
  bad=0; n=0; cd $S/b5; while IFS= read -r f; do n=$((n+1)); cmp -s "$f" "$E/b5/$f" || { bad=$((bad+1)); echo "DIFF $f" >> $R/gate.txt; }; done < <(find . -type f -path "*$D*" | grep -v "/\.")
  echo "gate files $n diff $bad" >> $R/metrics.txt
  ls -l $AB 2>/dev/null | grep -c "touches.*abin" >> $R/metrics.txt
}
run B 1
run A 0 &
run C 1 &
wait
touch $O/done
