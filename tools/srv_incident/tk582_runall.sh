set -e
O=/tmp/tk582out; mkdir -p $O; cd /tmp
P="nice -n 19 python3"; B=/data/collect/binance/data; DAY=2026-11-16
$P /tmp/tk582_gaps.py $B $DAY 21:18:00 21:23:30 8 > $O/gaps-ip-S1.txt
$P /tmp/tk582_gaps.py $B $DAY 21:48:50 21:54:00 8 > $O/gaps-ip-S2.txt
$P /tmp/tk582_gaps.py $B $DAY 22:19:30 22:22:30 8 > $O/gaps-ip-S3.txt
cp $O/g_s1.txt $O/g_s1.bak 2>/dev/null || true
$P /tmp/tk582_gaps2.py $B $DAY 21:18:00 21:23:30 8 > $O/story-S1.txt
$P /tmp/tk582_gaps2.py $B $DAY 21:48:50 21:54:00 8 > $O/story-S2.txt
$P /tmp/tk582_gaps2.py $B $DAY 22:19:30 22:22:30 8 > $O/story-S3.txt
cp $O/g_mark_all.txt $O/story-mark-2100-2230.txt
$P /tmp/tk582_dark.py $DAY 21:00:00 22:30:00 3 > $O/dark-all.jsonl
$P /tmp/tk582_sumdark.py $O/dark-all.jsonl > $O/sum-dark-by-window.txt
$P /tmp/tk582_blk.py $O/dark-all.jsonl > $O/blockers-in-stops.txt
$P /tmp/tk582_unbl.py $O/dark-all.jsonl > $O/gaps-blocked-vs-free.txt
$P /tmp/tk582_m1.py $O/dark-all.jsonl > $O/model-m1-savings.txt
$P /tmp/tk582_opts.py $O/dark-all.jsonl > $O/model-mark-options.txt
$P /tmp/tk582_capmatch.py $O/dark-all.jsonl > $O/capwait-vs-recovery.txt
$P /tmp/tk582_tcp.py > $O/tcp-state-at-timeouts.txt
$P /tmp/tk582_lanes.py > $O/mark00-lanes.txt
$P /tmp/tk582_conn.py > $O/connect-rate.txt
$P /tmp/tk582_vol.py $DAY 21:10:00 21:18:00 21:18:00 21:23:30 > $O/shard-volume-vs-dark-S1.txt
$P /tmp/tk582_vol.py $DAY 21:40:00 21:48:50 21:48:50 21:54:00 > $O/shard-volume-vs-dark-S2.txt
for w in "21:17:00 21:24:00 S1" "21:48:00 21:55:00 S2" "22:18:00 22:23:00 S3"; do set -- $w; $P /tmp/tk582_grp.py $DAY $1 $2 20 > $O/groups-$3.txt; $P /tmp/tk582_bw.py $DAY $1 $2 20 > $O/bytes-lag-$3.txt; done
$P /tmp/tk582_evmin.py $DAY 21:17:00 21:24:00 > $O/events-per-minute-exchanges-S1.txt
$P /tmp/tk582_evmin.py $DAY 21:48:00 21:55:00 > $O/events-per-minute-exchanges-S2.txt
$P /tmp/tk582_evmin.py $DAY 22:18:00 22:23:00 > $O/events-per-minute-exchanges-S3.txt
$P /tmp/tk582_ips.py > $O/exchange-ip-ranges.txt
echo ALLDONE > $O/DONE
