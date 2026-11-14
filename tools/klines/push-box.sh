#!/usr/bin/env bash
# Run on srv (host alias `box` in /root/.ssh/config): copy a directory to the Storage Box with rsync (no --delete, nothing removed
# anywhere), then compare sha256 of every file computed locally and by the box itself.
#   klines:    systemd-run --unit=klines-box --collect --nice=19 -p IOSchedulingClass=idle -p StandardOutput=append:/data/klines-box-check/box.log bash /data/klines/bin/push-box.sh
#   aggtrades: SRC=/data/aggtrades DST=alpha/aggtrades OUT=/data/aggtrades-box-check BW=40000 bash /data/klines/bin/push-box.sh
# BW = rsync --bwlimit in KB/s (0 = none). Result: $OUT/{local,remote}.sha256, last log line: VERDICT OK|MISMATCH.
# Volatile files (box.log, run*.log, progress.json, *.tmp*) are neither copied nor hashed.
set -euo pipefail
export HOME=/root
SRC=${SRC:-/data/klines}
DST=${DST:-alpha/klines}
OUT=${OUT:-/data/klines-box-check}
BW=${BW:-0}
BATCH=100
mkdir -p "$OUT"
EXC=(--exclude bin/ --exclude box.log --exclude 'run*.log' --exclude progress.json --exclude '*.tmp*')
ssh -o BatchMode=yes box mkdir -p alpha "$DST"
echo "== $(date -Is) rsync $SRC -> $DST (bwlimit $BW KB/s)"
nice -n 19 ionice -c3 rsync -a --partial --stats --bwlimit="$BW" "${EXC[@]}" -e "ssh -o BatchMode=yes" "$SRC/" "box:$DST/" | tail -6
echo "== $(date -Is) sha256 local"
(cd "$SRC" && find . -type f ! -path './bin/*' ! -name box.log ! -name 'run*.log' ! -name progress.json ! -name '*.tmp*' -printf '%P\0' | sort -z | nice -n 19 ionice -c3 xargs -0 sha256sum) > "$OUT/local.sha256"
n=$(wc -l < "$OUT/local.sha256")
echo "== $(date -Is) sha256 remote ($n files)"
: > "$OUT/remote.sha256"
mapfile -t files < <(cut -c67- "$OUT/local.sha256")
files=("${files[@]/#/$DST/}")
for ((i = 0; i < n; i += BATCH)); do
  ssh -o BatchMode=yes box sha256sum "${files[@]:i:BATCH}" 2>&1 | while read -r h f; do echo "$h  ${f#$DST/}"; done >> "$OUT/remote.sha256" || true
done
bad=$(diff <(sort -k2 "$OUT/local.sha256") <(sort -k2 "$OUT/remote.sha256") | grep -c '^[<>]' || true)
bytes=$(awk 'END{print NR}' "$OUT/local.sha256" >/dev/null; cd "$SRC" && find . -type f ! -path './bin/*' ! -name box.log ! -name 'run*.log' ! -name progress.json ! -name '*.tmp*' -printf '%s\n' | awk '{s+=$1} END{print s}')
if [[ $bad -eq 0 ]]; then
  echo "VERDICT OK: $n files, $bytes bytes, sha256 equal on srv and Storage Box ($(date -Is))"
else
  echo "VERDICT MISMATCH: $bad lines differ of $n files, see diff $OUT/local.sha256 $OUT/remote.sha256 ($(date -Is))"
fi
