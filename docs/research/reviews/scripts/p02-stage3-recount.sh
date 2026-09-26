#!/usr/bin/env bash
# Судья: независимый пересчёт П-02 третьей очереди. Г-36 — из кэша D20 (причинный флаг §12), Г-46 — из компактных flow.
cd ~/alpha
K1_BAD="FFUSDT POWERUSDT SKHYNIXUSDT VVVUSDT"
for base in epochs/e-aug/study/approaches/D20 epochs/e-archive/study/approaches/D20 study/approaches/D20; do
  for dd in "$base"/2026-0[89]-*; do
    day=$(basename "$dd"); [[ "$day" > "2026-09-23" ]] && continue
    awk -F, -v day="$day" -v bad="$K1_BAD" '
      BEGIN { n=split(bad,bb," "); for(i=1;i<=n;i++) K1[bb[i]]=1 }
      FNR==1 { for(i=1;i<=NF;i++) c[$i]=i
               sym=FILENAME; sub(/.*touches-/,"",sym); sub(/\.csv$/,"",sym)
               skip = (sym=="TRXUSDT") || ((sym in K1) && day>="2026-09-16" && day<="2026-09-19")
               delete cum; delete lastk; next }
      skip { next }
      { key=$c["side"] SUBSEP $c["price_tick"] SUBSEP $c["birth_ms"]; k=$c["touch_index"]+0
        if ((key in lastk) && k <= lastk[key]) viol++
        lastk[key]=k
        b = ($c["ended_by_death"]=="false") ? 1 : 0
        if (k>=1) { f = ((cum[key]+0) >= ($c["size_max_before"]+0)) ? 1 : 0
                    kk = (k>=3)?3:k
                    nb[f,kk]+=b; nt[f,kk]++ }
        cum[key] += $c["traded_during"] }
      END { printf "G36 %s", day; for(f=0;f<=1;f++) for(kk=1;kk<=3;kk++) printf " %d %d", nb[f,kk], nt[f,kk]; printf " viol=%d\n", viol }' "$dd"/touches-*.csv
  done
done
for d in epochs/e-aug/study/p02e/flow epochs/e-archive/study/p02e/flow study/p02e/flow; do
  for f in "$d"/2026-0[89]-*.csv.gz; do
    day=$(basename "$f" .csv.gz); [[ "$day" > "2026-09-23" ]] && continue
    zcat "$f" | awk -F, -v day="$day" 'NR==1{for(i=1;i<=NF;i++)c[$i]=i; next}
      $c["symbol"]=="TRXUSDT"{next}
      { b=($c["ended_by_death"]=="false")?1:0; m=$c["mismatch60"]; n++
        if(m=="1"){b1+=b;t1++} else if(m=="0"){b0+=b;t0++} }
      END{printf "G46 %s %d %d %d %d %d\n", day, b1,t1,b0,t0,n}'
  done
done
