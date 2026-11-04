set -eu
H=$HOME/alpha-pump; U=$H/universe
ls $H/candles/bybit-linear | sed 's/\.csv\.gz$//' | sort > $U/done-bybit-candles.txt
awk -v d="$U/done-bybit-candles.txt" 'BEGIN{while((getline l<d)>0)D[l]=1} {i=NR-1; if(i%10<7){print > "'$U'/sofia-oi.txt"; if(!($0 in D)) print > "'$U'/sofia-cand.txt"} else {print > "'$U'/deck-oi.txt"; if(!($0 in D)) print > "'$U'/deck-cand.txt"}}' $U/symbols-bybit-linear.txt
for k in sofia-oi sofia-cand deck-oi deck-cand; do
  awk 'NR%2==1' $U/$k.txt > $U/$k-a.txt; awk 'NR%2==0' $U/$k.txt > $U/$k-b.txt
done
wc -l $U/done-bybit-candles.txt $U/sofia-*.txt $U/deck-*.txt
