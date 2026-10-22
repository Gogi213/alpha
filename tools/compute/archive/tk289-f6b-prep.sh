set -e
cd /data/tk0289
rm -rf f6bplan; python3 p18_plan.py f6bplan --axes 6b --label L --strength-json thr/strength.json
head -3 f6bplan/L-2026-01.cells; grep -c . f6bplan/L-2026-0*.cells | head -3; grep -o "strength_min=[0-9.]*" f6bplan/L-2026-01.args | head -3
python3 /data/registry/guard.py plan --steps /data/tk0289/steps-f6b.tsv 2>&1 | tail -15
python3 -c "import json;d=json.load(open('/data/sched/jobs/1017150324841.json'));print('f1 active_s',d['active_s'],'cores',d['cores'])"
