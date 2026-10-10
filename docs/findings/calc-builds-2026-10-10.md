# Сборки Rust на calc (TK-170, В-223) — 10.10

- **Как:** `tools/vps-check.sh` по умолчанию `CHECK_HOST=calc` (VPS — `CHECK_HOST=vps`). Заявка `alsched submit --cls prod --prio 3`,
  `CALC_CORES` 4 / `CALC_MEM` 12 / `CALC_MAX` 15m; раннер `tools/calc-build-run.sh` → `/opt/rbuild/run.sh`.
  Тулчейн 1.93.1 + clippy + rustfmt в `/opt/rbuild` (диск `/`, SSD). 4 слота (`RBUILD_SLOTS`), у каждого тёплый `target-slotK` и `src-slotK`.
- **Замеры:** холодный `all` (fmt+clippy+test, 1231 тест, 4 ядра, зависимости с нуля) — 291 с. Тёплые слоты под боевой нагрузкой
  инженеров (TK-150/161/163, 4 параллельных заявки): 0,7–3,3 мин на сборку. Чистое 1×16 / 2×8 / 4×4 не снято — calc занят
  сборками и гейтами; выбрано 4×4 (= 16 потоков, бюджет CEO).
- **Диск:** 4 слота ≈ +3 ГБ на `/` (746 ГБ занято, 986 ГБ свободно).
- **Грабли:** `--max-runtime` = RuntimeMaxSec по стене, заморозка `measure` его съедает (rc=143 при active_s 10 с); гейты diff — `--cls prod`.
  Каждый submit пишет pending в реестр guard. >15m — предупреждение «без выписки».
- **Не снято:** стена одной головы на VPS против calc (прогон на VPS 21:45 недействителен — скрипт правили на ходу).
