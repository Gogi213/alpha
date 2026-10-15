T=$(cat /etc/alpha-launch/token); B=http://127.0.0.1:8791/$T
src=$(dirname $(ls /data/launch/out/L-*/run.json | head -1)); id=L-20261015-059$RANDOM; id=${id:0:17}
curl -s -o /dev/null $B/api/runs; curl -s -o /dev/null $B/api/status   # тёплые
s=$(date +%s.%N)   # события — от этой метки; mkdir идёт через 0,3 с (вычесть)
(curl -sN --max-time 20 $B/api/events | while read l; do echo "  $(echo "$(date +%s.%N) - $s" | bc) $l"; done) &
sleep 0.3
s2=$(date +%s.%N); mkdir /data/launch/out/$id && cp $src/run.json /data/launch/out/$id/run.json
sed -i "s/\"id\": *\"[^\"]*\"/\"id\": \"$id\"/" /data/launch/out/$id/run.json 2>/dev/null
n=0
while ! curl -s $B/api/runs | grep -q "$id"; do sleep 0.05; n=$((n+1)); [ $n -gt 600 ] && break; done
echo "событие → в /api/runs: $(echo "$(date +%s.%N) - $s2" | bc) с (id $id, из $(basename $src))"
wait; rm -r /data/launch/out/$id; ls /data/launch/requests | wc -l
