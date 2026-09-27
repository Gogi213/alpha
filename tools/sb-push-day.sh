#!/usr/bin/env bash
# Закрытые сутки коллектора → Hetzner Storage Box напрямую (T-37, CEO 27.09 «да» с условиями):
#   root/<SYM>-<день>[-pN].binlog → alpha/root/, root/deep/… → alpha/deep/ (раскладка как на Steam Deck и ящике);
#   rsync --bwlimit (замер 27.09: один поток коллектор→ящик ≈ 4,5 МБ/с, приём стакана ≈ 0,47 МБ/с → 3 МБ/с);
#   sha256 с обеих сторон (сумму ящика считает его sha256sum), журнал в формате T-30
#   `<сутки> <файл> ok|MISMATCH <байт> <sha256> collector-<сутки>` → sync/sb/done.txt, манифест — на ящик в
#   alpha/epochs/manifests/collector-<сутки>.sha256 (рядом с манифестами пачек T-30).
# Условие (б): швы gaps.csv в окне заливки против того же окна трёх прошлых ночей — новых больше → таймер выключается.
# Забор на Steam Deck и удаление после забора не меняются; файл, удалённый prune до заливки, — пропуск (копия — дека).
#   sb-push-day.sh [<сутки>]   (по умолчанию — вчера UTC)
set -uo pipefail
export LC_ALL=C
DAY="${1:-$(date -u -d yesterday +%F)}"
BASE=/opt/alpha; ROOT=$BASE/root; OUT=$BASE/sync/sb; mkdir -p "$OUT"; touch "$OUT/done.txt"
LOG=$OUT/push-$DAY.log; exec >> "$LOG" 2>&1
BW="${SB_BWLIMIT_KBPS:-3000}"; BATCH=150
SBH=u677479@u677479.your-storagebox.de
SSHC="ssh -p 23 -i $HOME/.ssh/id_storagebox -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4"
say() { echo "== $(date -u +%FT%TZ) $*"; }
[[ $DAY < $(date -u +%F) ]] || { say "$DAY не закрыт — стоп"; exit 2; }
T0=$(date -u +%FT%TZ)
say "старт $DAY, bwlimit ${BW} КБ/с"
for kind in root deep; do
  src=$ROOT; [ $kind = deep ] && src=$ROOT/deep
  list=$(cd "$src" && ls | grep -E "^[A-Z0-9]+-$DAY(-p[0-9]+)?\.binlog$" | sort)
  n=$(printf '%s' "$list" | grep -c . || true)
  [ "$n" -gt 0 ] || { say "$kind: файлов $DAY нет"; continue; }
  printf '%s\n' "$list" | nice -n 19 ionice -c3 rsync -a --partial --bwlimit="$BW" --files-from=- -e "$SSHC" \
    "$src/" "$SBH:alpha/$kind/" || say "$kind: rsync код $? (файлы, удалённые prune, — пропуск)"
  MAN=$OUT/$kind-$DAY.sha256
  (cd "$src" && printf '%s\n' "$list" | while read -r f; do [ -f "$f" ] && echo "$f"; done \
    | tr '\n' '\0' | nice -n 19 ionice -c3 xargs -0 -r sha256sum) > "$MAN"
  m=$(wc -l < "$MAN"); : > "$MAN.remote"
  mapfile -t files < <(cut -c67- "$MAN"); files=("${files[@]/#/alpha/$kind/}")
  for ((i = 0; i < m; i += BATCH)); do
    $SSHC $SBH sha256sum "${files[@]:i:BATCH}" 2>&1 | while read -r h f; do echo "$h  ${f#alpha/$kind/}"; done >> "$MAN.remote" || true
  done
  bad=0
  while read -r h f; do
    r=$(awk -v f="$f" '$2 == f {print $1}' "$MAN.remote")
    st=ok; [ "$r" = "$h" ] || { st=MISMATCH; bad=$((bad + 1)); }
    echo "$DAY $kind/$f $st $(stat -c %s "$src/$f") $h collector-$DAY" >> "$OUT/done.txt"
  done < "$MAN"
  say "$kind: файлов $n, сверено $m, расхождений $bad"
done
: > "$OUT/collector-$DAY.sha256"
for kind in root deep; do
  [ -f "$OUT/$kind-$DAY.sha256" ] && sed "s|  |  $kind/|" "$OUT/$kind-$DAY.sha256" >> "$OUT/collector-$DAY.sha256"
done
if [ -s "$OUT/collector-$DAY.sha256" ]; then
  $SSHC $SBH mkdir -p alpha/epochs/manifests
  rsync -a -e "$SSHC" "$OUT/collector-$DAY.sha256" "$SBH:alpha/epochs/manifests/" || say "манифест на ящик не лёг"
fi
T1=$(date -u +%FT%TZ)
# (б) швы в окне заливки против того же окна трёх прошлых ночей
w() { awk -F, -v a="$1" -v b="$2" 'NR > 1 && $1 >= a && $1 <= b' "$ROOT/gaps.csv" | wc -l; }
now=$(w "$T0" "$T1"); prev=0
for k in 1 2 3; do
  a=$(date -u -d "@$(( $(date -u -d "$T0" +%s) - k * 86400 ))" +%FT%TZ)
  b=$(date -u -d "@$(( $(date -u -d "$T1" +%s) - k * 86400 ))" +%FT%TZ)
  p=$(w "$a" "$b"); [ "$p" -gt "$prev" ] && prev=$p
done
say "швы gaps.csv в окне $T0…$T1: $now (прошлые 3 ночи, максимум: $prev)"
if [ "$now" -gt "$prev" ]; then
  sudo -n systemctl disable --now alpha-sb-push.timer && say "НОВЫЕ ШВЫ — таймер alpha-sb-push выключен"
fi
bad=$(grep -c "^$DAY .* MISMATCH " "$OUT/done.txt"); nok=$(grep -c "^$DAY .* ok " "$OUT/done.txt")
if [ "$nok" -eq 0 ] && [ "$bad" -eq 0 ]; then say "VERDICT EMPTY $DAY: файлов нет"
else say "VERDICT $([ "$bad" -eq 0 ] && echo OK || echo MISMATCH) $DAY: ok $nok, расхождений $bad"; fi
