#!/bin/bash
# TK-133 С-46/С-47/С-50: единственная выкладка планировщика и замка замеров на calc: файлы из git (HEAD) → /data/sched/, md5 после копии,
# /data/benchrun.sh -> /data/sched/benchrun-sched.sh, бэкапы — в /data/sched/archive/, /data/sched/VERSION (коммит + md5 каждого файла).
# Запуск с ПК из корня репо: bash tools/compute/sched-deploy.sh [--restart]   (--restart: перезапуск демона, только при пустой очереди)
# Отказ: файл манифеста изменён и не закоммичен. Окно: очередь пуста и нет идущей волны (alsched.py ps).
set -eu -o pipefail
HOST=${CALC_HOST:-root@89.163.242.211}
SSH=(ssh -n -i "$HOME/.ssh/id_rsa" -o "UserKnownHostsFile=$HOME/.ssh/known_hosts" -o BatchMode=yes "$HOST")
SSH_IN=(ssh -i "$HOME/.ssh/id_rsa" -o "UserKnownHostsFile=$HOME/.ssh/known_hosts" -o BatchMode=yes "$HOST")   # без -n: stdin = файл из git
MANIFEST="alsched.py:alsched.py
sched_cfg.py:sched_cfg.py
benchrun-sched.sh:benchrun-sched.sh
benchrun-inner.sh:benchrun-inner.sh
benchrun-legacy.sh:benchrun-legacy.sh
benchrun2-legacy.sh:benchrun2-legacy.sh
alsched-jobowners.sh:jobowners.sh
alsched-jobstate.sh:jobstate.sh"
cd "$(git rev-parse --show-toplevel)"
sha=$(git rev-parse --short HEAD)
ver="commit $sha $(date +%FT%T%z)"
while IFS=: read -r src dst; do
  if ! git diff --quiet HEAD -- "tools/compute/$src"; then echo "sched-deploy: tools/compute/$src не закоммичен" >&2; exit 2; fi
done <<<"$MANIFEST"
"${SSH[@]}" 'busy=$(python3 /data/sched/alsched.py ps | grep -Ec "running|занято [1-9]|в очереди [1-9]" || true); [ "$busy" = 0 ] || { echo "sched-deploy: очередь/волна не пусты — окно не то" >&2; exit 3; }; mkdir -p /data/sched/archive'
while IFS=: read -r src dst; do
  want=$(git show "HEAD:tools/compute/$src" | md5sum | cut -d' ' -f1)
  have=$("${SSH[@]}" "md5sum /data/sched/$dst 2>/dev/null | cut -d' ' -f1" || true)
  if [ "$want" != "$have" ]; then
    git show "HEAD:tools/compute/$src" | "${SSH_IN[@]}" "cat > /data/sched/$dst.new && chmod 755 /data/sched/$dst.new && { [ ! -e /data/sched/$dst ] || cp -p /data/sched/$dst /data/sched/archive/$dst.$(date +%y%m%d-%H%M%S); } && mv /data/sched/$dst.new /data/sched/$dst"
  fi
  got=$("${SSH[@]}" "md5sum /data/sched/$dst | cut -d' ' -f1")
  [ "$want" = "$got" ] || { echo "sched-deploy: md5 $dst: ждали $want, на calc $got" >&2; exit 4; }
  echo "$got  $dst  <- tools/compute/$src"
done <<<"$MANIFEST" >/tmp/sched-deploy-md5.txt
cat /tmp/sched-deploy-md5.txt
"${SSH[@]}" "ln -sfn /data/sched/benchrun-sched.sh /data/benchrun.sh; for f in /data/sched/alsched.py.bak-* /data/sched/alsched.py.pre-*; do [ -e \"\$f\" ] && mv \"\$f\" /data/sched/archive/; done; true"
{ echo "$ver"; cat /tmp/sched-deploy-md5.txt; } | "${SSH_IN[@]}" 'cat > /data/sched/VERSION'
if [ "${1:-}" = --restart ]; then "${SSH[@]}" 'systemctl restart alpha-sched; sleep 5; systemctl is-active alpha-sched'; fi
echo "sched-deploy: ок, $ver"
