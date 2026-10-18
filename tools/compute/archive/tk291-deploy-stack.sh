#!/usr/bin/env bash
# TK-291: строка «стопка» П-19 (Судья P-19-load-check) -> /data/launch/registry дописью + routes.json (хеш -> noline: К счёту не растёт), бэкап /data/launch/prev-tk291e;
#   /api/letopis?hyp=М-04 не 200 / строк < 72 / нет mwhy у стопки -> автооткат (tk291-rollback.py + routes.json из бэкапа) + перезапуск.
set -u
D=/data/tk0291/deploy-stack; R=/data/launch/registry; P=/data/launch/prev-tk291e; RT=/opt/alpha-launch/tools/launch/routes.json
T=$(cat /etc/alpha-launch/token); B=http://127.0.0.1:8791/$T; H=$(cat $D/stack.hash)
grep -q '"p19/stack"' $R/cells.jsonl && { echo "стопка уже в cells"; exit 4; }
mkdir -p $P/registry /tmp/tk291m && cp -a $R/. $P/registry/ && cp -a $RT $P/routes.json
for b in cells run_cells plan_cells metrics_why; do
  [ -z "$(tail -c1 $R/$b.jsonl)" ] || echo >> $R/$b.jsonl
  cat $D/add-$b.jsonl >> $R/$b.jsonl
done
python3 - <<PY
import json
t = open("$RT", encoding="utf-8").read()
c = json.loads(t)
assert json.dumps(c, ensure_ascii=False) == t.rstrip("\n"), "формат routes.json не round-trip"
assert "$H" not in c["routes"]
c["routes"]["$H"] = "noline"
open("$RT", "w", encoding="utf-8", newline="").write(json.dumps(c, ensure_ascii=False) + ("\n" if t.endswith("\n") else ""))
PY
systemctl restart alpha-launch; sleep 4
code=$(curl -sS -m 150 -G --data-urlencode "hyp=М-04" "$B/api/letopis" -o /tmp/tk291m/let-m04c.json -w "%{http_code}" 2>&1 | tail -1)
n=$(H=$H python3 -c "import json,os;d=json.load(open('/tmp/tk291m/let-m04c.json',encoding='utf-8'));print(len([r for r in d['rows'] if r['hash']]), sum(1 for r in d['rows'] if r['hash']==os.environ['H'] and r['mwhy']))" 2>/dev/null || echo "0 0")
if [ "$code" != "200" ] || [ "${n% *}" -lt 72 ] || [ "${n#* }" != "1" ]; then echo "letopis $code строк/mwhy $n -> откат"; python3 /data/tk0291/tk291-rollback.py $D $R; cp -a $P/routes.json $RT; systemctl restart alpha-launch; exit 5; fi
echo "letopis 200, строк М-04 / mwhy у стопки: $n"
