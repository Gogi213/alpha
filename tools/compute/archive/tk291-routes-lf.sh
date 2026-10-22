#!/usr/bin/env bash
# TK-291: 3 честные клетки ворот 0 (p19/lf*, считались только авг+сен) -> routes.json: noline («нет кодировки»), иначе панель считает их К счёту на остальные 7 мес.
#   бэкап routes.json /data/launch/prev-tk291f; /api/letopis?hyp=М-04 не 200 или строк < 72 -> откат.
set -u
D=/data/tk0291/deploy-lf; RT=/opt/alpha-launch/tools/launch/routes.json; P=/data/launch/prev-tk291f
T=$(cat /etc/alpha-launch/token); B=http://127.0.0.1:8791/$T
mkdir -p $P && cp -a $RT $P/routes.json
python3 - <<PY
import json
t = open("$RT", encoding="utf-8").read()
c = json.loads(t)
assert json.dumps(c, ensure_ascii=False) == t.rstrip("\n"), "формат routes.json не round-trip"
hs = [json.loads(l)["cell_hash"] for l in open("$D/add-plan_cells.jsonl", encoding="utf-8") if l.strip()]
assert len(hs) == 3 and not any(h in c["routes"] for h in hs)
for h in hs:
    c["routes"][h] = "noline"
open("$RT", "w", encoding="utf-8", newline="").write(json.dumps(c, ensure_ascii=False) + ("\n" if t.endswith("\n") else ""))
print("routes +", len(hs))
PY
systemctl restart alpha-launch; sleep 4
code=$(curl -sS -m 150 -G --data-urlencode "hyp=М-04" "$B/api/letopis" -o /tmp/tk291m/let-m04d.json -w "%{http_code}" 2>&1 | tail -1)
n=$(python3 -c "import json;d=json.load(open('/tmp/tk291m/let-m04d.json',encoding='utf-8'));print(len([r for r in d['rows'] if r['hash']]))" 2>/dev/null || echo 0)
if [ "$code" != "200" ] || [ "$n" -lt 72 ]; then echo "letopis $code строк $n -> откат"; cp -a $P/routes.json $RT; systemctl restart alpha-launch; exit 5; fi
echo "letopis 200, строк М-04: $n"
