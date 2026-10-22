#!/usr/bin/env bash
# TK-303: дельта F2 (B + R1 + первая подача) -> /data/launch/registry дописью, патч вкладки «Гипотезы» (hyps.py/launch.py/page.html) -> /opt/alpha-launch,
#   перезапуск alpha-launch, проверка /api/letopis и /api/hyp; не 200 -> откат из /data/launch/prev-tk303 + перезапуск.
# Ожидает в /data/tk0303: deploy/add-*.jsonl, deploy/tk303-launch.patch, tk303-deploy-delta.py.
set -u
D=/data/tk0303/deploy; R=/data/launch/registry; P=/data/launch/prev-tk303; L=/opt/alpha-launch/tools/launch
T=$(cat /etc/alpha-launch/token); B=http://127.0.0.1:8791/$T
[ -e $P/registry ] && { echo "prev-tk303 уже есть — повторный деплой? остановка"; exit 4; }
mkdir -p $P/registry $P/launch /tmp/tk303m
cp -a $R/. $P/registry/
cp -a $L/hyps.py $L/launch.py $L/page.html $P/launch/
(cd /opt/alpha-launch && patch -p1 < $D/tk303-launch.patch) || { echo "patch не лёг"; cp -a $P/launch/. $L/; exit 5; }
python3 /data/tk0303/tk303-deploy-delta.py --apply $D
systemctl restart alpha-launch; sleep 5
ok=1
for ep in api/letopis api/hyp; do
  code=$(curl -sS -m 150 "$B/$ep" -o /tmp/tk303m/$(basename $ep).json -w "%{http_code}" 2>&1 | tail -1)
  echo "$ep $code $(wc -c < /tmp/tk303m/$(basename $ep).json)"
  [ "$code" = "200" ] || ok=0
done
if [ $ok = 0 ]; then
  echo "не 200 -> откат"
  cp -a $P/registry/. $R/; cp -a $P/launch/. $L/; systemctl restart alpha-launch; exit 6
fi
for b in cells run_cells results runs sieve plan_cells; do echo $b $(wc -l < $R/$b.jsonl); done
