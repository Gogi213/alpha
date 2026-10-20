#!/usr/bin/env bash
# TK-303 (20.10): метки плато сита на F1∪F2∪B∪R1 (замена 20 строк sieve по ключу) + счётчик F2 и подвал вкладки «Гипотезы» (патч hyps/launch/page).
# Ожидает в /data/tk0303/deploy2: sieve-full.jsonl, tk303-launch2.patch, tk303-deploy-delta.py. Не 200 -> откат из /data/launch/prev-tk303b.
set -u
D=/data/tk0303/deploy2; R=/data/launch/registry; P=/data/launch/prev-tk303b; L=/opt/alpha-launch/tools/launch
T=$(cat /etc/alpha-launch/token); B=http://127.0.0.1:8791/$T
[ -e $P/launch ] && { echo "prev-tk303b уже есть — повторный деплой? остановка"; exit 4; }
mkdir -p $P/registry $P/launch /tmp/tk303m
cp -a $L/hyps.py $L/launch.py $L/page.html $P/launch/
(cd /opt/alpha-launch && patch -p1 --dry-run < $D/tk303-launch2.patch) || { echo "patch не ложится"; exit 5; }
(cd /opt/alpha-launch && patch -p1 < $D/tk303-launch2.patch) || { cp -a $P/launch/. $L/; exit 5; }
python3 $D/tk303-deploy-delta.py --sieve $D
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
echo sieve $(wc -l < $R/sieve.jsonl)
