#!/usr/bin/env bash
# TK-601: обновить страницу CatBoost П-42 (srv:8090). Правим tools/p42/ml_progress.json (схема — ml_progress.schema.json), затем:
#   bash tools/p42/ml_page_update.sh
# Загружает progress.json и исходники генератора на srv, перестраивает equity.json (tk601_equity.py, сверка с проверенными числами), пересобирает /data/pages/ml/index.html (JSON проверяется по схеме до записи страницы).
# TK-624: блок «Где мы» (ml_progress.json → where) и вид «Базы» вкладки «Эквити» (bases.json — суточные ряды L_base / S_base / S_mk1, $, tk624_table.py) — та же команда.
# TK-606: вкладка «Признаки» — правим tools/p42/ml_features.json (схема ml_features.schema.json), та же команда.
# TK-602: подвид «Форма» вкладки «Эквити» берёт /data/pages/ml/data/shape.json (tk602_shape.py: ~4 мин на srv, сверка с equity.json). Пересчитать вместе со страницей: REBUILD_SHAPE=1 bash tools/p42/ml_page_update.sh
set -euo pipefail
cd "$(dirname "$0")"
KEY=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
HOST=root@5.231.25.224
python -c "import json,sys; json.load(open('ml_progress.json', encoding='utf-8'))" || { echo "ml_progress.json — не JSON"; exit 2; }
python -c "import json,sys; json.load(open('ml_features.json', encoding='utf-8'))" || { echo "ml_features.json — не JSON"; exit 2; }
tar -c ml_progress.schema.json ml_features.schema.json tk593_page.py tk593_template.html tk601_equity.py tk602_shape.py | ssh "${KEY[@]}" "$HOST" 'tar -x -C /data/pages/_src'
# TK-624: код, читающий готовые ряды баз TK-599 / TK-616 (только чтение) → /data/pages/ml/data/bases.json
tar -c retitr_full.py retitr_full_run.py retitr_cells.py retitr_ftab.py tk616_maker.py tk624_table.py | ssh "${KEY[@]}" "$HOST" 'mkdir -p /data/p42-tk624/code && tar -x -C /data/p42-tk624/code'
scp "${KEY[@]}" -q ml_progress.json "$HOST:/data/pages/ml/data/progress.json"
scp "${KEY[@]}" -q ml_features.json "$HOST:/data/pages/ml/data/features.json"
ssh "${KEY[@]}" "$HOST" "REBUILD_SHAPE=${REBUILD_SHAPE:-0} bash -s" <<'REMOTE'
set -e
/opt/venv/bin/python /data/pages/_src/tk601_equity.py /data/p42-tk598 /data/pages/ml/data/equity.json
/opt/venv/bin/python /data/p42-tk624/code/tk624_table.py /data/p42-tk624/code /data/pages/ml/data/bases.json
if [ "$REBUILD_SHAPE" = 1 ] || [ ! -s /data/pages/ml/data/shape.json ]; then
  mkdir -p /data/p42-tk602/out
  /opt/venv/bin/python /data/pages/_src/tk602_shape.py /data/p42-tk598 /data/pages/ml/data/equity.json /data/p42-tk602/out
  cp /data/p42-tk602/out/shape.json /data/pages/ml/data/shape.json
fi
/opt/venv/bin/python /data/pages/_src/tk593_page.py /data/tk592/tree /data/tk592/pairs /data/pages/ml/data/curves.json /data/pages/_src/tk593_template.html /data/pages/ml
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:8090/
REMOTE
