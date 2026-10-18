#!/usr/bin/env bash
# TK-291: строки сетки П-19 (plan_cells) + ["P-19",[1,2]] в config.json панели calc; бэкап /data/launch/prev-tk291c; не 200 / нет 68 строк -> откат.
set -u
D=/data/tk0291/deploy; R=/data/launch/registry; P=/data/launch/prev-tk291c; C=/opt/alpha-launch/tools/launch/config.json
T=$(cat /etc/alpha-launch/token); B=http://127.0.0.1:8791/$T
grep -q '"protocol": "P-19"' $R/plan_cells.jsonl && { echo "P-19 уже в plan_cells"; exit 4; }
mkdir -p $P/registry && cp -a $R/. $P/registry/ && cp -a $C $P/config.json
[ -z "$(tail -c1 $R/plan_cells.jsonl)" ] || echo >> $R/plan_cells.jsonl
cat $D/add-plan_cells.jsonl >> $R/plan_cells.jsonl
python3 - <<PY
import json
t = open("$C", encoding="utf-8").read()
c = json.loads(t)
assert json.dumps(c, indent=2, ensure_ascii=False) + ("\n" if t.endswith("\n") else "") == t, "формат config.json не round-trip"
c["protocols"].append(["P-19", [1, 2]])
open("$C", "w", encoding="utf-8", newline="").write(json.dumps(c, indent=2, ensure_ascii=False) + ("\n" if t.endswith("\n") else ""))
print("config protocols:", c["protocols"])
PY
systemctl restart alpha-launch; sleep 4
code=$(curl -sS -m 150 -G --data-urlencode "hyp=М-04" "$B/api/letopis" -o /tmp/tk291m/let-m04.json -w "%{http_code}" 2>&1 | tail -1)
n=$(python3 -c "import json;d=json.load(open('/tmp/tk291m/let-m04.json',encoding='utf-8'));print(len([r for r in d['rows'] if r['hash']]))" 2>/dev/null || echo 0)
if [ "$code" != "200" ] || [ "$n" -lt 68 ]; then echo "letopis $code строк $n -> откат"; python3 /data/tk0291/tk291-rollback.py $D $R; cp -a $P/config.json $C; systemctl restart alpha-launch; exit 5; fi
echo "letopis 200, строк М-04: $n"
