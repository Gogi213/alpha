#!/usr/bin/env bash
# Run on srv (host alias `box` in /root/.ssh/config): copy /data/klines to Storage Box alpha/klines/ with rsync
# (no --delete, nothing removed anywhere), then compare sha256 of every file computed locally and by the box itself.
#   systemd-run --unit=klines-box --collect --nice=19 -p IOSchedulingClass=idle bash /data/klines/bin/push-box.sh
# Result: /data/klines-box-check/{local,remote}.sha256 and verdict.txt (last line: VERDICT OK|MISMATCH).
set -euo pipefail
export HOME=/root
SRC=/data/klines
DST=alpha/klines
OUT=/data/klines-box-check
BATCH=100
mkdir -p "$OUT"
ssh -o BatchMode=yes box mkdir -p alpha "$DST"
echo "== $(date -Is) rsync"
nice -n 19 ionice -c3 rsync -a --partial --stats --exclude bin/ -e "ssh -o BatchMode=yes" "$SRC/" "box:$DST/" | tail -6
echo "== $(date -Is) sha256 local"
(cd "$SRC" && find . -type f ! -path './bin/*' -printf '%P\0' | sort -z | nice -n 19 ionice -c3 xargs -0 sha256sum) > "$OUT/local.sha256"
n=$(wc -l < "$OUT/local.sha256")
echo "== $(date -Is) sha256 remote ($n files)"
: > "$OUT/remote.sha256"
mapfile -t files < <(cut -c67- "$OUT/local.sha256")
files=("${files[@]/#/$DST/}")
for ((i = 0; i < n; i += BATCH)); do
  ssh -o BatchMode=yes box sha256sum "${files[@]:i:BATCH}" 2>&1 | while read -r h f; do echo "$h  ${f#$DST/}"; done >> "$OUT/remote.sha256" || true
done
bad=$(diff <(sort -k2 "$OUT/local.sha256") <(sort -k2 "$OUT/remote.sha256") | grep -c '^[<>]' || true)
bytes=$(du -sb --exclude=bin "$SRC" | cut -f1)
if [[ $bad -eq 0 ]]; then
  echo "VERDICT OK: $n files, $bytes bytes, sha256 equal on srv and Storage Box ($(date -Is))"
else
  echo "VERDICT MISMATCH: $bad lines differ of $n files, see diff $OUT/local.sha256 $OUT/remote.sha256 ($(date -Is))"
fi
