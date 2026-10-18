#!/usr/bin/env bash
# TK-291: дельта ворот 0 (3 честные клетки lot_first, авг+сен) -> /data/launch/registry дописью (бэкап /data/launch/prev-tk291d), перезапуск alpha-launch;
#   /api/letopis?hyp=М-04 не 200 или строк < 71 -> автооткат (tk291-rollback.py) + перезапуск. Run R-20261017-010 уже в runs.jsonl.
set -u
D=/data/tk0291/deploy-lf; R=/data/launch/registry; P=/data/launch/prev-tk291d; T=$(cat /etc/alpha-launch/token); B=http://127.0.0.1:8791/$T
grep -q '"p19/lf' $R/cells.jsonl && { echo "ворота 0 уже в cells"; exit 4; }
mkdir -p $P/registry /tmp/tk291m && cp -a $R/. $P/registry/
: > $D/add-runs.jsonl
for b in cells run_cells results metrics plan_cells; do
  [ -z "$(tail -c1 $R/$b.jsonl)" ] || echo >> $R/$b.jsonl
  cat $D/add-$b.jsonl >> $R/$b.jsonl
done
systemctl restart alpha-launch; sleep 4
code=$(curl -sS -m 150 -G --data-urlencode "hyp=М-04" "$B/api/letopis" -o /tmp/tk291m/let-m04b.json -w "%{http_code}" 2>&1 | tail -1)
n=$(python3 -c "import json;d=json.load(open('/tmp/tk291m/let-m04b.json',encoding='utf-8'));print(len([r for r in d['rows'] if r['hash']]))" 2>/dev/null || echo 0)
if [ "$code" != "200" ] || [ "$n" -lt 71 ]; then echo "letopis $code строк $n -> откат"; python3 /data/tk0291/tk291-rollback.py $D $R; systemctl restart alpha-launch; exit 5; fi
echo "letopis 200, строк М-04: $n"
