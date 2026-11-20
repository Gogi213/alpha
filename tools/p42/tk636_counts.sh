#!/usr/bin/env bash
# П-42 §13.35 (TK-636): counts без P&L (h775 первым — пишет ECDF и пороги, затем o566) — таблицы `--ftab`, покрытие, дубли, m_new, Jaccard, peer_gap.
# Запуск: systemd-run --unit=tk636-counts --collect -q -p Nice=19 -p CPUQuota=400% -p IOSchedulingClass=idle bash /data/p42-tk636/code/tk636_counts.sh ; код возврата — /data/p42-tk636/rc/counts.
set -uo pipefail
ROOT=/data/p42-tk636; cd "$ROOT/code"; mkdir -p "$ROOT/log" "$ROOT/rc"; rm -f "$ROOT/rc/counts"
for uni in h775 o566; do
  PYTHONUTF8=1 python3 tk636_run.py counts --uni $uni > "$ROOT/log/counts-$uni.log" 2>&1
  rc=$?; [ $rc -eq 0 ] || { echo "counts-$uni:$rc" > "$ROOT/rc/counts"; exit 1; }
done
echo 0 > "$ROOT/rc/counts"
