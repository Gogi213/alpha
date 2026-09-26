#!/usr/bin/env bash
# Судья: расстояние фронтрана от стены (тики) у бид-касаний возраста >= 45 мин, кэш D20, без исходов
cd ~/alpha
for base in epochs/e-aug/study/approaches/D20 epochs/e-archive/study/approaches/D20 study/approaches/D20; do
  for dd in "$base"/2026-0[89]-*; do
    day=$(basename "$dd"); [[ "$day" > "2026-09-23" ]] && continue
    awk -F, -v day="$day" '
      FNR==1 { for(i=1;i<=NF;i++){ if($i=="side")s=i; if($i=="age_ms")a=i; if($i=="price_tick")p=i; if($i=="frontrun_tick")f=i }
               trx = (FILENAME ~ /TRXUSDT/); next }
      trx { next }
      $s=="bid" && $a+0 >= 2700000 { n++; if($f==""){nf++} else { d=$f-$p; if(d==1)d1++; else if(d>=2)d2++; else bad++ } }
      END { printf "%s %d %d %d %d %d\n", day, n, nf, d1, d2, bad }' "$dd"/touches-*.csv
  done
done
