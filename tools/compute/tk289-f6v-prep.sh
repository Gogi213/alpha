set -e
# TK-289 П-18 6в: план 70 клеток (S × usd_min) -> /data/tk0289/f6vplan; выписка В-224 по steps-f6v.tsv
cd /data/tk0289
rm -rf f6vplan; python3 p18_plan.py f6vplan --axes 6v --label L
head -3 f6vplan/L-2026-01.cells; grep -c . f6vplan/L-2026-0*.cells | head -3; grep -o "flow=[0-9.]*" f6vplan/L-2026-01.args | sort -u | head -9
python3 /data/registry/guard.py plan --steps /data/tk0289/steps-f6v.tsv 2>&1 | tail -15
