#!/usr/bin/env bash
# TK-023: мощная почасовая машина Hetzner рядом с ящиком на время прогона TK-022 (янв–июн). Дом = путь деки
# (/home/deck/alpha, как на VPS), чтобы сценарии суток tmp-p07/cells-by-day/*.sh шли без правок.
# Секреты — только из окружения: HCLOUD_TOKEN (API-токен проекта Hetzner, Read&Write; вводит владелец).
#   fast-up.sh [--dry-run] up                       создать машину (ключ ssh и сервер), адрес → $STATE
#   fast-up.sh [--dry-run] provision                пакеты, бинарники, скрипты, ключ ящика, sshfs ro, regime/sigma/D20
#   fast-up.sh [--dry-run] speedtest                параллельное чтение ящика, МБ/с (решает число подкачек)
#   fast-up.sh [--dry-run] run <файл-суток> [lanes] сутки "<эпоха> <YYYY-MM-DD>" по строкам; сценарий $SCEN_DIR/$SCEN-<сутки>.sh
#   fast-up.sh [--dry-run] collect                  итоги b5/*/<сутки> → ящик alpha/derived/fast/out/<сутки>.tar
#   fast-up.sh [--dry-run] gate <сутки> <дом-VPS>   sha256 всех файлов суток здесь и на VPS, расхождения → rc=1
#   FAST_CONFIRM=yes fast-up.sh down                удалить машину и ключ (иначе отказ)
set -uo pipefail
DRY=0; [ "${1:-}" = "--dry-run" ] && { DRY=1; shift; }
CMD="${1:?up|provision|speedtest|run|collect|gate|down}"; shift || true

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE="${STATE:-$ROOT/data/fast-server.state}"          # IP=… ID=… KEYID=…
TYPE="${SERVER_TYPE:-ccx63}"; LOC="${LOCATION:-fsn1}"; NAME="${SERVER_NAME:-alpha-fast}"
PUB="${SSH_PUB:-$HOME/.ssh/id_rsa.pub}"; PRIV="${SSH_PRIV:-${PUB%.pub}}"
BOX="${BOX:-u677479@u677479.your-storagebox.de}"; BOXKEY="${BOXKEY:-$HOME/.ssh/id_storagebox}"
BINS="${BINS:-alpha-e74f200-v3}"                         # бинарники через пробел, берутся с VPS /opt/alpha-compute/bin
VPS="${VPS:-root@13.140.29.171}"
SCEN_DIR="${SCEN_DIR:-/home/deck/alpha/tmp-p07/cells-by-day}"; SCEN="${SCEN:-jall-jul}"
A=/home/deck/alpha
API=https://api.hetzner.cloud/v1
SSHO=(-i "$PRIV" -o UserKnownHostsFile="$HOME/.ssh/known_hosts" -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ServerAliveInterval=15)
PY="$(command -v python3 || command -v python || true)"

