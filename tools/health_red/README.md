Скрипты разбора красных слотов (docs/findings/health-red-slots-2026-11-16.md). Читают данные на srv (`/tmp/v3.json` = ответ `/api/v3`, sysstat `sa14/sa15`,
`series-minutes.jsonl`, `stats-*.jsonl`, `events-*.jsonl`, `/data/netwatch`); запускать на srv, `ab.sh` — с ПК (тянет статистику Софии).
Порядок: `slotsys.py` → `minfeat.py` → `cmp.py`, `ep.py`; `stall.py` → `stall2.py`, `cont2.py`, `steal.py`; `to1.py`, `ss2.py`, `mid.py <epoch 00:00 UTC>`;
Bybit: `by3.py <shard>` (скачивает архив public.bybit.com), `by4.py <день> <shard> <epoch>`; Бинанс/Gate против Софии: `ab.sh <t0> <t1>` (+`ab.py`).
Тяжёлых вычислений нет (чтение ≤ 250 МБ, `nice`).
