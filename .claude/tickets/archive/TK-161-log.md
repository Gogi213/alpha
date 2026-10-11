# TK-161 — архив лога

Записи, перенесённые из `.claude/tickets/TK-161.md` (старые сверху, текст дословно).

### 2026-10-10T17:44:12+04:00 ceo
CEO 17:50 — правило сдачи для оставшихся КТ (разрывы 4a.1 и КТ-7/КТ-9 сегодня): сдача Судье = твоя ветка УЖЕ влита в рабочий ствол audit/design-fixes-2026-09-22 (git merge сам, конфликты решены, vps-check all зелёный на голове ствола, если задет src); Судья принимает голову ствола. Не оставлять принятое в отдельной ветке, не держать копии правок в основном дереве.

### 2026-10-10T18:10:50+04:00 ceo
CEO 18:00, владелец (В-222): «Нужно стремиться срезать код. Чтобы ты его не грепал лишний раз. Это экономия токенов». Для этого КТ: в отчёте — строки активного кода до/после (src/**/*.rs; tools без archive); чистый рост только с причиной; мёртвое, дубли, неиспользуемые флаги/env — удалить тем же КТ; старый путь после приёмки нового — удалить, а не оставлять под флагом. Качество и «байт в байт» — как прежде.

### 2026-10-10T18:56:12+04:00 ceo
CEO 19:15, В-222 — срезы Судьи (docs/findings/kt-code-cuts-2026-10-10.md) В ОБЪЁМ КТ-6a: А — общий движок суток Э-11 (shared_driver/shared_multi/sched/shared_depth + drive_day_shared), индекс удержания hold_index (+ветки fast_hold), развёртка горизонта, trim в backtest/ (≈2 650 строк); Б — после приёмки гейта ветки «выкл» боевого набора удалить (EVENT_STEPS 1/live/0, DIRECT_FEED-выкл; медленное удержание — только #[cfg(test)] как оракул), TICK/ATTEMPT_STATS → один --diag; В — команда lob backtest (1 209) после выноса read_tick_step/touch_view_of_approach/read_day_schedule/форм в plan/forms. 7 ALPHA_* срезанного в RunFlags НЕ переносить. Строки до/после — в отчёт.

### 2026-10-10T19:55:10+04:00 engineer
[итог: wait] ждём: file:C:/visual projects/alpha/data/tk161/a1-vps.done — А1 (общий движок Э-11) срезан: 4 модуля, drive_day_shared, тест, 3 поля RunFlags, −1605 строк src, не коммичено. vps-check идёт. Дальше: починить, коммит, А2.

### 2026-10-10T19:55:57+04:00 engineer
[итог: wait] ждём: file:C:/visual projects/alpha/data/tk161/a1-vps.done — Первый vps-check упал на tar (файл tools/serve_dashboard.py на миг пропал); перезапущен. В дереве чужая правка bounce_grid.rs (6b) — не коммичу. Дальше: зелёный -> коммит моих файлов, А2.

### 2026-10-10T20:07:50+04:00 engineer
[итог: wait] ждём: file:C:/visual projects/alpha/data/tk161/a1-vps.done — VPS: 1235 тестов зелёные, но локальный bash упал на vps-check.sh (его правит другая сессия) до clippy/fmt; перезапуск с копией скрипта. Дальше: коммит А1, А2.

### 2026-10-10T20:08:51+04:00 engineer
[итог: wait] ждём: file:C:/visual projects/alpha/data/tk161/a1-vps.done — all отказал check-sprawl: tools/compute 84>83 файлов (не моё, в HEAD). Тесты 1235 зелёные ранее; гоню clippy+fmt отдельно. Дальше: коммит А1, А2; про sprawl спросить Судью.

### 2026-10-10T20:14:39+04:00 engineer
[итог: continue] — А1 закоммичен 473b7bdb: −1605 строк src, 1235 тестов, clippy+fmt зелёные (all отказал check-sprawl tools/compute 84>83, не моё). Дальше А2: hold_index/trim зовутся из bounce_grid.rs (WIP 6b).

### 2026-10-10T20:18:35+04:00 engineer
[итог: wait] ждём: file:C:/visual projects/alpha/data/tk161/a2.done — А2 в дереве (hold_index, trim, horizon_grow, шов shared в fast_hold; −1514 строк src, не коммичено); vps-check all -> a2-vps.log. Дальше: правка clippy, коммит моих хунков (bounce_grid.rs с WIP 6b).

### 2026-10-10T20:28:59+04:00 engineer
[итог: wait] ждём: file:C:/visual projects/alpha/data/tk161/a2.done — А2: fmt-ошибка (стр.3907) исправлена cargo fmt, vps-check all перезапущен. bounce_grid.rs уже влит 6b (с моей правкой диагностики). Дальше: зелёный -> коммит А2.

### 2026-10-10T20:39:10+04:00 engineer
[итог: wait] ждём: file:C:/visual projects/alpha/data/tk161/a2.done — А2: компиляция тестов падала на удалённом fast_hold::FORCE_IDX (backtest.rs:3023) — блок убран; test+clippy+fmt на VPS перезапущены в фоне (a2-vps.log). Дальше: зелёный -> коммит А2.

### 2026-10-10T21:14:06+04:00 engineer
[итог: wait] ждём: file:C:/visual projects/alpha/data/tk161/a2.done — А2: 1231 тест зелёный; clippy упал на сиротском doc-комментарии в diag.rs (убран); clippy+fmt перезапущены на VPS. Дальше: зелёный -> коммит А2.

### 2026-10-10T21:25:24+04:00 engineer
[итог: continue] — А2 закоммичен 5be4b029: −1471 строк src; 1231 тест, clippy, fmt зелёные (VPS). Дальше Б (ветки «выкл», один --diag), В (lob backtest); гейт d15+d01 diff 0 ещё не гонялся.

### 2026-10-10T21:30:16+04:00 engineer
[итог: wait] ждём: host:calc:/data/tk161/gate.done — Гейт А1+А2 на голове 5be4b029: сборка live+r2 на VPS -> calc -> gate-families (base; r2,dl,tk115*) идёт фоном (data/tk161/drive-gate.sh, drive.log). Дальше: summary diff 0 -> Б/В.

### 2026-10-10T21:47:37+04:00 watch
сторож: ждёшь `host:calc:/data/tk161/gate.done`, а работающих заданий тикета на машине 0 дольше 15 мин; последние: заданий тикета на машине нет — ожидание снято, владелец будится: перезапусти задание или смени wait_for

### 2026-10-10T21:50:32+04:00 engineer
[итог: wait] ждём: host:calc:/data/tk161/gate.done — Гейт А1+А2 (голова 5be4b029): бинари live a294da00 / r2 608ac2af на calc, job 1010195013616 (base; r2,dl,tk115*) идёт. Дальше: summary diff 0 -> Б/В.

### 2026-10-10T22:13:50+04:00 ceo
CEO 22:15 (обход): сборки build-TK-150/158/163 стоят queued на calc, calc простаивает (load 1,1) — их держит tk161-gate класса measure (эксклюзивное окно alsched). Гейт «байт в байт» (diff 0) эксклюзива НЕ требует: подавать --cls prod (параллельно со сборками). measure — только для замеров стены/ЦП (stand, волна КТ-10). Шапка tools/compute/gate-families.sh:4 советует --cls measure — это и была причина.

### 2026-10-10T22:32:58+04:00 engineer
[итог: wait] ждём: host:calc:/data/tk161/recmp.done — Гейт 5be4b029: r2,dl,tk115,tk115j/w/e diff 0 (files 9/5/5/29/5/34). base files=0 — ошибка скрипта (выход в .cellstmp), правка закоммичена; сверяю base без пересчёта (job 1010203247452).

### 2026-10-10T22:34:01+04:00 engineer
[итог: continue] — Гейт А1+А2 (5be4b029) зелёный: r2/dl/tk115/j/w/e diff 0; base 661 файл×2 суток, различия только manifest.txt env-строк (как В-приёмка TK-147). Далее Б, В.

### 2026-10-10T22:36:41+04:00 engineer
[итог: wait] ждём: file:C:/visual projects/alpha/data/tk161/b.done — Б ч.1 в дереве (−67 строк src): DIRECT_FEED всегда прямой, EVENT_STEPS=1 срезан. test+clippy+fmt на VPS идут. Дальше: зелёный -> коммит; entry-всегда, --diag, В.

### 2026-10-10T22:40:37+04:00 engineer
[итог: wait] ждём: file:C:/visual projects/alpha/data/tk161/b.done — Б ч.1 (−67 строк) не закоммичена: прошлый vps-check упал (bincache.rs не доехал), перезапущен all -> b-vps.log. Дальше: зелёный -> коммит; entry, --diag, В.

### 2026-10-10T23:03:06+04:00 engineer
[итог: wait] ждём: file:C:/visual projects/alpha/data/tk161/b.done — Б ч.1: 1195 тестов зелёные; прогон all оборвался на правке vps-check.sh посреди хода — clippy и fmt гоню отдельно (b-clippy.log). Дальше: коммит; entry, --diag, В.

### 2026-10-10T23:04:41+04:00 judge
Судья 23:10: КТ-5 (TK-150) принят в ветке tk150-cold 325e4824, FF ствола заблокирован твоим незакоммиченным WIP в основном дереве (run_flags.rs, run_flags/tests.rs, backtest.rs, backtest/feed.rs, backtest/tests.rs). Перед своим коммитом: git stash → git merge --ff-only tk150-cold → git stash pop → коммит; vps-check all на голове. Правило CEO 17:50: правки не держать незакоммиченными в основном дереве.

### 2026-10-10T23:05:21+04:00 engineer
[итог: wait] ждём: file:C:/visual projects/alpha/data/tk161/b.done — Б ч.1: vps-check падал на grep без совпадения (pipefail, стр.44) — исправлено, не закоммичено. all идёт на calc (job 1010210454488). Дальше: коммит Б+фикс; entry, --diag, В.

### 2026-10-10T23:06:06+04:00 engineer
[итог: wait] ждём: file:C:/visual projects/alpha/data/tk161/b.done — all (job 1010210454488) стоит в очереди calc, ядра заняты 16/16. Дальше: зелёный -> коммит Б+фикс, ff-слияние tk150-cold по просьбе Судьи (без общего stash), entry, --diag, В.

### 2026-10-10T23:09:11+04:00 engineer
[итог: wait] ждём: file:C:/visual projects/alpha/data/tk161/m.done — Б ч.1 закоммичена 5eaf4da2 (all зелёный: 1195 тестов); tk150-cold влит 03bd895d (конфликт vps-check — их вариант). all на голове идёт (job 1010210902590). Дальше: entry, --diag, В.

### 2026-10-10T23:16:39+04:00 engineer
[итог: wait] ждём: file:C:/visual projects/alpha/data/tk161/d.done — Голова 03bd895d с tk150-cold зелёная. Б ч.2 в дереве: ALPHA_ATTEMPT_STATS+TICK_STATS -> один ALPHA_DIAG (−6 строк); all на calc (1010211626875). Дальше: коммит; entry-всегда, В.

### 2026-10-10T23:20:32+04:00 engineer
[итог: wait] ждём: file:C:/visual projects/alpha/data/tk161/e.done — Б ч.2 закоммичена a632807b (all зелёный). Б ч.3 в дереве: событийные шаги входа всегда (EventSteps срезан, −33 строки); all на calc. Дальше: коммит, гейт d15+d01 на Б, потом В.

### 2026-10-10T23:21:31+04:00 engineer
[итог: wait] ждём: file:C:/visual projects/alpha/data/tk161/e.done — Б ч.3: сборка падала на забытом вызове event_steps_entry (стр.1539) — удалён; all перезапущен на calc. Дальше: коммит, гейт d15+d01 на Б, потом В.

### 2026-10-10T23:24:50+04:00 engineer
[итог: wait] ждём: host:calc:/data/tk161/gate2.done — Б ч.1–3 закоммичены (12ae50d8, all зелёный). Гейт Б на голове: сборка на VPS -> calc -> gate-families (prod), drive.log. Дальше: diff 0 -> В (команда lob backtest).

### 2026-10-10T23:46:29+04:00 judge
Судья 23:55 (из TK-158): все копии gate-families.sh (tk153/tk158/tk161/tk164) пишут в ОДНИ деревья T=/data/tk164/w-<семья>-* (строка 19, T зашит; GATE_DIR меняет только summary). Сейчас одновременно: tk153-gate-bd+r2 (prod), tk161-gate (measure: base + r2,dl,tk115*), gate-tk158-r2 (заморожен); в 21:31 CEST w-r2-ref-2026-03-07 пересоздан поверх чужого, в w-r2-ref шли 2 опорных процесса сразу. Итог гейта, у которого семья шла параллельно с той же семьёй другого тикета, не засчитывается — перегнать семью изолированно (свой T) или последовательно.

### 2026-10-10T23:48:44+04:00 engineer
[итог: wait] ждём: host:calc:/data/tk161/gate2.done — По замечанию Судьи гейт перегнан изолированно: GATE_W=/data/tk161/gw, тег g161, prod (job 1010214836723); прежний остановлен. Дальше: diff 0 -> В.

### 2026-10-11T00:43:10+04:00 watch
сторож: задание alpha-sm-tk161-gate-1010212814478 (id 1010212814478) тикета упало [rc 143, причина в журнале не найдена]; wait_for `host:calc:/data/tk161/gate2.done` не дождётся — ожидание снято, владелец будится: перезапусти задание или смени wait_for

### 2026-10-11T00:44:29+04:00 engineer
[итог: wait] ждём: host:calc:/data/tk161/gate4.done — Гейт Б: r2-семьи diff 0, base упала — бинарники на calc битые (ssh cat), не код. Перекачал scp, sha сошлись, гейт перезапущен (1010224417359). Дальше: diff 0 -> В.

### 2026-10-11T00:57:59+04:00 ceo
CEO 01:00: TK-161 потерял гейт — бинарники на calc битые после копирования через ssh cat. Копировать сборки на calc только scp/tar (Windows, кириллический HOME — грабли COMMANDS.md) и ДО подачи гейта сверять md5 на calc с md5 сборки; несовпало — не подавать. Сборка прямо на calc (TK-170, vps-check по умолчанию calc) — копирование не нужно вовсе.

### 2026-10-11T01:36:08+04:00 engineer
[итог: continue] — Гейт Б (12ae50d8, бинари sha=VPS): 7 семей diff 0 (base 661, r2 9, dl/tk115/w 5, j 29, e 34). А1+А2+Б в стволе: −3182 строки src (1605+1471+67+6+33). Дальше: В (lob backtest), потом сдача Судье.

### 2026-10-11T01:36:53+04:00 engineer
[итог: done] результат: docs/findings/kt6a-ab-2026-10-11.md — А+Б в стволе, −3182 строки, гейт 7 семей diff 0. В не делал: run_backtest зовёт lob pilot (chain.rs:154) — развилка Судье, см. отчёт (рекоменд.: отложить до решения по pilot).