say() { echo "== $(date '+%F %T') $*"; }
die() { echo "ОШИБКА: $*" >&2; exit 1; }
api() { # api METHOD PATH [json]
  if [ "$DRY" = 1 ]; then echo "DRY: curl -X $1 $API$2 ${3:+-d '$3'}  (токен из HCLOUD_TOKEN)" >&2; echo '{"dry":true}'; return 0; fi
  [ -n "${HCLOUD_TOKEN:-}" ] || die "нужен HCLOUD_TOKEN (владелец вводит сам)"
  curl -fsS -X "$1" -H "Authorization: Bearer $HCLOUD_TOKEN" -H 'Content-Type: application/json' "$API$2" ${3:+-d "$3"}
}
jget() { "$PY" -c "import sys,json;d=json.load(sys.stdin);print(eval(sys.argv[1]))" "$1"; }
load_state() { [ -f "$STATE" ] && . "$STATE"; [ -n "${IP:-}" ] || { [ "$DRY" = 1 ] && IP=DRY-IP || die "нет $STATE — сначала up"; }; }
rsh() { if [ "$DRY" = 1 ]; then echo "DRY: ssh root@$IP $*"; else ssh "${SSHO[@]}" "root@$IP" "$@"; fi; }
rcp() { if [ "$DRY" = 1 ]; then echo "DRY: scp $*"; else scp "${SSHO[@]}" "$@"; fi; }
install_day() {
  rsh "cat > $A/bin/fast-day.sh <<'EOS'
#!/usr/bin/env bash
# одна полоса: сутки \$2 эпохи \$1 — корень-день ссылками на ящик, K1 из verify-logs, сценарий, rc
E=\$1; D=\$2; H=$A/epochs/\$E; SB=/mnt/sb/alpha/epochs/\$E/root
mkdir -p \$H/status; [ -e \$H/status/\$D.rc ] && exit 0
[ -e \$H/bin ] || ln -sfn $A/bin \$H/bin
cd \$H || exit 1
dir=study/root-\$D; rm -rf \$dir; mkdir -p \$dir
for f in \$SB/*-\$D.binlog \$SB/*-\$D-p*.binlog \$SB/instruments.csv; do [ -e \$f ] && ln -s \$f \$dir/; done
echo '{\"start_hour_utc\":0,\"closed\":true,\"binlog_files\":[]}' > \$dir/session.json
grep -aE '^verify: [A-Z0-9]+ status=(ok|fail)' \$SB/verify-logs/\$D.log | while read -r _ s st _; do echo \${st#status=} > \$dir/verify-\$s.status; done
mkdir -p study/approaches; [ -e study/approaches/D20 ] || ln -sfn $A/study/approaches/D20 study/approaches/D20
[ -e study/regime ] || ln -sfn $A/study/regime study/regime
[ -e study/sigma240 ] || ln -sfn $A/study/sigma240 study/sigma240
s=\$(date +%s); bash $SCEN_DIR/$SCEN-\$D.sh > \$H/status/\$D.log 2>&1; rc=\$?
echo \"rc=\$rc sec=\$((\$(date +%s)-s))\" > \$H/status/\$D.rc
EOS
chmod +x $A/bin/fast-day.sh"
}
boxssh="ssh -p 23 -i /root/.ssh/id_storagebox -o BatchMode=yes -o ServerAliveInterval=15"

case "$CMD" in
up)
  [ -f "$PUB" ] || [ "$DRY" = 1 ] || die "нет $PUB"
  [ "$DRY" = 1 ] || [ -n "${HCLOUD_TOKEN:-}" ] || die "нужен HCLOUD_TOKEN (владелец вводит сам)"
  [ "$DRY" = 1 ] || [ ! -f "$STATE" ] || die "машина уже создана ($STATE) — сначала down"
  pk=$(cat "$PUB" 2>/dev/null || echo "ssh-rsa DRY")
  say "ключ ssh в проект"
  kid=$(api POST /ssh_keys "{\"name\":\"$NAME-$(date +%s)\",\"public_key\":\"$pk\"}" | jget "d.get('ssh_key',{}).get('id','DRY')")
  say "сервер $TYPE в $LOC"
  r=$(api POST /servers "{\"name\":\"$NAME\",\"server_type\":\"$TYPE\",\"image\":\"ubuntu-24.04\",\"location\":\"$LOC\",\"ssh_keys\":[$kid],\"start_after_create\":true}")
  id=$(echo "$r" | jget "d.get('server',{}).get('id','DRY')"); ip=$(echo "$r" | jget "d.get('server',{}).get('public_net',{}).get('ipv4',{}).get('ip','DRY-IP')")
  [ "$DRY" = 1 ] || case "$ip" in ""|*[!0-9.]*) die "сервер не создан (ответ API: $(echo "$r" | cut -c1-200))";; esac
  [ "$DRY" = 1 ] || { mkdir -p "$(dirname "$STATE")"; printf 'IP=%s\nID=%s\nKEYID=%s\n' "$ip" "$id" "$kid" > "$STATE"; }
  say "создан id=$id ip=$ip (расчёт по часам начался — снос: down)"
  if [ "$DRY" != 1 ]; then for _ in $(seq 40); do ssh "${SSHO[@]}" -o ConnectTimeout=5 "root@$ip" true 2>/dev/null && break; sleep 5; done; fi
  ;;
provision)
  load_state; say "пакеты"
  rsh 'export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq rsync sshfs python3 htop >/dev/null; mkdir -p '"$A"'/{bin,epochs,study,tmp-p07/cells-by-day} /mnt/sb /scratch; ln -sfn /mnt/sb/alpha '"$A"'/sb; grep -q user_allow_other /etc/fuse.conf || echo user_allow_other >> /etc/fuse.conf'
  say "бинарники ($BINS) и скрипты с VPS/этой машины"
  for b in $BINS; do
    if [ "$DRY" = 1 ]; then echo "DRY: $VPS:/opt/alpha-compute/bin/$b -> $IP:$A/bin/"
    else ssh "${SSHO[@]}" "$VPS" "cat /opt/alpha-compute/bin/$b" | ssh "${SSHO[@]}" "root@$IP" "cat > $A/bin/$b.new && chmod +x $A/bin/$b.new && mv $A/bin/$b.new $A/bin/$b"; fi
  done
  [ "$DRY" = 1 ] || { for b in $BINS; do echo "md5 $b: VPS $(ssh "${SSHO[@]}" "$VPS" "md5sum < /opt/alpha-compute/bin/$b" | cut -c1-32) | новая $(rsh "md5sum < $A/bin/$b" | cut -c1-32)"; done; }
  [ "$DRY" = 1 ] && echo "DRY: tar tools/compute/*.py *.sh + _env.sh -> $IP:$A/bin/" || tar -C "$ROOT/tools/compute" -cf - . | rsh "tar -xf - -C $A/bin"
  if [ "$DRY" = 1 ]; then echo "DRY: сценарии $VPS:$SCEN_DIR → $IP"; else ssh "${SSHO[@]}" "$VPS" "tar -C $(dirname "$SCEN_DIR") -cf - --exclude='*.log' cells-by-day" | rsh "tar -C $A/tmp-p07 -xf -"; fi
  say "ключ ящика (копия, живёт до down) и sshfs ro"
  [ "$DRY" = 1 ] && echo "DRY: scp $BOXKEY -> root@$IP:/root/.ssh/id_storagebox (0600)" || { rsh 'mkdir -p /root/.ssh; chmod 700 /root/.ssh'; rcp "$BOXKEY" "root@$IP:/root/.ssh/id_storagebox"; rsh 'chmod 600 /root/.ssh/id_storagebox'; }
  rsh "mountpoint -q /mnt/sb || sshfs -p 23 -o IdentityFile=/root/.ssh/id_storagebox,ro,reconnect,ServerAliveInterval=15,ServerAliveCountMax=4,StrictHostKeyChecking=accept-new,kernel_cache,max_read=1048576,allow_other $BOX:/home /mnt/sb && ls /mnt/sb/alpha | head -3"
  say "regime, sigma240, D20 янв–июн с ящика (4 потока)"
  rsh "mkdir -p $A/study/regime $A/study/sigma240 $A/study/approaches/D20; rsync -a -e '$boxssh' $BOX:alpha/study/regime/ $A/study/regime/ 2>&1 | tail -2; for m in jan feb mar apr may jun; do echo \$m; done | xargs -P 4 -I{} rsync -a -e '$boxssh' $BOX:alpha/derived/tk015/e-{}/D20/ $A/study/approaches/D20/; du -sh $A/study/approaches/D20"
  ;;
speedtest)
  load_state; N="${STREAMS:-6}"; say "чтение ящика: $N потоков × 20 с"
  rsh "cd /mnt/sb/alpha/epochs/e-jan/root && ls *.binlog | head -$N | xargs -P $N -I{} sh -c 'timeout 20 cat {} | wc -c' | awk '{s+=\$1} END {printf \"%d МБ за 20 с = %.0f МБ/с суммарно\\n\", s/1048576, s/1048576/20}'"
  ;;
run)
  load_state; F="${1:?файл суток}"; LANES="${2:-${LANES:-40}}"
  [ "$DRY" = 1 ] || [ -f "$F" ] || die "нет $F"
  say "прогон: $LANES полос, сутки из $F, сценарий $SCEN_DIR/$SCEN-<сутки>.sh"
  [ "$DRY" = 1 ] || rcp "$F" "root@$IP:/root/days.txt"
  install_day
  rsh "systemd-run --unit=alpha-fast-run --setenv=HOME=/root --working-directory=$A bash -c \"xargs -a /root/days.txt -P $LANES -L1 $A/bin/fast-day.sh\"; sleep 2; systemctl is-active alpha-fast-run"
  ;;
collect)
  load_state; say "итоги суток → ящик"
  rsh "cd $A/epochs && for e in */; do e=\${e%/}; ls \$e/status/*.rc 2>/dev/null | while read -r rc; do d=\$(basename \$rc .rc); grep -q '^rc=0' \$rc || continue; [ -e \$e/status/\$d.box ] && continue; (cd \$e && find b5 -type d -name \$d | tar -cf /scratch/\$d.tar -T - ) && rsync -a --mkpath -e '$boxssh' /scratch/\$d.tar $BOX:alpha/derived/fast/out/\$e/\$d.tar && touch \$e/status/\$d.box && rm /scratch/\$d.tar; done; done; ls $A/epochs/*/status/*.box 2>/dev/null | wc -l"
  ;;
gate)
  load_state; D="${1:?сутки}"; VH="${2:-$A/epochs/e-jul}"; E="$(basename "$VH")"; T="${TMPDIR:-/tmp}"
  h='cd "$H" && find b5 -type f -path "*/'"$D"'/*" ! -name "*.log" | sort | xargs sha256sum'
  say "гейт «байт в байт»: сутки $D, готовые итоги VPS ($VH) против прогона на новой машине (тот же бинарник)"
  if [ "$DRY" = 1 ]; then echo "DRY: D20/$D с VPS → новая; fast-day.sh $E $D; sha256 b5/*/$D с обеих; cmp"; exit 0; fi
  install_day
  ssh "${SSHO[@]}" "$VPS" "tar -C $VH/study/approaches/D20 -cf - $D" | rsh "mkdir -p $A/study/approaches/D20 && tar -C $A/study/approaches/D20 -xf -"
  rsh "find $A/epochs/$E/status -name $D.rc -delete 2>/dev/null; $A/bin/fast-day.sh $E $D; cat $A/epochs/$E/status/$D.rc"
  ssh "${SSHO[@]}" "$VPS" "H=$VH; $h" > "$T/gate-vps.txt" 2>&1 || true
  rsh "H=$A/epochs/$E; $h" > "$T/gate-new.txt" 2>&1 || true
  if cmp -s "$T/gate-vps.txt" "$T/gate-new.txt" && [ -s "$T/gate-vps.txt" ]; then say "ГЕЙТ ЗЕЛЁНЫЙ: $(wc -l < "$T/gate-vps.txt") файлов, расхождений 0"
  else say "ГЕЙТ КРАСНЫЙ"; diff "$T/gate-vps.txt" "$T/gate-new.txt" | head -20; exit 1; fi
  ;;
down)
  [ "${FAST_CONFIRM:-}" = yes ] || [ "$DRY" = 1 ] || die "снос только с FAST_CONFIRM=yes (после collect и проверки, что итоги на ящике)"
  load_state; . "$STATE" 2>/dev/null || true
  say "снос сервера ${ID:-?} и ключа ${KEYID:-?}"
  api DELETE "/servers/${ID:-0}" >/dev/null; api DELETE "/ssh_keys/${KEYID:-0}" >/dev/null; [ "$DRY" = 1 ] || rm -f "$STATE"
  ;;
*) die "неизвестная команда $CMD" ;;
esac
