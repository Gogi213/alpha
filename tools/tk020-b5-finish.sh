#!/usr/bin/env bash
# TK-020 (CEO 02:00): /opt/alpha/b5 коллектора (rounds.csv упавших сеток 17.09, 3 ГБ) — перенос на ящик уже идёт юнитом
# alpha-tk020-b5push2; этот скрипт ждёт его конца, добивает rsync до кода 0, затем сверка и локальное удаление ТОЛЬКО через
# tk020-dedupe-apply (sha256 локальной = sha256sum канона на ящике перед каждым файлом): сначала сухой, apply — только если
# would-delete = числу файлов плана. Запуск от root (каталог 2026-09-17 принадлежит root), ключ ящика — ubuntu.
set -uo pipefail
export HOME=/home/ubuntu SB_KNOWN_HOSTS=/home/ubuntu/.ssh/known_hosts
cd /home/ubuntu/tk020 || exit 1
RP=alpha/derived/collector-b5-2026-09-17; SRC=/opt/alpha/b5; ST=b5-finish.status
SBH=u677479@u677479.your-storagebox.de
SSHC="ssh -p 23 -i $HOME/.ssh/id_storagebox -o UserKnownHostsFile=$SB_KNOWN_HOSTS -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4"
say() { echo "$(date -u +%FT%TZ) $*" | tee -a $ST; }
while systemctl is-active --quiet alpha-tk020-b5push2; do sleep 30; done
say "перенос-юнит закончил; добивка rsync до кода 0"
ok=0
for t in 1 2 3 4 5; do
  nice -n 19 ionice -c3 rsync -az --partial --bwlimit=3000 -e "$SSHC" "$SRC/" "$SBH:$RP/" && { ok=1; break; }
  say "rsync попытка $t не удалась"; sleep 20
done
[ $ok = 1 ] || { say "FAIL: rsync не дошёл — ничего не удалено"; exit 1; }
python3 - <<'EOF'
import os, csv
w = csv.writer(open('plan-b5.csv', 'w', newline=''))
w.writerow(['local', 'canon', 'bytes', 'sha256', 'class'])
for d, _, fs in sorted(os.walk('/opt/alpha/b5')):
    for f in sorted(fs):
        p = os.path.join(d, f)
        if os.path.islink(p): continue
        w.writerow([p, 'alpha/derived/collector-b5-2026-09-17/' + os.path.relpath(p, '/opt/alpha/b5'), os.path.getsize(p), '', 'b5'])
EOF
nt=$(( $(wc -l < plan-b5.csv) - 1 )); say "план: $nt файлов"
LOG=dedupe-b5-dry.csv python3 tk020-dedupe-apply.py local --plan plan-b5.csv --allow-prefix $SRC > dry-b5.out 2>&1
nw=$(awk -F, 'NR>1 && $2=="would-delete"' dedupe-b5-dry.csv | wc -l)
say "сухой: would-delete $nw из $nt"
[ "$nw" -eq "$nt" ] && [ "$nt" -gt 0 ] || { say "FAIL: сухой не сошёлся — ничего не удалено, см. dedupe-b5-dry.csv"; exit 1; }
LOG=dedupe-b5-apply.csv python3 tk020-dedupe-apply.py local --plan plan-b5.csv --allow-prefix $SRC --apply > apply-b5.out 2>&1
nd=$(awk -F, 'NR>1 && $2=="deleted"' dedupe-b5-apply.csv | wc -l)
say "DONE: удалено $nd из $nt файлов; $(tail -n 3 apply-b5.out | tr '\n' ' ')"
