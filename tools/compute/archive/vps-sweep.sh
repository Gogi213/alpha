#!/usr/bin/env bash
# Диск VPS Софии (TK-075). Ставится как /opt/alpha-compute/sweep.sh.
#   sweep.sh run <target> <cmd...>  — под .build.lock: проверка места, cmd, затем rm -rf <target> (trap, и при падении)
#   sweep.sh                        — таймер 15 мин: если замок свободен — убрать осиротевшие target-*, старые копии исходников/архивы;
#                                     тревога в шину (CEO), если свободно < MINFREE ГБ или сборочная область > CAP ГБ
# Чужое (/home, /root, /opt/watcher…) не трогаем.
D=/opt/alpha-compute; CAP="${SWEEP_CAP_GB:-12}"; MINFREE="${SWEEP_MINFREE_GB:-20}"; PEAK="${SWEEP_PEAK_GB:-3}"; KEEPN="${SWEEP_KEEP_BIN:-40}"
avail() { df --output=avail -BG "${SWEEP_PATH:-/}" | tail -1 | tr -dc 0-9; }
area() { du -sBG -c "$D"/target-* "$D"/wave2-src* "$D"/src-* 2>/dev/null | tail -1 | tr -dc 0-9; }
alarm() {
  echo "sweep: ТРЕВОГА $1" | tee -a "$D/sweep.alarm" >&2
  [ -r "$D/bus/busclient.py" ] && python3 "$D/bus/busclient.py" send "диск.софия" --payload "{\"msg\":\"$1\"}" >/dev/null 2>&1
  return 0
}
WD="$PWD"; cd "$D" || exit 0
clean_old() { find . -maxdepth 1 \( -name 'wave2-src*' -o -name 'src-*' -o -name '*.tgz' \) -cmin +1440 -exec rm -rf {} +; }
clean_bin() {
  [ -d bin ] || return 0
  ls -t bin | tail -n +$((KEEPN+1)) | while read -r f; do
    grep -qxF "$f" bin-keep.txt 2>/dev/null || { [ -n "$(find "bin/$f" -mtime +2 2>/dev/null)" ] && [ -f "bin/$f" ] && rm -f "bin/$f"; }
  done
}
if [ "${1:-}" = run ]; then
  T="$2"; shift 2
  rm -rf "$D"/target-*   # замок наш: чужих сборок нет, всё осиротевшее
  clean_old
  a=$(avail); u=$(area)
  if [ $((u + PEAK)) -gt "$CAP" ] || [ $((a - PEAK)) -lt "$MINFREE" ]; then
    clean_bin; a=$(avail); u=$(area)
  fi
  if [ $((u + PEAK)) -gt "$CAP" ] || [ $((a - PEAK)) -lt "$MINFREE" ]; then
    alarm "отказ сборки: область ${u}G + ${PEAK}G > ${CAP}G или свободно ${a}G - ${PEAK}G < ${MINFREE}G"
    echo "sweep: ОТКАЗ — мало места (область ${u}G, свободно ${a}G)" >&2; exit 75
  fi
  cd "$WD"; trap 'rm -rf "$T"' EXIT
  "$@"
  exit $?
fi
# таймер
flock -n 9 || exit 0   # идёт сборка — не трогаем
clean_old; rm -rf "$D"/target-*
clean_bin
a=$(avail); u=$(area)
echo "sweep: free ${a}G area ${u}G"
[ "$a" -lt "$MINFREE" ] && alarm "свободно ${a}G < ${MINFREE}G на / (VPS София)"
[ "$u" -gt "$CAP" ] && alarm "сборочная область ${u}G > ${CAP}G"
exit 0
