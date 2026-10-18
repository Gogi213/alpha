#!/usr/bin/env bash
# TK-291: дельта 68 клеток П-19 -> /data/launch/registry дописью (бэкап /data/launch/prev-tk291b), перезапуск alpha-launch, проверка /api/letopis;
#   не 200 -> автоотков (tk291-rollback.py: удаляет только мои строки) + перезапуск.
set -u
D=/data/tk0291/deploy; R=/data/launch/registry; P=/data/launch/prev-tk291b; T=$(cat /etc/alpha-launch/token); B=http://127.0.0.1:8791/$T
grep -q '"id": "М-04"' $R/hypotheses.jsonl || { echo "нет М-04 в hypotheses"; exit 3; }
grep -q '"id": "R-20261017-010"' $R/runs.jsonl && { echo "run 010 уже есть"; exit 4; }
mkdir -p $P/registry && cp -a $R/. $P/registry/
for b in cells run_cells results metrics runs; do
  [ -z "$(tail -c1 $R/$b.jsonl)" ] || echo >> $R/$b.jsonl
  cat $D/add-$b.jsonl >> $R/$b.jsonl
done
systemctl restart alpha-launch; sleep 4
code=$(curl -sS -m 150 "$B/api/letopis" -o /tmp/tk291m/let-all.json -w "%{http_code}" 2>&1 | tail -1)
if [ "$code" != "200" ]; then echo "letopis $code -> откат"; python3 /data/tk0291/tk291-rollback.py $D $R; systemctl restart alpha-launch; exit 5; fi
echo "letopis 200 $(wc -c < /tmp/tk291m/let-all.json)"
for b in cells run_cells results metrics runs; do echo $b $(wc -l < $R/$b.jsonl); done
