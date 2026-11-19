#!/bin/bash
cd /data/tk582v3/rep24
HI=1795000441000
SH=$(python3 -c "import json;print(\",\".join(json.load(open(\"/data/tk582v3/layout.json\"))[\"live_shards\"]))")
python3 /data/tk582v3/tk582_copy_report.py --live /data/collect/binance/data --copy /data/tk582v3/data --live-shards $SH --to-ms $HI   --live-health /data/turbo-probe/soak-binance/health.json --copy-health /data/tk582v3/health.json   --compare-bin /data/tk582v3/alpha-tk582v3-cmp --work /data/tk582v3/cmp --compare-span-h 2 --json rep24.json > rep24.md 2> rep24.err
echo report-rc=$? > rep24.rc
for side in live copy; do
  if [ $side = live ]; then F=/data/collect/binance/data/2026-11-18/mark00-*.tsv.zst; else F=/data/tk582v3/data/2026-11-18/mark00-*.tsv.zst; fi
  zstd -dc -q $F 2>/dev/null | python3 /data/tk582v3/tk582_mark_frames.py 1794960000000 $HI > mk18-$side.json
done
touch rep24.done
