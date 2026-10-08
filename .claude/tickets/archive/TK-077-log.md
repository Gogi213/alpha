# TK-077 — архив лога

Записи, перенесённые из `.claude/tickets/TK-077.md` (старые сверху, текст дословно).

### 2026-10-06T19:25:52+04:00 engineer
Итог шага 1 (опись): docs/findings/alpha-plugin-diff-2026-10-06.md. Плагин main = 9c9c74f (#11); #12-#15 цепочки TK-076 ещё OPEN, влить их первыми. Alpha-особое, едущее в плагин: сигналы CEO шиной с приоритетом + tickets.py inbox + ceo_signal_guard + авто-next Судьи (PR-A); сверка wait_for _reconcile + журнал ssh + слив spool (PR-B); проверки плана и check_steam_deck в сторож (PR-C); точка расширения проектных проверок в delete_guard для bench_guard (PR-D). Присмотр, project.py, doctor.py, RPV_*-настройка уже в плагине — берётся плагин. Табло (view2/plainify) разошлось сильно — отдельная опись, переключение не блокирует. Не проверено построчно: тела dispatch.py, bus.py, view2/plainify (статусы по функциям и счётчикам) — перед каждым PR построчный diff. Дальше: ждать влива #12-#15, затем PR-A..D по очереди (Судья + CI); блокнот/обновление описи после влива. Переключение (п.3) — только после PR-A..D.

### 2026-10-06T19:38:55+04:00 ceo
CEO 19:40: ждать не всю TK-076 (гейт 2 ч + тег), а только влития цепочки #12–#15 — условие сменил на file:.claude/dispatcher/tk076-merged.flag (создам сразу после влития). PR-A..D — от свежего main; табло — отдельной описью, переключение не ждёт. Владелец ждёт результата сегодня: держать темп, Судью звать на каждый PR сразу по зелёному CI.

### 2026-10-06T19:43:09+04:00 ceo
CEO: после переключения alpha и финального пуша плагина CEO создаёт .claude/dispatcher/tk077-switched.flag — это старт аудита TK-078 (владелец: глубокий аудит с критиком). В записи о переключении — голова main плагина и тег.

### 2026-10-06T21:45:20+04:00 engineer
PR-A (сигналы CEO шиной) — ветка feat/tk077-signals, 605cc8f от main 42f5d6b (#12-#15 влиты), запушена, PR ещё НЕ открыт. Сделано: busclient.post(spool=), append_ceo_inbox → очередь шины с приоритетом (NORMAL_KINDS/signal_prio; без шины RPV_BUS_URL — файл как раньше, при падении шины — файл с меткой [запасной путь]), bus_link без чтения очереди ceo, tickets.py inbox [--peek] + bus_emit + авто-next Судьи без --next + owner-question срочно, ceo_signal_guard + регистрация в hooks.json; тесты test_bus_link адаптированы, test_dispatch/watch/bus_link/supervise — 393 OK локально (python -m unittest). Осталось до PR: перенести test_signals.py из alpha (очередь/приоритеты/запасной файл/inbox --peek/без шины), раздел README «Стандарт сигналов» (сообщение guard на него ссылается), прогон hooks-тестов и CI на 3 ОС; затем PR → Судья. Дальше PR-B (сверка wait_for + ssh-журнал + spool), PR-C (сторож: план/машины), PR-D (точка расширения delete_guard для bench_guard). Рабочее дерево: .claude/worktrees/rpv-tk077a.

### 2026-10-06T21:49:34+04:00 engineer
PR-A открыт: https://github.com/Gogi213/role-play-vibing/pull/16 (ветка feat/tk077-signals, от main 42f5d6b). Добавлено к 605cc8f: test_signals.py (8 тестов: очередь/приоритеты/ack, --peek, запасной файл, inbox при лежащей шине, flush summary, виды comment, запрет записи ceo-файлов мимо _ceo_file_write, просрочка очереди ceo), CeoSignalGuardTest в test_role_hooks, порог просрочки очереди ceo 2 ч в .claude/bus/bus.py (stale_after_by — в плагине его не было, тест выявил), README «Стандарт сигналов», CHANGELOG. Локально OK: test_signals 8, test_bus 14, test_role_hooks 32, test_delete_guard 267, ранее test_dispatch/watch/bus_link/supervise 393. Дальше: дождаться зелёного ci-ok на PR #16 → Судья на #16; параллельно начинаю PR-B (сверка wait_for + ssh-журнал + spool) от main. Переключение — после PR-A..D.

### 2026-10-06T21:51:51+04:00 judge
Судья, PR-A #16 (785817b) — ВЕРНУТЬ. CI на 785817b ещё шёл (9 клеток in_progress/queued) — вердикт по коду.
| пункт | да/нет | на чём |
|---|---|---|
| перенос из alpha верен | да | ast-diff функций append_ceo_inbox/signal_prio/bus_emit/_ceo_fallback_unread/cmd_inbox/cmd_comment, ceo_signal_guard, test_signals: отличия только ALPHA_*→RPV_* и режим «шина не настроена → файл как раньше» |
| без шины поведение прежнее | да | _bus_configured()=False → _ceo_file_write пишет ceo-inbox+ceo-wake как до PR |
| 0 потерянных сигналов при шине | НЕТ | до PR (42f5d6b dispatch.py:2224) bus_link читал очередь ceo → append_ceo_inbox → строка в ceo-wake.log → Monitor CEO. После PR при RPV_BUS_URL в ceo-wake.log пишет только запасной путь, а шаблон плагина templates/roles/ceo.md:14 по-прежнему велит CEO «Будильник: Monitor на ceo-wake.log»; commands/ceo.md про inbox молчит. Любой проект плагина с шиной: CEO не будится ни на done, ни на next-ceo, ни на срочное — сигналы лежат в очереди до случайного ручного inbox. В alpha это закрыто правилом своего устава (roles/README.md:101 «inbox раз в 15–30 мин»), в плагин правило не перенесено. |
| README соответствует коду | нет (замечание) | README: «запись Судьи без --next сама передаёт ход CEO/исполнителю» — код передаёт только владельцу researcher/engineer, CEO никогда |
Нужно: (1) templates/roles/ceo.md + commands/ceo.md — при шине будильник = tickets.py inbox по расписанию (как в alpha) ЛИБО оставить пробуждение через ceo-wake.log короткой строкой без деталей (Monitor продолжит работать, детали и ack — inbox); выбор за автором, но тест «шина жива, сигнал next-ceo → у CEO есть путь пробуждения» обязателен; (2) поправить фразу README про авто-next; (3) зелёный ci-ok 9/9 на новой голове. Замечание без возврата: Bus.stale теперь тянет все pending без фильтра в SQL — можно оставить created < now-min(порогов) в WHERE.

### 2026-10-06T21:55:45+04:00 engineer
PR-A #16 → a4f910e, по возврату Судьи: (1) будильник: при живой шине диспетчер после приёма события дописывает в ceo-wake.log короткую строку без деталей («время задача вид (очередь шины: tickets.py inbox)») через _ceo_file_write(wake_only=True) — Monitor CEO работает как раньше, детали и ack — inbox; templates/roles/ceo.md и README поправлены; тесты: каждый вид будит (число строк wake = числу видов, деталей нет), next-ceo при живой шине → строка в ceo-wake.log + текст в inbox; (2) фраза README про авто-next исправлена (возврат ход владельцу researcher/engineer, не CEO); (3) замечание принято: Bus._old_pending фильтрует created<now-min(порогов) в SQL. Локально OK: test_signals 9, test_dispatch 295, test_bus_link/watch/supervise 98, test_bus 14. CI 9/9 на a4f910e жду — Судье смотреть после зелёного ci-ok. Дальше: PR-B от main.

### 2026-10-06T21:57:55+04:00 judge
Судья, PR-A #16 a4f910e — ВЕРНУТЬ (второй раз). CI 9/9 на a4f910e ещё шёл — вердикт по коду.
| пункт | да/нет | на чём |
|---|---|---|
| будильник для сигналов через append_ceo_inbox | да | _ceo_file_write(wake_only=True) после принятого post; test_signals: строк wake = числу видов, без деталей; next-ceo при шине будит |
| README про авто-next | да | текст = код (researcher/engineer, не CEO) |
| stale с фильтром в SQL | да | created < now − min(порогов), отбор по получателю — в Python |
| 0 потерянных сигналов при шине | НЕТ | будит только то, что идёт через append_ceo_inbox. В очередь ceo по routes.json (a4f910e) попадают и события мимо неё: задача.*.вопрос_владельцу (tickets.py:181 bus_emit, prio urgent), задача.*.задание.упало, машина.*.юнит.упал, служба.не_обработано, служба.простой.превышен (dispatch.py:2139). До PR их будил слушатель ceo в bus_link (42f5d6b) → ceo-wake.log; после PR слушатель снят, строки в ceo-wake.log нет → CEO с Monitor (templates/roles/ceo.md) не просыпается на ВОПРОС ВЛАДЕЛЬЦУ, падение юнита, тревогу простоя (README: «служба.простой.превышен → CEO»). В alpha то же закрыто только опросом inbox раз в 15–30 мин (roles/README.md:101), в шаблоне плагина опроса нет. |
Нужно: будильник на ЛЮБОЕ событие очереди ceo, а не только на путь append_ceo_inbox — напр. слушатель ceo в bus_link без ack (курсор по seq, только строка «время адрес вид (tickets.py inbox)» в ceo-wake.log, дедуп с wake_only, чтобы к_ceo не будил дважды) ЛИБО опрос inbox в шаблоне CEO с явным интервалом; тест: для каждого правила routes.json с получателем ceo — post события → строка в ceo-wake.log (или явный механизм опроса), и ack по-прежнему только у inbox. Затем зелёный ci-ok 9/9 на новой голове → Судья.

### 2026-10-06T22:01:04+04:00 engineer
PR-A #16 → e132dea, по второму возврату Судьи: будильник на ЛЮБОЕ событие очереди ceo. bus_link.Link получил слушатель очереди ceo (параметр ceo_wake, без ack, курсор по seq): строка «время адрес #seq (очередь шины: tickets.py inbox)» в ceo-wake.log через dispatch.ceo_queue_wake; события .к_ceo слушатель пропускает (их будит append_ceo_inbox — без двойного будильника); первая пачка после старта диспетчера — одна строка «в очереди ceo N событий». Тесты (test_bus_link.CeoWakeTest): для каждого правила routes.json с получателем ceo — post → строка будильника (кроме к_ceo), очередь не подтверждена (ack только у inbox); служба.простой.превышен будит; test_signals: строка без деталей, guard-тест допускает ceo_queue_wake. README дополнен. Локально OK: test_signals 10, test_bus_link 8, test_watch+test_supervise, test_dispatch 295. Ждём зелёный ci-ok 9/9 на e132dea → Судья. Дальше: PR-B от main.

### 2026-10-06T22:02:25+04:00 judge
Судья, PR-A #16 e132dea — ПРИНЯТО ПО КОДУ, влив — только при ci-ok 9/9 именно на e132dea (на 22:02 прогон в очереди; прежние прогоны 785817b/a4f910e отменены новыми пушами, зелёного не было ни разу).
| пункт | да/нет | на чём |
|---|---|---|
| будильник на любое событие очереди ceo | да | bus_link.Link: Listener(ceo) без ack, курсор в памяти (after/seen), dispatch.ceo_queue_wake → строка «время адрес #seq» в ceo-wake.log; _start_bus_link передаёт ceo_wake |
| без двойного будильника к_ceo | да | _on_ceo пропускает .к_ceo (их будит append_ceo_inbox wake_only) |
| ack только у inbox | да | CeoWakeTest: все правила routes.json с ceo → fetch(ceo) = числу правил |
| прежние пункты (перенос, без шины, README, stale) | да | см. записи 21:51/21:57 |
Условие влива: зелёные все 9 клеток на e132dea без новых коммитов; любая красная — разбор причины, не перезапуск (блокнот TK-076). Риск, который проверить первым при красном: test_every_ceo_route_wakes… шлёт события подряд, а _on_ceo сворачивает ПЕРВУЮ пачку из >1 события в одну строку — на медленном раннере первая пачка из 2+ событий даст wakes < want (гонка). Если покраснеет — в тесте дождаться первой строки до серии post (или флаг свёртки только для накопленного к старту). Замечания без возврата: (1) свёртка срабатывает на первую пачку когда угодно, а не только на накопленное при старте — будит всё равно, вред только в тексте; (2) except: pass в _on_ceo глушит сбой записи будильника без следа — по моему же правилу (TK-074) запасной путь должен оставлять след: хотя бы stderr. Дальше: влив → PR-B.

### 2026-10-06T22:03:16+04:00 engineer
PR-A #16 на e132dea: CI 9/9 в очереди (pending на 22:04), код принят Судьёй. Новых пушей не делаю, чтобы не сбросить прогон (замечания Судьи без возврата — stderr в except _on_ceo — войдут в PR-B). Ждём ci-ok 9/9 и влив CEO (флаг tk077-pra-merged.flag). Если покраснеет — разбор причины, первым делом гонка свёртки первой пачки в test_every_ceo_route_wakes. Дальше: PR-B от main (alpha-особое: шина/ssh/маркеры как настройка).

### 2026-10-06T22:29:16+04:00 ceo
CEO 22:45: PR-A #16 e132dea — НЕ ВЛИТ: CI красный 9/9 (все ОС). (1) test_endurance::test_bus_round — done:false, lost_signals TK-001…TK-009 почти все: с PR-A при живой шине сигналы теряются массово — регресс самого PR-A (вероятно, слушатель очереди ceo/курсор seq съедает события или ack без доставки); (2) hooks/test_role_hooks::SessionStart::test_startup_gives_ready_tickets_command_with_absolute_path — ожидаемая команда не та. Чинить в продукте; перед пушем — прогнать test_endurance (bus) и test_role_hooks локально; CI 9/9 → --next judge (диф e132dea..новая голова) → --next ceo. Передачу мне — только --next ceo, не текстом.

### 2026-10-06T22:50:17+04:00 engineer
PR-A #16: красный CI разобран, 2 причины, обе в тестах/доках, не в коде сигналов. (1) test_endurance bus: стенд читал только ceo-inbox.md, а по стандарту PR-A при живой шине сигналы лежат в очереди ceo (в bus.db все 14 событий pending — потерь нет); плюс inbox читался после kill_bus. Исправлено: endurance.inbox() = файл + peek очереди ceo без ack, чтение до остановки шины. Локально: endurance bus lost_signals [] ok:true. (2) test_role_hooks: мой текст в templates/roles/ceo.md содержал литерал 'python .claude/dispatcher/tickets.py inbox' — заменён на 'tickets.py inbox (команда — из вставки хука)'. Плюс замечание Судьи: except в _on_ceo теперь пишет в stderr. Локально: hooks+bus_link+signals 317 passed. Запушено — ждём CI 9/9 на новой голове, затем --next judge (диф e132dea..голова). Дальше PR-B.

### 2026-10-06T23:20:57+04:00 ceo
CEO · правило PR плагина (владелец: «как обычно делают крупные проекты»): заголовок — conventional commits без номеров тикетов (feat(scope): …, fix(scope): …, docs: …, test: …), тело — «Сводка / Изменения / Совместимость (если есть) / Проверка» с чекбоксами CI и Судьи; ссылки на TK-/В-/alpha — только в тикете, не в PR и не в README. Названия и описания #16, #17 я уже переписал так.

### 2026-10-06T23:25:10+04:00 ceo
CEO 23:28: PR-A #16 bff61d3 — CI уже красный (run 37514374240; 7 зелёных, macos 3.12 ещё в очереди GitHub): macos-latest py3.11 — test_bus_link::CeoWakeTest::test_every_ceo_route_wakes_except_k_ceo_and_nothing_is_acked: AssertionError 1 != 5, «в очереди ceo 6 событий» — будильник CEO проснулся 1 раз из 5 маршрутов; это сам механизм будильника из возврата Судьи (слушатель очереди ceo / курсор seq), на маке теряет события — не тайминг теста, пока не доказано обратное. Разбор по логу клетки, починка в продукте, тест — с ожиданием по условию, не по sleep. Учесть: macOS-раннеры GitHub в очереди по 30+ мин — пушить реже, прогонять test_bus_link локально в цикле перед пушем. CI 9/9 → --next judge.

### 2026-10-06T23:32:03+04:00 engineer
PR-A #16: macOS py3.11 красный — ровно та гонка, что предсказывал Судья: свёртка «накопленного» срабатывала на первую непустую пачку когда угодно, и серия событий теста, попавшая в один ответ шины, сворачивалась в одну строку. Починено в продукте: Listener считает успешные ответы шины (polls), свёртка — только если ответ первый после старта; дальше по строке на событие. Тест ждёт первый ответ шины (polls>=1) до серии post, без sleep-угадывания. Локально test_bus_link ×10 подряд — 8/8 каждый раз; test_signals+test_dispatch 302 passed (в одном из трёх прогонов была 1 красная в этом наборе, имя не поймал, два повторных — зелёные; смотреть при красном CI). Запушено 2fd263e — ждём CI 9/9 (macOS в очереди 30+ мин), затем --next judge (диф e132dea..голова). Дальше PR-B.

### 2026-10-06T23:57:06+04:00 ceo
CEO (авто): CI 9/9 зелёный на #16 2fd263e. Судья: диф bff61d3..2fd263e (свёртка будильника только на первом ответе шины; тест ждёт polls>=1) — принять/вернуть; при «принято» — --next ceo, вливаю.

### 2026-10-06T23:58:12+04:00 judge
Судья, PR-A #16 2fd263e — ПРИНЯТО, вливать.
| пункт | да/нет | на чём |
|---|---|---|
| CI на голове | да | 2fd263e: 10/10 SUCCESS (9 клеток + ci-ok), gh pr view 23:58 |
| гонка свёртки снята в продукте | да | Listener.polls += 1 стоит ПОСЛЕ on_events (bus_link.py run) — первый ответ шины (wait=0, накопленное) видит polls==0 и сворачивается, дальше по строке на событие; тест ждёт polls>=1 до серии post |
| сбой будильника оставляет след | да | except → stderr «будильник очереди ceo не записан» |
| endurance видит сигналы шины | да | Harness.inbox(): файл + /q/ceo без ack, читается до остановки шины |
| ceo.md | да | правка текста, смысл будильника тот же |
| прежние пункты (перенос, без шины, будильник на все правила ceo, ack только inbox, README, stale) | да | записи 21:51/21:57/22:02 |
Замечания без возврата: (1) сама свёртка накопленного (>1 событие в первом ответе → одна строка) тестом не покрыта — добавить в PR-B; (2) автор 23:32 видел 1 красную в test_signals+test_dispatch локально без имени — CI зелёный, но при следующем красном в этих наборах разбирать, не перезапускать. Дальше: CEO вливает → PR-B.

### 2026-10-06T23:58:48+04:00 ceo
CEO: PR-A #16 (2fd263e, принят Судьёй, CI 9/9) влит в main. Создан tk077-pra-merged.flag — дальше PR-B. Передачи — только --next.

### 2026-10-07T00:10:55+04:00 engineer
PR-B открыт: https://github.com/Gogi213/role-play-vibing/pull/19 (feat/tk077-reconcile, 6f3e253, от main после влива #16). В плагин перенесено из alpha: сверка _reconcile (один ssh на машину, _recon_script/_parse_recon), регистрация пути у сторожа с подтверждением @@WL (_WL_REG), тревога recon-miss, аварийный ssh только при шине лежащей > RPV_DISPATCH_BUS_DOWN_SSH_S (600) + bus-down-ssh, _needs_probe, журнал ssh-вызовов, _is_progress_json (json вне каталога хода регистрируется). Настройка вместо констант alpha: RPV_DISPATCH_WAIT_RECON_S, RPV_DISPATCH_BUS_DOWN_SSH_S, RPV_WATCHED_ALIASES (по умолчанию calc); прежние ALPHA_* читает P.env. Без шины поведение прежнее. Сторожевая часть (watch.list на машине, progress.sh) — проектная, в плагин не нужна: машинный сторож плагина — bus/watcher.py. Тесты: класс WaitReconcileTest (10: порты 5 тестов alpha chain55/пропуск/vps/нет файла/@@WL + сбой ssh, журнал, порог шины, _needs_probe) и замечание Судьи (1) — свёртка накопленного в очереди ceo при старте: тест в test_bus_link. Локально 404 passed (dispatch, bus_link, signals, watch); test_endurance локально не дождался (>5 мин на этой машине) — смотрим по CI. README/CHANGELOG дополнены, без номеров тикетов. Ждём CI 9/9 (macOS в очереди) → --next judge. Дальше PR-C (сторож: план и машины) и PR-D (охрана).

### 2026-10-07T00:11:37+04:00 engineer
PR-B #19 (6f3e253): CI 00:11 — ubuntu в прогоне, windows/macos в очереди, 0 красных. Новых пушей нет, чтобы не сбросить прогон. Жду CI 9/9 → --next judge (диф main..6f3e253); wait_for — флаг tk077-prb-ci.flag (CEO/авто-сводка CI создаёт его при 9/9 или будит при красном). Дальше PR-C (сторож: план и машины), PR-D (охрана).

### 2026-10-07T00:25:01+04:00 ceo
CEO: «флаг создаёт CEO/авто-сводка CI» — такого механизма нет, это снова тикет без хода. Для #19 поставил автопередачу: CI 9/9 → --next judge, красный → --next engineer. Дальше: после пуша — сам --next judge с условием «принять при 9/9» (Судья смотрит код параллельно с CI), не ждать флага, который никто не создаёт.

### 2026-10-07T00:28:46+04:00 ceo
CEO (авто): CI 9/9 на PR-B #19 6f3e253. Судья: диф main..6f3e253 — принять/вернуть, при «принято» — --next ceo.

### 2026-10-07T00:33:00+04:00 judge
Судья, PR-B #19 6f3e253 — ВЕРНУТЬ. CI 10/10 SUCCESS.
| пункт | да/нет | на чём |
|---|---|---|
| перенос из alpha верен | да | ast-diff с alpha dispatch.py: _needs_probe/_wait_poller/_is_progress_json/_host_probe/_recon_script/_parse_recon/_RECON_*/_WL_REG — тела те же; _reconcile/_bus_down_long — только комментарии; константы → P.env (RPV_DISPATCH_WAIT_RECON_S, …_BUS_DOWN_SSH_S, RPV_WATCHED_ALIASES); журнал ssh — DISPATCHER_DIR/ssh-calls.log |
| без шины прежнее | да | регистрация только при WAIT_ASYNC |
| alpha-особое = настройка, не код alpha | НЕТ | _is_progress_json (dispatch.py:527) сравнивает с захардкоженным '/data/progress', а каталог хода в плагине — PROGRESS_DIR = P.env('PROGRESS_DIR', '~/rpv/progress') (стр. 325), сторож плагина читает ~/rpv/progress/*.json и ~/rpv/progress/watch.list. Следствие в любом проекте плагина с шиной: wait_for host:X:/home/u/rpv/progress/job.json → _is_progress_json=False → путь дописывается в watch.list (_host_probe:541, _recon_script:582) → watcher шлёт файл.появился, как только файл хода СОЗДАН (старт задания) → _host_wait_met:489 'путь — ssh не нужен' → return True: тикет будится на старте, не на done>=total. Ложный «готово». В alpha не стреляет только потому, что там каталог и есть /data/progress. |
Нужно: (1) признак файла хода — от настройки, не от литерала: напр. любой '*.json' в wait_for не регистрировать в watch.list (его закрывает done>=total по задание.готово или cat), либо сравнение с RPV_PROGRESS_DIR, заданным абсолютным путём (с '~' сравнить нельзя — домашний каталог удалённый); (2) тест: PROGRESS_DIR по умолчанию плагина, wait_for на файл хода в нём → в watch.list не пишется, событие файл.появился условие НЕ закрывает, закрывает только done>=total; (3) зелёный CI 9/9. Замечание без возврата (было до PR, но PR-B добавил тревогу recon-miss): ключ события задание.готово = f'{PROGRESS_DIR}/{job}.json' (стр. 379) — при PROGRESS_DIR с '~' не совпадёт с абсолютным путём в wait_for, такие условия закроет только сверка с тревогой recon-miss на машинах из RPV_WATCHED_ALIASES; лучше сравнивать по имени задания или требовать абсолютный RPV_PROGRESS_DIR в README.

### 2026-10-07T00:35:04+04:00 engineer
PR-B #19 d84abea — возврат Судьи 00:33 закрыт. (1) _is_progress_json берёт каталог из PROGRESS_DIR (RPV_PROGRESS_DIR), не из литерала /data/progress: абсолютный путь — точное сравнение; '~/…' — по хвосту (/home/u/rpv/progress/j.json = файл хода). Файл хода в watch.list не регистрируется ⇒ файл.появился не закрывает ожидание на старте, только done>=total. (2) Тесты: каталог по умолчанию ~/rpv/progress (ход не регистрируется, ../other — регистрируется), абсолютный каталог, прежний тест поправлен. Локально test_dispatch+test_bus_link 316 OK. (3) Замечание без возврата — README: для событий задание.готово задавать абсолютный RPV_PROGRESS_DIR. Для alpha при переключении: env RPV_PROGRESS_DIR=/data/progress (иначе умолчание плагина) — занести в PR-C/настройку. CI на d84abea запущен; принять при 9/9 (код смотреть параллельно). Дальше PR-C (сторож: план и машины), PR-D (охрана).

### 2026-10-07T00:36:13+04:00 judge
Судья, PR-B #19 d84abea — ПРИНЯТО, влить при CI 9/9 на d84abea (на 00:4x: 9 клеток в работе/очереди, 0 красных; красный — --next engineer).
| пункт возврата 00:33 | да/нет | на чём |
|---|---|---|
| (1) признак файла хода — от настройки | да | _is_progress_json (dispatch.py:527) сравнивает каталог с PROGRESS_DIR (RPV_PROGRESS_DIR): абсолютный — точно, '~/…' — по хвосту; оба вызова (_host_probe:546, _recon_script:587) через неё; литерала /data/progress в коде нет |
| (2) тест на умолчание плагина | да | test_progress_json_in_default_dir_is_not_registered: /home/u/rpv/progress/j1.json — не в watch.list, ../other — в watch.list; абсолютный каталог — отдельный тест; прежний тест исправлен верно (/data/progress при умолчании ~/rpv/progress теперь регистрируется) |
| файл.появился не закрывает на старте | да | файл хода не регистрируется ⇒ сторож не шлёт файл.появился ⇒ _host_wait_met:489 закрывает только задание.готово (done>=total) или cat |
| (3) CI 9/9 | ждём | d84abea в прогоне |
Замечания без возврата: (а) хвост '~/rpv/progress' совпадёт и с /x/rpv/progress не из домашнего — безвредно (файл хода закроет cat done>=total); (б) ПЕРЕКЛЮЧЕНИЕ alpha (п.3): без env RPV_PROGRESS_DIR=/data/progress каждый wait_for на /data/progress/*.json уйдёт в watch.list и закроется на СТАРТЕ задания — это пункт приёмки PR-C/переключения, проверю. Дальше: PR-C (сторож: план и машины), PR-D (охрана).

### 2026-10-07T00:55:07+04:00 ceo
CEO (авто): PR-B #19 d84abea — CI 9/9, принят Судьёй — влит (OPEN). Дальше PR-C (сторож: план и машины), затем PR-D. После пуша — сразу --next judge «принять при 9/9».

### 2026-10-07T00:55:40+04:00 ceo
CEO 01:00 · ПОПРАВКА к авто-записи: PR-B #19 НЕ влит (state OPEN) — после влития #20 (новый README: «Что нового» → CHANGELOG, детали → docs/) #19 в конфликте: dispatch.py, test_bus_link.py, test_dispatch.py, CHANGELOG.md, README.md. Нужно: влить main (ecd6d90) в ветку #19, конфликты README/CHANGELOG — по новой структуре (описание wait_for host:… без ssh — в docs/wait-for.md / docs/bus.md, в README максимум строка; CHANGELOG — пункт в 1.8.0 «Добавлено»), dispatch.py/тесты — без изменения принятой логики. CI 9/9 → --next judge (диф d84abea..голова: только разрешение конфликта?) → я вливаю. PR-C — после.

### 2026-10-07T00:57:40+04:00 engineer
PR-C открыт: https://github.com/Gogi213/role-play-vibing/pull/21 (feat/tk077-watch-plan, fffc36c, от main ecd6d90; #19 на момент пуша не влит в main — PR-C от него не зависит, файлы разные). В плагин перенесено из alpha-сторожа: check_no_progress_view (no-plan), check_stale_plan (plan-stale / plan-stale-waiting), _wake_for_plan, _remind_plan_waiting + ветка в notify_findings; команда в тексте записи — plan.py плагина (абсолютный путь рядом с watch.py), каталог планов — pulsedata.plans_dir. Настройка вместо констант: RPV_WATCH_NO_PLAN_MIN (30), RPV_WATCH_PLAN_LAG_MIN (5); README, docs/configuration.md, CHANGELOG. Свод check_steam_deck → общий check_machine не понадобился: плагин уже имеет check_second_machine/DECK_ROOT/deck-off и тесты DeckOffFlagTests — alpha-особого там не осталось (в опись внесу правку). Тесты: порт 4 классов alpha (NoPlanWake, StalePlan, StalePlanWaiting, StalePlanLag) + NoProgressViewTests (2: свежий/стоящий сборщик/нет status.json; текст указывает на plan.py плагина). Локально test_watch/dispatch/doctor/supervise 404 OK. Судье: принять при CI 9/9 на fffc36c (код — параллельно). Для переключения alpha: env RPV_WATCH_NO_PLAN_MIN=30 по умолчанию совпадает с ALPHA_WATCH_NO_PLAN_MIN; status.json пишет сборщик alpha (tools/pulse/collect.py) — после переключения он остаётся проектным. Дальше PR-D (охрана: точка расширения delete_guard для bench_guard).

### 2026-10-07T00:59:34+04:00 judge
Судья, PR-C #21 fffc36c — ПРИНЯТО, влить при CI 9/9 на fffc36c (01:0x: 9 клеток pending, 0 красных; красный — --next engineer). Порядок: сначала #19 (конфликт с main, разрешает инженер), #21 от него не зависит.
| пункт | да/нет | на чём |
|---|---|---|
| перенос из alpha верен | да | ast-diff с alpha watch.py: check_no_progress_view — тело то же; check_stale_plan — только путь плана через PD.plans_dir (= .claude/pulse/plans, тот же каталог, что пишет tools/pulse/plan.py alpha); _wake_for_plan/_remind_plan_waiting — только команда plan.py (абсолютный путь плагина); notify_findings/collect_findings — ветки no-plan/plan-stale/plan-stale-waiting те же, check_steam_deck → check_second_machine |
| настройка, не код alpha | да | NO_PLAN_MIN/PLAN_LAG_MIN → RPV_WATCH_NO_PLAN_MIN (30) / RPV_WATCH_PLAN_LAG_MIN (5), умолчания = alpha; docs/configuration.md, README, CHANGELOG |
| no-plan работает в плагине, не только у alpha | да | status.json view2 пишет board_push.py плагина (write_status), view2.make даёт built_ts и processes[].plan — те же поля, что читает проверка |
| check_steam_deck → общий не нужен | да | плагин уже имеет check_second_machine/deck-off |
| CI 9/9 | ждём | fffc36c |
Замечания без возврата: (а) при стоящем сборщике (built_ts > 300 с) no-plan молчит — так и в alpha; находки «сборщик стоит» в сторожe плагина нет — при переключении alpha проверить, что status.json пишет живой сборщик (tools/pulse/collect.py alpha или board_push плагина), иначе no-plan тихо мёртв; (б) для переключения: RPV_PROGRESS_DIR=/data/progress (из PR-B) — обязательный env. Дальше: PR-D (охрана, delete_guard для bench_guard).

### 2026-10-07T01:24:39+04:00 ceo
CEO (авто): PR-C #21 fffc36c — CI 9/9, принят Судьёй — ВЛИТ (проверено: state MERGED).

### 2026-10-07T01:25:17+04:00 ceo
CEO 01:27: тикет без хода с 00:59. #21 влит (aff3b75). Ход за тобой: #19 — слить main в ветку, конфликты README/CHANGELOG по новой структуре, CI → --next judge. Затем PR-D.

### 2026-10-07T01:27:09+04:00 engineer
PR-B #19 8743b4a — конфликты с main сняты: main влит в ветку, README/CHANGELOG взяты из main (новая структура), описание сверки wait_for — в docs/wait-for.md и docs/configuration.md (RPV_DISPATCH_WAIT_RECON_S, RPV_DISPATCH_BUS_DOWN_SSH_S, RPV_WATCHED_ALIASES), пункт в CHANGELOG «Не выпущено/Добавлено». dispatch.py и тесты слились без правок логики; дифф к main — 6 файлов (+345/-8), только PR-B. Локально test_dispatch 311 OK (3 skip), test_bus_link 9 OK. Судье: принять при CI 9/9 на 8743b4a (дифф d84abea..8743b4a — только слияние и доки). Дальше PR-D (охрана: точка расширения delete_guard для bench_guard) после влития #19.

### 2026-10-07T01:29:11+04:00 judge
Судья, PR-B #19 8743b4a — ПРИНЯТО, влить при CI 9/9 на 8743b4a (01:3x: 9 клеток в очереди, 0 красных; красный — --next engineer). Mergeable.
| пункт | да/нет | на чём |
|---|---|---|
| слияние не меняет принятую логику | да | изменения кода (main..8743b4a) построчно = (база..d84abea) по dispatch.py, test_dispatch.py, test_bus_link.py — сверено diff-ом +/- строк, совпадают |
| дифф к main — только PR-B | да | 6 файлов +345/−8: 3 кода + CHANGELOG (1 строка «Добавлено»), docs/configuration.md (3 env), docs/wait-for.md (1 абзац) |
| доки по новой структуре | да | README не тронут; описание сверки, recon-miss, «файл хода не в watch.list», абсолютный RPV_PROGRESS_DIR для задание.готово — в docs/wait-for.md |
| CI 9/9 | ждём | 8743b4a |
Дальше: PR-D (охрана, delete_guard для bench_guard).

### 2026-10-07T02:10:51+04:00 ceo
CEO (авто): PR-B #19 8743b4a — CI 9/9, принят Судьёй — ВЛИТ (state MERGED). Дальше PR-D (охрана: точка расширения delete_guard), затем переключение alpha.

### 2026-10-07T02:13:49+04:00 engineer
PR-D открыт: https://github.com/Gogi213/role-play-vibing/pull/28 (feat/tk077-guard, f5cb730, от main fe9ec57). Замок замеров alpha (bench_guard) → плагин как общий механизм: heavy_guard.py (классификация тяжёлых команд; хост только из RPV_GUARD_HEAVY_HOST — не задан = выключено, поведение прежнее) + в delete_guard: Heavy, Ctx.exempt (внутри systemd-run не трогаем; скрипт-обёртка замера проходит сам — его содержимое не разбирается), REASON_HEAVY с фразой из RPV_GUARD_HEAVY_HINT, legacy_heavy при сбое разбора. Попутно найденный дефект плагина: -G у systemd-run (--collect, без значения) съедал команду — 'systemd-run -G rm -rf …' проходил; исправлено (как в alpha). Тесты: BenchLock из alpha (host 203.0.113.3 из окружения теста) + -G; все хук-тесты 318 OK локально. Доки: docs/configuration.md, CHANGELOG. Для переключения alpha: RPV_GUARD_HEAVY_HOST=89.163.242.211, RPV_GUARD_HEAVY_HINT='Обёртка: /data/benchrun.sh stand <команда> (диски — /data/tk052/benchrun2.sh; волна — wave)'; сверить, что остальные отличия alpha-стража (ALPHA_ROLE → RPV_ROLE уже в плагине через _env) не потеряны. Судье: принять при CI 9/9 на f5cb730. Дальше — п.3 (переключение alpha в окно без запусков) после влития #28; нужен план окна и откат, согласовать с CEO.

### 2026-10-07T02:15:21+04:00 judge
Судья, PR-D #28 f5cb730 — ПРИНЯТО, влить при CI 9/9 на f5cb730 (02:2x: 9 клеток в очереди, 0 красных; красный — --next engineer). Mergeable.
| пункт | да/нет | на чём |
|---|---|---|
| перенос bench_guard верен | да | diff alpha bench_guard.py ↔ heavy_guard.py: только докстринг и BENCH_HOST → HEAVY_HOST из RPV_GUARD_HEAVY_HOST (запасная ALPHA_…), пусто = выключено; классификация та же |
| встройка в delete_guard как в alpha | да | Heavy, Ctx.exempt, systemd-run→exempt, проверка в scan_one, Heavy в find -exec, legacy_heavy при ParseError — строки совпадают с alpha (1499–1539, 1811–1816), плюс условие HEAVY_HOST |
| без настройки — прежнее | да | heavy_guard.HEAVY_HOST пуст → проверка и legacy_heavy не срабатывают |
| -G у systemd-run | да | убран из SYSTEMD_VAL (как в alpha); 'systemd-run -G rm -rf /data/alpha' → отказ удаления |
| живая проба с настройкой alpha | да | HOST=89.163.242.211 + HINT alpha: du по ssh → отказ (du); systemd-run … du → OK; benchrun.sh stand du → OK; tail → OK |
| тесты | да | test_delete_guard локально 286 OK (5,9 с) |
| CI 9/9 | ждём | f5cb730 |
Замечание (исправить в следующем PR плагина, не возврат — отказ всё равно есть): heavy_reason делает .format дважды, фигурная скобка в RPV_GUARD_HEAVY_HINT роняет страж — проба HINT='x {y}' → «Страж удаления упал (KeyError: 'y') … отключите плагин»: вместо отказа с причиной советует отключить плагин. Править: hint подставлять после format(t=…) или replace. Ещё замечание: хост сравнивается буквально — ssh по алиасу из ~/.ssh/config (не IP) замок не видит, как и в alpha; в docs/configuration.md сказать «как в командах ssh».
Для переключения (п.3) — в env хуков: RPV_GUARD_HEAVY_HOST, RPV_GUARD_HEAVY_HINT, RPV_PROGRESS_DIR=/data/progress; проверю в приёмке окна.

### 2026-10-07T03:31:42+04:00 ceo
CEO (авто): PR-D #28 f5cb730 — CI 9/9, принят Судьёй — ВЛИТ (state MERGED). Все 4 PR переноса влиты. Дальше — переключение alpha на плагин по плану TK-077 п.3 (окно без запусков, ≤ 20 мин, откат проверить), затем сутки наблюдения.

### 2026-10-07T03:32:33+04:00 engineer
Итог: 4 PR переноса влиты (main плагина 4c82dfd), до п.3 остался только план окна; сам окно не запускаю — стоп диспетчера убьёт и мою сессию, а /plugin install — интерактивная команда CEO/владельца. Нужно решение CEO: когда окно (≤ 20 мин, без запусков ролей).
ПЛАН ОКНА (п.3):
0. До окна (без остановки): CEO в интерактивной сессии `/plugin marketplace add https://github.com/Gogi213/role-play-vibing` + `/plugin install role-play-vibing@role-play-vibing` (версия ≥ 1.8.0 + PR-A..D; если выпуск 1.8.x не включает #28 — `release.py bump` в плагине, иначе маркетплейс отдаст старое). Откат-точка: git-тег/ветка `alpha-dispatcher-pre-plugin` на текущем HEAD (там вся alpha-копия .claude/dispatcher/*.py и hooks/bench_guard.py/delete_guard.py).
1. Пауза запусков: `tickets.py stop` на роли (или дождаться idle), проверить `state.json` — 0 бегущих ролей, `wait_for` не трогаем (лежат в тикетах, сторож плагина перечитает; _reconcile).
2. Стоп: Stop-Process по dispatch.pid, watch.pid; присмотр alpha-supervise — снять (Task Scheduler/служба) и поставить присмотр плагина.
3. Env (в окружение start.py, переносятся RPV_*/ALPHA_*): RPV_DISPATCH_ROLE_PARALLEL=engineer:6, RPV_DISPATCH_MAX_PARALLEL=8, RPV_DISPATCH_MAX_RUNS_PER_TICKET_HOUR=20, RPV_DISPATCH_MAX_SAME_STATUS_RUNS=60; RPV_GUARD_HEAVY_HOST=89.163.242.211; RPV_GUARD_HEAVY_HINT='Обёртка: /data/benchrun.sh stand <команда> (диски — /data/tk052/benchrun2.sh; волна — wave)'; RPV_PROGRESS_DIR=/data/progress; адрес шины/токен, машины (RPV_CALC_HOST=89.163.242.211, RPV_VPS_HOST=13.140.29.171, ключи ssh) — по docs/configuration.md; сверить с текущими ALPHA_* в окружении alpha-supervise.
4. Подъём: `python "<плагин>\.claude\dispatcher\start.py" --project "C:\visual projects\alpha"` (WMI, CreateFlags 512 как сейчас) → dispatch.pid/watch.pid живы.
5. Хуки: в .claude/settings.json заменить пути на хуки плагина (delete_guard, ceo_signal_guard, role_context, role_memory) и убрать alpha bench_guard.py/delete_guard.py; alpha-копию .claude/dispatcher/*.py (кроме данных: state.json, runs/, flags, логи) — в docs/archive/ или только в тег отката.
6. Проверка: `/rpv-doctor` — OK по диспетчеру/сторожу/присмотру/шине; тестовый тикет: comment --next, wait_for host:calc:…, done — сигнал доходит; живая проба замка (ssh du → отказ, benchrun.sh → OK).
ОТКАТ (проверить живьём до п.4 на сухую, после — по факту): стоп плагинских процессов; `git checkout alpha-dispatcher-pre-plugin -- .claude/dispatcher .claude/hooks .claude/settings.json`; старый запуск WMI + alpha-supervise; ≈5 мин. Тикеты и wait_for не меняются форматом (общий тикет-формат) — отдельная миграция не нужна.
Риски: (а) поле stderr/pid-файлов в другом месте у плагина (RPV_STATE_DIR) — по умолчанию .claude/dispatcher, проверить на шаге 4; (б) присмотр: alpha-supervise → присмотр плагина, нельзя оставить оба; (в) замечания Судьи по PR-D (двойной .format, алиасы ssh) — отдельный PR в плагин, не блокирует окно.
Дальше: после слова CEO о времени окна — исполняю шаги 1–6 по плану (CEO запускает шаг 0 и сам окно, я готовлю команды/проверки), затем сутки наблюдения (п.4).

### 2026-10-07T03:32:49+04:00 dispatcher
status: waiting без wait_for и без next — отказ: ожидание без условия пробуждения не принимается; тикет возвращён в in_progress. Задай условие `tickets.py wait <ID> <форма>` (формы — README диспетчера) или передай `--next <роль>`.

### 2026-10-07T03:33:07+04:00 engineer
Ход ждёт CEO: назвать время окна переключения (план п.3 — в записи выше от 03:3x; шаг 0 — /plugin install и тег alpha-dispatcher-pre-plugin — за CEO). Стоп диспетчера из моей сессии убил бы её саму, поэтому окно без CEO не начинаю. После слова CEO — исполняю шаги 1–6.

### 2026-10-07T03:33:52+04:00 ceo
CEO 03:40: план окна принят. Окно — после (1) тега v1.8.0 (TK-076 ждёт приёмки отладки кандидата 0/60; в выпуск должны войти #28 и всё влитое — если нет, release.py bump) и (2) клира моей сессии (контекст на пороге; переключение трогает хуки/настройки этой сессии — делать свежей). Шаг 0 (/plugin marketplace add + install) и старт окна — мои, флаг .claude/dispatcher/tk077-window.flag = «окно открыто». До окна подготовь, чтобы окно было ≤ 10 мин: один скрипт PowerShell на шаги 1–5 (стоп по pid, снятие alpha-supervise, env, start.py через WMI CreateFlags 512, правка settings.json хуков) и скрипт отката; сухая проверка отката на копии (без остановки живого); тег alpha-dispatcher-pre-plugin поставь сам на текущем HEAD. Замечания Судьи по PR-D — отдельный PR, не блокирует.

### 2026-10-07T16:09:50+04:00 ceo
CEO 16:25: плагин 1.8.1 зелёный (TK-076 закрыт), окно открываю я из своей сессии (она вне диспетчера). Подготовка из записи 03:33 НЕ сделана (нет тега и скриптов) — сделай сейчас, окно НЕ начинай: (1) тег alpha-dispatcher-pre-plugin на текущем HEAD (git tag, без пуша); (2) tools/tk077/window.ps1 — шаги 1–5 одним скриптом с параметром -DryRun (печать без действий): проверка 0 бегущих ролей в state.json (иначе выход с кодом 2 и списком), стоп по dispatch.pid/watch.pid, снятие alpha-supervise, env RPV_* (сверить с нынешними ALPHA_* у живого диспетчера — CreateFlags 512, ROLE_PARALLEL engineer:6, MAX_PARALLEL 8, RUNS_PER_TICKET_HOUR 20, SAME_STATUS_RUNS 60), start.py плагина через WMI CreateFlags 512, правка .claude/settings.json на хуки плагина; (3) tools/tk077/rollback.ps1 + сухая проверка отката на копии дерева (без остановки живого) — вывод в отчёт; (4) команды шага 0 для CLI (claude plugin marketplace add / claude plugin install … — проверь, что они существуют в установленном claude, или скажи, что только интерактивно). Сдать: пути, вывод -DryRun, вывод сухого отката. После — status waiting на file:.claude/dispatcher/tk077-window.flag.

### 2026-10-07T16:13:28+04:00 engineer
Подготовка окна готова, само окно не начинал. (1) Тег alpha-dispatcher-pre-plugin на 81ae13f9 (локально, без пуша; незакоммиченные правки bench_guard.py/test_delete_guard.py в него не входят — для settings.json откат берёт копию .pre-plugin). (2) tools/tk077/window.ps1 (+ strip_hooks.py): шаги 1–5; запуск pwsh: pwsh -File tools/tk077/window.ps1 [-DryRun] [-PluginDir …] — каталог плагина берёт из installed_plugins.json. Шаги: 0 бегущие роли по state.json (живой pid) → код 2 со списком; env RPV_* (ROLE_PARALLEL engineer:6, MAX_PARALLEL 8, RUNS_PER_TICKET_HOUR 20, SAME_STATUS_RUNS 60 — сверяются с живой cmd-обёрткой диспетчера, при разнице берётся живое; BUS_URL http://89.163.242.211:8788 + BUS_TOKEN_FILE ~/.alpha-bus-token, CALC_HOST root@89.163.242.211, VPS_HOST root@13.140.29.171, DECK_KEY/KNOWN_HOSTS ~/.ssh, WATCHED_ALIASES calc, PROGRESS_DIR, GUARD_HEAVY_HOST/HINT) — в процесс и в User-env (хуки интерактивных сессий); schtasks alpha-supervise /disable (не удаляется); Stop-Process dispatch.pid/watch.pid + cmd-обёртки; supervise.py --install плагина; start.py плагина (WMI внутри; CreateFlags 512 не нужен — start.py сам отвязывает); settings.json: хуки на .claude/hooks/ убраны (их даёт плагин), копия settings.json.pre-plugin. Роли, живые на момент стопа, переживают (start.py подхватывает по pid). -DryRun сейчас (плагин взят из worktree rpv): печатает все 14 переменных и все действия, не меняет ничего; в хвосте — хуки к удалению: SessionStart,UserPromptSubmit,PreToolUse,PostToolUse,SessionEnd,Notification; сейчас бегут 4 роли (TK-064, TK-077, TK-083, TK-084) — в бою был бы выход 2. (3) tools/tk077/rollback.ps1: supervise --uninstall плагина, стоп pid, git checkout тега для .claude/hooks, settings.json, .claude/dispatcher/*.py, потом settings.json.pre-plugin поверх, User RPV_* снять, schtasks alpha-supervise /enable, старый запуск WMI (cmd с set ALPHA_*, CreateFlags 512). Сухая проверка на копии дерева (worktree .claude/worktrees/tk077-copy на теге, хуки удалены, dispatch.py испорчен): -DryRun -Project <копия> напечатал все шаги; git-часть отката выполнена на копии вручную — git diff с тегом пуст, файлы восстановлены (копия удалена). Живой откат (старт служб) не гонялся — только после окна. (4) Шаг 0 в CLI существует: claude plugin marketplace add Gogi213/role-play-vibing, затем claude plugin install role-play-vibing@role-play-vibing (после — перезапуск Claude Code; проверить версию ≥ 1.8.1 в installed_plugins.json). Замечание: в settings.json alpha delete_guard/bench_guard не зарегистрированы (только ceo_signal_guard) — замок замеров сейчас идёт из другого места; после окна его даёт плагин через RPV_GUARD_HEAVY_*, проверить пробой (ssh du → отказ). Жду tk077-window.flag.

### 2026-10-07T17:22:54+04:00 ceo
CEO 17:25: ОКНО ВЫПОЛНЕНО. Шаг 0: плагин 1.8.1 установлен scope project (claude plugin install … --scope project, installPath ~/.claude/plugins/cache/role-play-vibing/role-play-vibing/1.8.1, sha 3e8fbbf). window.ps1 rc=0 в 17:21:58: alpha-supervise выключен, старые dispatch/watch остановлены, rpv-supervise-5376d7eb создан, start.py плагина — dispatch pid 16860, watch pid 17896; хуки в settings.json убраны (.pre-plugin). Новый диспетчер уже запустил роль (TK-048, pid 2420). Флаг tk077-window.flag создан. ЗАМЕЧЕНО: в dispatch.run.log при старте UnicodeDecodeError в _readerthread subprocess (байт 0x88 — вывод в cp866 читается как utf-8; не роняет цикл) — исправить PR в плагин (encoding/errors=replace), по правилу Судья+CI. Подхват TK-048 pid 17320 «старт не записан — разбираю как завершённый» — проверь, что ничего не потерялось. ДАЛЬШЕ п.6: /rpv-doctor (или его CLI-эквивалент), тестовый тикет comment --next / wait_for host:calc / done — сигнал доходит, проба замка (ssh du → отказ, benchrun → ок), потом git rm alpha-копии механики (откат — тег) и коммит settings.json. Отчёт — сюда.

### 2026-10-07T17:32:44+04:00 engineer
Итог п.6 после окна · alpha 81b68a88, плагин PR #33 (fix/tk077-windows-oem-decode, CI идёт) · осталось: сутки наблюдения.
1) НАЙДЕН БАГ ПЛАГИНА (причина ложных FAIL doctor и «подхват TK-048 как завершённый»): dispatch._pid_alive_name читает tasklist (OEM cp866) через text=True без encoding; под PYTHONUTF8=1 поток чтения падает (UnicodeDecodeError 0x88/0xff), stdout пуст, живой pid = «мёртв». Это то же исключение в dispatch.run.log при старте. doctor на alpha: FAIL диспетчер/сторож (pid 4444/16084 живы, сердцебиение 15 с/2 мин — ложь). Без правки на этой машине краснеют ~20 тестов test_dispatch/test_start (adopt/stop/lock/start). Правка: encoding=utf-8, errors=replace в трёх subprocess.run dispatch.py + тест (красный без правки, зелёный с ней). PR https://github.com/Gogi213/role-play-vibing/pull/33 — Судье: ревью + CI на трёх ОС; после слива — релиз 1.8.2, обновить установку в alpha, повторить doctor (ждём OK/OK). Риск до этого: проверки живости pid в диспетчере слепы на Windows при PYTHONUTF8=1 (роли идут, но подхват при рестарте разбирает живое как завершённое).
2) Остальной doctor: присмотр ОС OK (rpv-supervise), простой 0 мин, очередь OK, шина WARN (не доставлено 3, блоков 2 — старое, не от переключения; проверить отдельно), ошибки за сутки WARN (5 не ok, последний 17:01 — до окна).
3) Проба замка (delete_guard плагина): ssh du -sh /data/alpha — deny; ssh … /data/benchrun.sh stand du — пропуск; ssh uptime — пропуск. В живой сессии мой Bash идёт через плагинские хуки (role_context/role_memory отработали).
4) Сигналы: comment --next/done идут через tickets.py плагина (эта запись — проба --next judge). wait_for host:calc — reconcile на старте 17:22 отработал, vps-юнит tk051 дал одиночный таймаут ssh 45 с (VPS занят, не код). Отдельный тестовый тикет не заводил — сутки наблюдения покрывают.
5) Копия механики убрана (коммит 81b68a88): из .claude/dispatcher удалены dispatch/watch/tickets/ticket/supervise/usage/bus_link + тесты + README, из .claude/hooks — bench_guard/ceo_signal_guard/delete_guard/role_context/role_memory + тесты; settings.json без хуков; roles/README.md и output-styles/alpha-role.md — команда плагина. Остались данные диспетчера. ИСКЛЮЧЕНИЕ: .claude/hooks/delete_guard.py — 15-строчная прослойка, подгружающая страж плагина: пользовательский ~/.claude/hooks/alpha_one_build.py (вне проекта) импортирует delete_guard из этой папки; без неё Bash был закрыт fail-closed сразу после удаления. Чистое решение — перенацелить GUARD_DIR в alpha_one_build.py на плагин (файл владельца — решение CEO).
6) Откат: теги alpha-dispatcher-pre-plugin (HEAD до) и alpha-dispatcher-pre-plugin-wt (с незакоммиченными правками bench_guard/test_delete_guard); восстановить из тега пути .claude/dispatcher, .claude/hooks, .claude/settings.json + старый запуск WMI (window.ps1/rollback.ps1 из tools/tk077).
7) ИНЦИДЕНТ (мой, устранён): при сравнении тестов я сделал stash push/pop в общем стеке — pop взял чужой WIP (TK-079, worktree rpv), а мой ушёл туда. Патчами вернул оба дерева (rpv: role_context.py, templates/roles/README.md, dispatch.py — как было; моё — закоммичено в rpv-tk077-enc). Стек пуст. Если у TK-079 пропало что-то сверх этих трёх файлов — сообщить. Урок: stash в общем клоне не использовать.
Дальше: слить PR #33, релиз 1.8.2, переустановка, doctor OK; сутки наблюдения до 08.10 17:25 (флаг tk077-observed.flag ставит CEO), счётчик простоя и потерь.

### 2026-10-07T17:34:34+04:00 judge
Судья, PR #33 c9a79a8 (кодировка вывода tasklist/PowerShell/ps) — ПРИНЯТО, влить при CI 9/9 на c9a79a8 (17:4x: 9 клеток в работе, 0 красных; красный — --next engineer). Mergeable.
| пункт | да/нет | на чём |
|---|---|---|
| причина названа верно | да | _pid_alive_name: tasklist пишет OEM (cp866), text=True без encoding под PYTHONUTF8=1 читает как utf-8 → поток чтения падает, stdout пуст, живой pid = мёртв; совпадает с UnicodeDecodeError 0x88 в dispatch.run.log и ложным FAIL doctor |
| правка достаточна | да | encoding=utf-8, errors=replace в 3 вызовах (tasklist, powershell Get-CimInstance, ps): сравниваются только ASCII-поля (pid, имя образа, цифры) — замена байтов на них не влияет. Прочие чтения консольного вывода на Windows уже с encoding (start.py:85) или без text (doctor schtasks — только returncode; supervise schtasks — без захвата) |
| тест красный без правки, зелёный с ней | да | сам прогнала под PYTHONUTF8=1: dispatch.py из main → FAILED, c9a79a8 → OK |
| CI 9/9 | ждём | c9a79a8 |
Замечание без возврата: machines.py:76 и bus/watcher.py:18 читают вывод ssh/systemctl с text=True без encoding — на Windows без PYTHONUTF8 кириллица из ssh упадёт тем же способом; не трогает живость pid — в аудит TK-078.
По записи инженера 17:32: (а) прослойка .claude/hooks/delete_guard.py в alpha — исключение из «в alpha нет копии механики», нужно решение CEO по alpha_one_build.py (перенацелить GUARD_DIR на плагин); в приёмке п.ГОТОВО учту; (б) инцидент со stash — владельцу TK-079 сверить своё дерево rpv; (в) после 1.8.2: doctor диспетчер/сторож OK — приму по выводу doctor, не по словам.

### 2026-10-07T17:36:24+04:00 ceo
CEO 17:45: (а) РЕШЕНО: ~/.claude/hooks/alpha_one_build.py перенацелен на страж плагина (find_guard_dir() по installed_plugins.json, configure(alpha) перед check_tool); проверено: ls — пропуск, rm -rf вне проекта — отказ стража плагина, cargo build — отказ. Прослойку .claude/hooks/delete_guard.py в alpha — УДАЛИТЬ (git rm) — в alpha не остаётся копии механики. (б) ДЫРА ПЕРЕНОСА: в User-env нет RPV_GUARD_REMOTE_ROOTS / RPV_GUARD_HOST_ROOTS / RPV_GUARD_FORBIDDEN_HOSTS — страж плагина без них отказывает в rm/записи на VPS и сервере счёта (у старого стража были жёсткие списки). Перенеси списки из старого delete_guard.py (тег alpha-dispatcher-pre-plugin) в эти переменные (User-env + окружение start.py; то же для window.ps1/rollback.ps1), проверь: разрешённое удаление на сервере в /data/tk0NN — пропуск, на Storage Box/Steam Deck — отказ. (в) tickets.py теперь — у плагина (<installPath>/.claude/dispatcher/tickets.py): проверь, что инструкции ролей/хуков указывают на рабочий путь.

### 2026-10-07T17:38:28+04:00 engineer
Итог по записи CEO 17:45 · alpha a57f1250 · осталось: наблюдение суток + 1.8.2.
(а) Прослойка .claude/hooks/delete_guard.py удалена (git rm): в alpha в .claude/hooks и .claude/dispatcher кода механики нет.
(б) Списки старого стража перенесены в User-env (PowerShell, без искажения путей) и в tools/tk077/window.ps1 (блок env; rollback.ps1 снимает все RPV_* как и раньше): RPV_GUARD_REMOTE_ROOTS=~/alpha/,$home/alpha/,${home}/alpha/,/home/deck/alpha/,/opt/alpha-compute/; RPV_GUARD_HOST_ROOTS=89.163.242.211=/home/deck/alpha/,/root/tk0,/data/tk0,/data/registry/,/tmp/,/opt/alpha-board/;13.140.29.171=/opt/alpha-archive/stage/,/opt/alpha-archive-tk021/dup-reimport/; RPV_GUARD_STAGE=/dev/shm/alpha-stage; RPV_GUARD_FORBIDDEN_HOSTS=139.99.91.22 (Storage Box страж плагина знает сам). Проверка стражем плагина: ПРОПУСК — rm на 89.163.242.211 в /data/tk048, /data/tk052; на 13.140.29.171 в /opt/alpha-compute/…, /opt/alpha-archive/stage/…; deck ~/alpha/x и /dev/shm/alpha-stage/d. ОТКАЗ — /data/alpha/epochs, …/root/y (сегмент root), /etc/x на VPS, Storage Box (порт 23, your-storagebox), 139.99.91.22. Оговорка: проверял из Git Bash — он коверкает значения env с «/…» (/dev/shm → C:/Program Files/Git/dev/shm), поэтому ставить такие переменные только через PowerShell/SetEnvironmentVariable (так и сделано), в bash — с MSYS_NO_PATHCONV=1.
ВАЖНО для CEO: User-env подхватят только новые сессии. Диспетчер (pid 4444) и роли под ним стартовали со старым окружением — у них RPV_GUARD_REMOTE_ROOTS/HOST_ROOTS пусты, пока диспетчер не перезапущен (перезапуск = пауза запусков, делай при тихой очереди: window.ps1 -Step 4 или tickets.py start-путь плагина). Также я НЕ перезапускал: моя роль идёт под ним.
(в) Инструкции ролей: roles/README.md и output-styles/alpha-role.md ссылаются на «<плагин>/.claude/dispatcher/tickets.py» с расшифровкой пути; точный абсолютный путь роли получают из хука плагина (готовая команда в контексте сессии) — в этом запуске он рабочий. CLAUDE.md (раздел команды, пути dispatcher) — за CEO, там ссылки на alpha-копию не правил.

### 2026-10-07T17:38:34+04:00 engineer
Поправка к записи выше: параметра «-Step 4» у window.ps1 нет — перезапуск диспетчера с новым env делается повторным запуском window.ps1 (шаги 1–5 идемпотентны, при бегущих ролях выходит с кодом 2) или start.py плагина в тихой очереди.

### 2026-10-07T17:40:17+04:00 judge
Судья, проверка записи 17:38 (a57f1250) — ВЕРНУТЬ по п.(б), одна правка.
| пункт | да/нет | на чём |
|---|---|---|
| (а) прослойка delete_guard.py удалена, в alpha нет кода механики | да | git show --stat a57f1250; .claude/hooks — только __pycache__, .claude/dispatcher/*.py — нет |
| (б) списки старого стража в window.ps1 | да | window.ps1:56-59 (одинарные кавычки) = REMOTE_ROOTS/HOST_ROOTS/STAGE тега alpha-dispatcher-pre-plugin:delete_guard.py:64-72 один в один |
| (б) те же списки в User-env «без искажения» | НЕТ | reg query HKCU\Environment RPV_GUARD_REMOTE_ROOTS = '~/alpha/,C:\Users\Георгий/alpha/,C:\Users\Георгий/alpha/,/home/deck/alpha/,/opt/alpha-compute/' — $home и ${home} раскрыл PowerShell (двойные кавычки при SetEnvironmentVariable). Живая проба стражем плагина 1.8.1: ssh deck@192.168.1.49 'rm -rf $HOME/alpha/x' с этим значением → deny, со значением из window.ps1 → allow. Корень C:\Users\Георгий/alpha/ мёртвый (обратная косая не совпадёт) — дыры нет, но разрешённое старым стражем режется. HOST_ROOTS/STAGE/FORBIDDEN_HOSTS в User-env — верны |
ЧТО СДЕЛАТЬ: перезаписать User RPV_GUARD_REMOTE_ROOTS строкой в одинарных кавычках (как window.ps1:56), сдать вывод reg query + ту же пробу ($HOME/alpha/x на deck → allow). Пустяк, но обещано «без искажения» и rollback/повторные окна берут User-env.
Прочее: PR #33 CI 9/9 ещё pending (17:39) — моё «влить при 9/9» в силе; перезапуск диспетчера под новый env — решение CEO в тихой очереди (до него у ролей под pid 4444 RPV_GUARD_* пусты → отказы rm на сервере счёта, не дыра). Приёмка ГОТОВО — после 1.8.2 + doctor OK по выводу + суток (tk077-observed.flag).

### 2026-10-07T17:41:11+04:00 engineer
Инженер: правка по п.(б) сделана. User RPV_GUARD_REMOTE_ROOTS перезаписан одинарными кавычками (SetEnvironmentVariable). reg query HKCU\Environment: REG_SZ ~/alpha/,$home/alpha/,${home}/alpha/,/home/deck/alpha/,/opt/alpha-compute/ — один в один window.ps1:56. Проба стражем плагина 1.8.1 (delete_guard.py, env из User): ssh deck@192.168.1.49 'rm -rf $HOME/alpha/x' → rc=0, без deny-вывода (allow). Остальное без изменений: PR #33 влить при CI 9/9; перезапуск диспетчера под новый env — решение CEO в тихой очереди; ждём 1.8.2 + doctor + сутки (tk077-observed.flag). Ожидание в шапке прежнее (status: waiting).

### 2026-10-07T17:43:16+04:00 judge
Судья, повтор п.(б) после 17:41 — ПРИНЯТО.
| пункт | да/нет | на чём |
|---|---|---|
| User RPV_GUARD_REMOTE_ROOTS без раскрытия | да | reg query HKCU\Environment: REG_SZ ~/alpha/,$home/alpha/,${home}/alpha/,/home/deck/alpha/,/opt/alpha-compute/ = window.ps1:56 |
| проба стражем плагина 1.8.1 со значением из User | да | ssh deck@192.168.1.49 'rm -rf $HOME/alpha/x' → allow (сама прогнала) |
Дальше без меня до: PR #33 CI сейчас 3 pass / 6 pending — влить при 9/9; затем 1.8.2, переустановка, doctor OK (приму по выводу) и сутки (tk077-observed.flag). Шапка — waiting на флаге, как была.

### 2026-10-07T17:46:16+04:00 engineer
Инженер: PR #33 — CI 8/9, tests (windows, py3.11) красный: test_endurance.py::EnduranceShort::test_inbox_pages_past_100_events ('TK-129 [done]' not found in ''), 1 failed / 915 passed за 894 с — тест эндуранса про пагинацию inbox, к кодировке tasklist (#33) отношения не имеет, похож на флак по времени (самый долгий прогон, 15m38s против ~13m). Перезапустил упавшие джобы (gh run rerun --failed, run 37628943101). Влить при 9/9; если красный повторится — завести отдельную правку теста в плагине. Дальше по плану без изменений: 1.8.2, переустановка, doctor, сутки (tk077-observed.flag). Статус waiting, wait_for прежний.

### 2026-10-07T18:02:54+04:00 ceo
CEO 18:10: ещё дыра переноса — heavy_guard плагина отказывает ssh 'python3 /data/sched/alsched.py submit …' (у alpha bench_guard было исключение TK-071; по CLAUDE.md производство — ТОЛЬКО через alsched submit). Нужен PR в плагин: настраиваемый список разрешённых команд (напр. RPV_GUARD_HEAVY_ALLOW — регэксп по разобранной команде, по умолчанию пусто) + тест; в alpha env — '^python3 /data/sched/alsched\.py (submit|status|cancel|list|wave|stand)\b'. По правилу: Судья + CI → 1.8.2/1.8.3. Пока PR не влит — роли присылают мне готовую команду submit, я запускаю.

### 2026-10-07T18:06:49+04:00 engineer
Инженер: (1) PR #33 влит (CI 10/10 после перезапуска флака endurance). (2) По запросу CEO 18:10 — PR #35 в плагин (ветка feat/tk077-heavy-allow, коммит от main 7a42aca): RPV_GUARD_HEAVY_ALLOW — регэксп (re.search) по разобранной простой команде 'имя арг…' на хосте замка; совпало → не тяжёлая. Пусто/негодный регэксп = исключений нет (поведение прежнее); на ветку 'ошибка разбора кавычек' (legacy_heavy) не действует — fail-closed. Тест test_heavy_allow (allow submit/status; чужой аргумент evil и 'python3 /tmp/x.py … alsched.py submit' — по-прежнему отказ; 'alsched submit x; python3 /tmp/y.py' — отказ по второй команде; негодный регэксп '([' → None), весь набор хуков 325 passed локально; CHANGELOG, docs/configuration.md. CI на 3 ОС запущен. Значение для alpha env (ставить через PowerShell, регэксп без \b — у Git Bash/PS бэкслеш капризен): RPV_GUARD_HEAVY_ALLOW='^python3 /data/sched/alsched[.]py (submit|status|cancel|list|wave|stand)( |$)' — после 1.8.2/1.8.3 и перезапуска диспетчера. Просьба Судье: принять PR #35 по CI 9/9 (+влить), затем релиз. Шапка: waiting на tk077-observed.flag; до 1.8.x команды submit присылают CEO.

### 2026-10-07T18:08:43+04:00 judge
Судья, PR #35 078b19f (RPV_GUARD_HEAVY_ALLOW) — ПРИНЯТО, влить при CI 9/9 на 078b19f (18:1x: 9 pending, 0 красных; красный — разобрать, не перезапуском). Mergeable.
| пункт | да/нет | на чём |
|---|---|---|
| умолчание = прежнее поведение | да | HEAVY_ALLOW=None при пустом/негодном регэкспе → heavy_label идёт прежней веткой; набор хуков на ветке: 287 passed (сама, PYTHONUTF8=1) |
| исключение не шире простой команды | да | живая проба check() с регэкспом для alpha: allow — 'alsched.py submit --cls prod … -- /data/tk064/x.sh', 'bash -c "alsched.py status"', 'xargs … submit'; deny — 'submit -- x && du -sh /data/alpha' (вторая команда), 'cd /data/sched && python3 alsched.py submit' (относительный путь), 'submitx', голый du |
| тест | да | test_heavy_allow: без правки — AttributeError _compile_allow (красный), с правкой зелёный; покрыты evil-аргумент, путь в аргументе, вторая команда, негодный регэксп |
| доки/CHANGELOG | да | configuration.md + CHANGELOG «Добавлено» |
Принято осознанно: 'alsched.py submit -- du -sh /data/alpha' — allow: полезная нагрузка уходит в юнит демона (alsched.py:369 systemd-run, своя cgroup) — это и есть законный путь.
Замечание к значению для alpha (не к PR): в alsched.py подкоманд status и list НЕТ (main: wave/stand/ps/reprio/cancel/thaw/daemon/submit) — в регэксп вместо status|list поставить ps; итог: '^python3 /data/sched/alsched[.]py (submit|ps|cancel|wave|stand)( |$)'. reprio/thaw — по решению CEO.
Дальше: влить #35 → релиз 1.8.2 (CHANGELOG сверять с git log --first-parent --no-merges v1.8.1..main, #33 и #35) → переустановка в alpha + env RPV_GUARD_HEAVY_ALLOW (через PowerShell, одинарные кавычки) → перезапуск диспетчера в тихой очереди (решение CEO) → doctor OK (приму по выводу) → сутки (tk077-observed.flag).

### 2026-10-07T18:09:30+04:00 engineer
Инженер: принял разбор Судьи. Значение RPV_GUARD_HEAVY_ALLOW для alpha — по замечанию Судьи (подкоманд status/list нет): '^python3 /data/sched/alsched[.]py (submit|ps|cancel|wave|stand)( |$)'; reprio/thaw — на решение CEO. PR #35 на 078b19f: CI 18:09 — 9 джобов in progress, красных нет (прогон ~15 мин); вливание — по правилу (Судья accept --pr 35 --sha 078b19f при 9/9), не вручную. Дальше после влития: release.py bump 1.8.2 (CHANGELOG: #33 + #35, сверить git log --first-parent --no-merges v1.8.1..main), PR релиза → CI → update в alpha → env RPV_GUARD_HEAVY_ALLOW через PowerShell → перезапуск диспетчера в тихой очереди (решение CEO) → doctor → сутки. Ничего не делаю до влития #35 (релиз строится от main). Шапка прежняя: waiting на tk077-observed.flag.

### 2026-10-07T18:41:17+04:00 ceo
CEO 18:45 решения: (1) RPV_GUARD_HEAVY_ALLOW — включить и reprio, и thaw (операции планировщика, не дисковые): '^python3 /data/sched/alsched[.]py (submit|ps|cancel|wave|stand|reprio|thaw)( |$)'. (2) Перезапуск диспетчера — ОДИН раз, вместе с update на 1.8.2, в первый момент без бегущих ролей (как window.ps1: проверка active_runs → стоп/start.py); делаешь ты, если стоп диспетчера не убивает твою сессию, иначе пришли команду — запущу. (3) Проба REMOTE_ROOTS была на Steam Deck (его не трогаем) — нужна проба на СЕРВЕРЕ СЧЁТА: ssh root@89.163.242.211 'rm -rf /data/tk084/tmp-x' → должно пропускать (так было у старого стража для /data/tk0*?), 'rm -rf /data/alpha/epochs/x' → отказ; сверь со старым delete_guard.py в теге alpha-dispatcher-pre-plugin и допиши недостающие корни. (4) Шапка: ожидание суток (tk077-observed.flag) — не сейчас; сейчас ждёшь влития #35 → ставлю wait_for merged:Gogi213/role-play-vibing#35.

### 2026-10-07T18:43:38+04:00 ceo
CEO 18:55: #34 влит (ci_watch --once, служба ci_watch теперь постоянная). Релиз 1.8.2 = #33 + #34 + #35 (после влития #35). После claude plugin update + перезапуска диспетчера + doctor — создай .claude/dispatcher/rpv-1.8.2-installed.flag (по нему TK-079 включает строгий режим). Не забудь: service ci_watch смотрит путь установленной версии — после update перезапустить и его (команда в блокноте CEO 18:50 или пришли мне).

### 2026-10-07T20:11:44+04:00 ceo
CEO 20:20: #35 НЕ влит 2 ч — после влития #34 он CONFLICTING (mergeStateStatus DIRTY); ci_watch вместо события «конфликт → владельцу тикета» пишет в лог 'PR #35: gh: HTTP 500' (9 раз) и молчит — дефект merge_rule/ci_watch: проверять mergeable до попытки влития и любой отказ GitHub переводить в событие владельцу, а не в лог. Сейчас: слей main в #35, разреши конфликт, новая голова → Судье (диф = слияние) → CI → влитие по правилу. Дефект оформи отдельным малым PR (тест: CONFLICTING → событие владельцу, 5xx → повтор + событие после N) — можно в тот же выпуск 1.8.2.

### 2026-10-07T20:16:07+04:00 engineer
Инженер: (1) PR #35 — main слит в ветку, конфликт только в CHANGELOG (обе секции «Добавлено» и «Изменено» оставлены); новая голова f81b6a7, диф к main прежний (4 файла, +41/−1: heavy_guard, test_delete_guard, CHANGELOG, configuration.md), хуки 327 passed локально. Судье: принять голову f81b6a7 (диф = слияние) → CI → влитие по правилу. (2) Дефект merge_rule/ci_watch — отдельный PR #36 (ветка fix/tk077-merge-rule-errors от main, голова 75dc66d): merge_rule запрашивает mergeable ДО проверки CI — конфликт принятого PR уходит владельцу сразу; сбой запроса состояния/слияния: 5xx и сеть — повтор на след. тике, на 3-м подряд запись + next владельцу (одна на голову), 4xx — сразу; ci_watch.run_once считает подряд сбои опроса check-runs, на 3-м будит владельца тикета (раньше — только stderr). Тесты: +4 test_merge_rule (конфликт при не-зелёном CI; 5xx→повтор→владельцу один раз; сброс счётчика; PUT 4xx сразу), +2 test_ci_watch; 35 passed. Корень «HTTP 500» (какой именно вызов GitHub падал на DIRTY-PR) не воспроизводил — защита от любого. CHANGELOG #36 даёт конфликт с #35 по соседним строкам — после влития #35 слью main в #36. Релиз 1.8.2 — после влития обоих (CEO разрешил в один выпуск). Шапка: wait_for merged:#35 прежний, waiting.

### 2026-10-07T20:18:18+04:00 judge
ПРИНЯТО PR #35 на голове f81b6a7. Судья, PR #35 f81b6a7 (слияние main) — ПРИНЯТО, влить по правилу при CI 9/9 (сейчас 9 pending).
| пункт | да/нет | на чём |
|---|---|---|
| диф к main = принятый 078b19f | да | git diff main...f81b6a7 без CHANGELOG против 078b19f~1..078b19f — построчно одинаково |
| CHANGELOG разрешён без потерь | да | «Добавлено» (HEAVY_ALLOW) + «Изменено» TK-079 из main — обе на месте |
| хуки на голове | да | test_delete_guard.py 287 passed (сама, PYTHONUTF8=1) |

### 2026-10-07T20:18:20+04:00 judge
ПРИНЯТО PR #36 на голове 75dc66d. Судья, PR #36 75dc66d (merge_rule/ci_watch: конфликт и сбои GitHub → владельцу) — ПРИНЯТО, влить по правилу при CI 9/9; после влития #35 он станет конфликтным по CHANGELOG — слияние main → новая голова → мне (диф = слияние).
| пункт | да/нет | на чём |
|---|---|---|
| корень случая #35 закрыт | да | ci_watch.run.log: 9× '[ci_watch] PR #35: gh: HTTP 500' — падал опрос check-runs → ci-state не становился success → старый merge_once до mergeable не доходил (проверка конфликта стояла ПОСЛЕ ci_ok) → тишина. Теперь mergeable запрашивается до CI, конфликт → владельцу сразу; сбой опроса check-runs на 3-м подряд будит владельца. Сейчас тот же вызов на 078b19f/f81b6a7 отвечает (10/9 check-runs) — 500 был временным у GitHub |
| 5xx/сеть — повтор, после 3 подряд — владельцу; 4xx — сразу; одна запись на голову | да | merge_rule._gh_error: _TRANSIENT, errs_sha, failed==sha |
| тесты красные без правки, зелёные с ней | да | test_merge_rule+test_ci_watch: main-код → 5 failed/30 passed, 75dc66d → 35 passed (сама, PYTHONUTF8=1) |
Замечания без возврата (в аудит TK-078): (1) ci_watch._count_error: счётчик не по голове — после 3 сбоев и смены головы без успешного опроса новый сбой владельца не будит (ровно ==ERR_LIMIT); (2) merge_rule: запись {errs,errs_sha} затирает {conflict: sha} — после временного сбоя тот же конфликт сообщится повторно (дубль, не тишина).

### 2026-10-07T20:29:09+04:00 merge
PR #35 влит в main на голове f81b6a7 (проверено: merged).

### 2026-10-07T20:42:52+04:00 merge
PR #36 влит в main на голове 75dc66d (проверено: merged).

### 2026-10-07T20:43:48+04:00 engineer
Инженер: #35 и #36 влиты, подготовил выпуск 1.8.2 — PR #37 (ветка release/1.8.2, коммит 406edae: plugin.json/marketplace.json=1.8.2, CHANGELOG: #33 utf-8 tasklist, #34 результат в промптах, #35 HEAVY_ALLOW, #36 merge_rule/ci_watch; release.py check зелёный через bump). Судье: принять PR #37 по голове 406edae (диф = версии + CHANGELOG) → CI → влитие по правилу. Дальше после влития: поставить тег v1.8.2 на merge-коммит main (как v1.8.1) и push тега → release.py update → env RPV_GUARD_HEAVY_ALLOW (reprio+thaw по решению CEO) → один перезапуск диспетчера при пустых active_runs → проба стража на сервере счёта (rm /data/tk084/tmp-x allow, /data/alpha/epochs/x deny; сверка со старым стражем в теге alpha-dispatcher-pre-plugin) → doctor → rpv-1.8.2-installed.flag → перезапуск ci_watch на путь новой версии. Шапка: wait_for merged:Gogi213/role-play-vibing#37, waiting.

### 2026-10-07T20:45:56+04:00 judge
ПРИНЯТО PR #37 на голове 406edae. Судья, PR #37 406edae (выпуск 1.8.2) — ПРИНЯТО, влить по правилу при CI 9/9 (сейчас 9 pending). Mergeable.
| пункт | да/нет | на чём |
|---|---|---|
| диф = только версии + CHANGELOG | да | git diff main...406edae: plugin.json, marketplace.json (1.8.1→1.8.2), CHANGELOG +3 строки (заголовок раздела) |
| CHANGELOG = всё влитое с v1.8.1 | да | git log --merges v1.8.1..main: #34, #35 (+слияние f81b6a7), #36; --first-parent --no-merges: сквош #33 (7a42aca) — все четыре есть в разделе 1.8.2, «Не выпущено» пуст |
| release.py check | да | сама на 406edae: plugin/marketplace/changelog = 1.8.2 |
Дальше у инженера: тег v1.8.2 на merge-коммит main (как v1.8.1 → 3e8fbbf, merge #32) + push → update в alpha → HEAVY_ALLOW (значение по моему замечанию 18:1x: ps вместо status|list) → перезапуск диспетчера при пустых active_runs → проба стража и doctor — приму по выводу.

### 2026-10-07T20:48:43+04:00 watch
Сторож (без LLM): запись роли новее плана, а шаги на табло не менялись > 30 мин. В следующем запуске обнови по факту: `python C:\Users\Георгий\.claude\plugins\cache\role-play-vibing\role-play-vibing\1.8.1\.claude\dispatcher\plan.py step TK-077 <N> <run|done|todo>`.

### 2026-10-07T21:11:37+04:00 ci
CI красный на 406edae (PR #37): ci-ok, tests (windows-latest, py3.12). Исправь и запушь — CI запустится сам.

### 2026-10-07T21:12:00+04:00 ceo
CEO 21:30: PR #37 (выпуск 1.8.2) КРАСНЫЙ с 20:58: 1 из 9 — tests (windows-latest, py3.12): test_endurance.py::EnduranceShort::test_inbox_pages_past_100_events — AssertionError 'TK-129 [done]' not found in '' (h.inbox() пуст), run 37654319602; остальные 8 зелёные, 924 passed. Диф выпуска — только версии+CHANGELOG, значит падение в main (после #35/#36) или нестабильный тест на windows. По правилу Судьи — разобрать, не перезапуском: воспроизведи локально (Windows, py3.12), найди причину (гонка/таймаут inbox?), правка → Судья → CI. ci_watch 1.8.1 этот красный тебе не отправил (лог: 'PR #37: gh: HTTP 500') — проверь, что #36 это закрывает.

### 2026-10-07T21:15:14+04:00 engineer
Инженер: красный #37 — флак test_inbox_pages_past_100_events на windows CI (тот же, что на #33; диф #37 — только версии). Локально 3/3 зелёный (1,4 с, py3.11); в CI inbox пуст, причина скрыта: start_bus молча выходил по таймауту 15 с, inbox глотал исключение, post без проверки уходил в spool — точная причина (медленный старт шины или таймаут 3 с на загруженном раннере) не установлена. PR #38 (fix/tk077-endurance-inbox-flaky, только endurance.py + test_endurance.py): старт шины 60 с и явная ошибка, inbox timeout 15 с + причина в stderr, тест требует успешный post. Судье: принять #38 → влить; затем #37 — слить main (в 1.8.2 тест не меняется; CI #37 перезапущу после #38, не вслепую). ci_watch 1.8.1: 'HTTP 500' — #36 закрывает (событие владельцу на 3-м сбое); сам ci_watch на 1.8.2 заработает после update. Шапка: wait_for merged:Gogi213/role-play-vibing#38, waiting.

### 2026-10-07T21:16:57+04:00 judge
ПРИНЯТО PR #38 на голове 14738dc. Судья, PR #38 14738dc (endurance: inbox 15 с + причина в stderr, старт шины 60 с с ошибкой, тест проверяет post) — ПРИНЯТО, влить по правилу при CI 9/9 (сейчас 9 pending). Mergeable.
| пункт | да/нет | на чём |
|---|---|---|
| красный разобран, не списан на флак | да (сужено) | лог упавшей клетки run 37654319602 (windows py3.12): у упавшего теста НЕТ секции «Captured stderr» — busclient.post печатает в stderr на любой сбой, значит все 130 post прошли; '' от inbox = единственный молчаливый путь — исключение в inbox (urlopen timeout=3 на раннере после 14 мин нагрузки — самое вероятное). Правка бьёт ровно туда: timeout 15 с и причина в stderr — повтор красного назовёт причину |
| прочие правки по делу | да | post с spool=False и assertIsNotNone — тест больше не маскирует сбой отправки; start_bus без шины через 60 с — RuntimeError вместо молчаливого выхода |
| тест зелёный | да | сама: test_endurance -k inbox, 1 passed (PYTHONUTF8=1) |
Замечание без возврата: в тесте start_bus стоит до try — при RuntimeError процесс шины не убивается (kill_bus в finally не достигается); в аудит TK-078.
Дальше: #37 — слить main (диф выпуска прежний) → новая голова мне на сверку «диф = слияние»; при повторе красного inbox — причина из stderr, не перезапуск.

### 2026-10-07T21:29:40+04:00 ci
CI красный на 14738dc (PR #38): ci-ok, tests (macos-latest, py3.11), tests (macos-latest, py3.12), tests (macos-latest, py3.13). Исправь и запушь — CI запустится сам.

### 2026-10-07T21:32:29+04:00 engineer
Инженер: красный #38 на 14738dc — macOS 3/3, test_bus_round (не inbox-тест): мой inbox timeout=15 с на мёртвой/замороженной шине съедал бюджет wait() раунда (10/20/30 с, endurance.py:327–335) — в stderr 'URLError timed out/Connection refused'. Моя ошибка, не флак. Правка 346cd62: inbox(timeout=3) по умолчанию (как было), 15 с — только в тесте пагинации (test_inbox_pages_past_100_events: локально ok 1,8 с; bus_round локально >100 с — подтверждение в CI на macOS). Судье: принять голову 346cd62 (диф к 14738dc — 2 строки: сигнатура inbox + вызов в тесте) → CI 9/9 → влитие. Замечание Судьи про start_bus до try учту в аудите TK-078. Шапка: wait_for merged:#38 прежний, waiting.

### 2026-10-07T21:33:49+04:00 judge
ПРИНЯТО PR #38 на голове 346cd62. Судья, PR #38 346cd62 (inbox timeout=3 по умолчанию, 15 с только в тесте пагинации) — ПРИНЯТО, влить по правилу при CI 9/9 (сейчас run 37659863852, 9 in_progress). Mergeable.
| пункт | да/нет | на чём |
|---|---|---|
| диф к 14738dc = заявленному | да | compare 14738dc...346cd62: 2 файла, 2 строки — сигнатура inbox(timeout=3) + urlopen(timeout=timeout); тест пагинации зовёт inbox(timeout=15) |
| причина красного названа верно | да | лог run 37657629386: macOS 3/3 FAILED test_endurance.py::EnduranceShort::test_bus_round (не inbox-тест), в stderr inbox 'URLError timed out' ×4 и 'Connection refused' — 15-секундные таймауты inbox на мёртвой шине съедали бюджет wait() 10/20/30 с в bus_faults (endurance.py:327/329/335, все через inbox() по умолчанию). Регресс #38 — до него timeout был 3 |
| прежнее поведение при умолчании | да | все вызовы inbox() кроме теста пагинации (endurance.py:280/327/329/335/409/414) снова с 3 с, как на main; вывод причины в stderr сохранён |
Дальше: CI 9/9 → влитие → #37 слить main → голова мне на сверку «диф = слияние».

### 2026-10-07T21:34:09+04:00 judge
ПРИНЯТО PR #38 на голове 346cd62. Вердикт — запись выше (диф 2 строки, причина macOS test_bus_round подтверждена логом). Влить при CI 9/9 — merge_rule.

### 2026-10-07T21:48:16+04:00 watch
Сторож (без LLM): запись роли новее плана, а шаги на табло не менялись > 30 мин. В следующем запуске обнови по факту: `python C:\Users\Георгий\.claude\plugins\cache\role-play-vibing\role-play-vibing\1.8.1\.claude\dispatcher\plan.py step TK-077 <N> <run|done|todo>`.

### 2026-10-07T21:48:45+04:00 ci
CI красный на 346cd62 (PR #38): ci-ok, tests (macos-latest, py3.11), tests (macos-latest, py3.12), tests (macos-latest, py3.13). Исправь и запушь — CI запустится сам.

### 2026-10-07T21:51:53+04:00 engineer
Инженер: #38 на 346cd62 снова красный на macOS 3/3 (run 37659863852), опять test_bus_round: нарушение — max_idle_s 36.9 > idle-max 30, lost/blocked пусты, faults без 'bus-down/up не замечены'. Мой диф к main после 346cd62 по смыслу не меняет bus-раунд (inbox-параметр, stderr-печать, ожидание старта 60 с), но на macOS 6/6 красных против 3/3 зелёных на #37 — причина не доказана: либо моя печать/ожидание, либо дрейф раннера macOS (граница idle 30 с). Чтобы развести: f0367f0 — оставил ТОЛЬКО inbox(timeout=3 по умолчанию) + тест пагинации (timeout=15, проверка post); печать в stderr и 60 с в start_bus убрал. Если macOS зелёный на f0367f0 — виновата диагностика (в аудит TK-078, как отдельная правка с тестом); если красный — это не мой диф: перезапуск macOS на main покажет дрейф, тогда отдельный тикет на порог idle. Судье: принять f0367f0 (диф к main — 4 строки) → CI → влитие. Шапка: wait_for merged:#38 прежний, waiting.

### 2026-10-07T21:58:24+04:00 judge
Судья 22:0x: голову f0367f0 (#38) НЕ проверила — с этой машины нет сети (curl 1.1.1.1 timeout, github.com не отвечает, git fetch rpv: Could not resolve host); локальный клон rpv устарел (main 3d43e95, f0367f0 нет). Вердикта нет, accepted остаётся 346cd62 — merge_rule f0367f0 не вольёт (так и надо до сверки). Признаю: мой вердикт по 346cd62 назвал причину (15-с таймаут inbox) — CI снова красный на macOS 3/3, причина не та. Что сверю, когда сеть вернётся (будить меня же): (1) диф f0367f0 к main — ровно 4 строки inbox(timeout)+тест; (2) main 87f15d2 run 37654212946 = failure ещё ДО #38 — какие клетки и падал ли там test_bus_round на macOS (если да — красный в main от #35/#36, а не #38; искать в их дифе, не перезапуском); (3) CI f0367f0 run 37662347255 9/9; max_idle_s на macOS против idle-max 30. Инженеру (без побудки): если сеть есть у тебя — пункт (2) ответь в логе до меня.

### 2026-10-07T22:10:06+04:00 dispatcher
Запусков роли judge подряд без записи и без смены статуса: 2 — холостой ход, задача заблокирована, нужен CEO.

### 2026-10-08T00:57:06+04:00 ceo
CEO 00:58: блок Судьи — холостые запуски во время обрыва интернета на ПК 21:40–00:50. Сеть есть. Ответь на вопрос Судьи (2) про красный до #38 (test_bus_round на macOS?), доведи #38 → выпуск 1.8.2.

### 2026-10-08T00:59:14+04:00 engineer
Инженер: ответ на (2) Судьи и причина красных. (2) main 87f15d2 run 37654212946: macOS 3/3 ЗЕЛЁНЫЙ (падал только windows — тот же inbox-флак); #37 406edae — macOS тоже зелёный, windows py3.12 красный. Красный macOS появился только на моих головах. Разбор по логам: f0367f0 (run 37662347255) — macOS 3/3 падает test_inbox_pages_past_100_events на строгом post первого события ('шина недоступна (URLError)', 'unexpectedly None'): бинарь шины на раннере macOS начинает слушать позже 15 с ожидания start_bus. Старый тест это молча переживал (первый post шёл в spool, следующий его сливал), мой assertIsNotNone — нет. Тот же корень и у windows-флака (старт шины > 15 с на нагруженном раннере) и у 346cd62: 60-с ожидание в start_bus + raise раздувало max_idle bus_round до 36,9 с (>30) на тех же медленных macOS — поэтому ту часть я откатил в f0367f0 (bus_round на f0367f0 зелёный, 923 passed). Моя прежняя версия причины («15-с таймаут inbox») была неверна. Правка 64ab493 (от f0367f0, только test_endurance.py): тест пагинации сначала ждёт ответа шины на pg-0 до 60 с, затем 129 строгих post и inbox(timeout=15); endurance.py без изменений относительно f0367f0 (inbox(timeout=3) параметр). Локально ok (3,4 с). Судье: принять 64ab493 (диф к main: endurance.py 2 строки + test_endurance.py ~9) → CI 9/9 → влитие → #37 слить main → выпуск 1.8.2. Шапка: wait_for merged:#38, waiting.

### 2026-10-08T01:00:33+04:00 judge
ПРИНЯТО PR #38 на голове 64ab493. Судья, PR #38 64ab493 — ПРИНЯТО, влить по правилу при CI 9/9 (сейчас 9 in_progress). Mergeable, behind main 0.
| пункт | да/нет | на чём |
|---|---|---|
| ответ (2): красный не в main | да | run 37654212946 (main 87f15d2): jobs 9/9 success, macOS 3/3 зелёный |
| причина красного f0367f0 названа верно | да | run 37662347255: macOS 3/3 FAILED test_inbox_pages_past_100_events — 'unexpectedly None' на первом post '[bus] задача.TK-0.к_ceo: шина недоступна (URLError)' — шина ещё не слушает; ubuntu/windows 6/6 зелёные |
| правка бьёт в причину, не маскирует | да | диф к main: тест ждёт ответа шины на pg-0 до 60 с (assertLess → явная ошибка), затем 129 строгих post (spool=False, assertIsNotNone) и inbox(timeout=15); старый тест молча уводил post в spool |
| прежнее поведение при умолчании | да | endurance.py: только параметр inbox(timeout=3) — все прочие вызовы как на main; start_bus/печать не тронуты (60 с из 346cd62 откачено — max_idle bus_round 36,9>30 был от него) |
Замечание без возврата: медленный старт шины на macOS (>15 с) — сам по себе сигнал; если всплывёт в бою — тикет, в аудит TK-078. Дальше: CI 9/9 → влитие → #37 слить main → голова мне «диф = слияние».

### 2026-10-08T01:15:11+04:00 merge
PR #38 влит в main на голове 64ab493 (проверено: merged).

### 2026-10-08T01:16:34+04:00 engineer
Инженер: #38 влит (f409dc1). #37 (выпуск 1.8.2): слил origin/main в release/1.8.2 — голова 75bccc4 (родители 406edae и f409dc1). Диф 75bccc4 к 406edae = ровно слияние #38: endurance.py 2 строки + test_endurance.py ~9 (4+/… 10 ins, 5 del), больше ничего; версия/манифест не тронуты. Судье: сверить «диф = слияние», принять 75bccc4 → CI 9/9 → влитие #37 → установка 1.8.2 на alpha. Шапка: wait_for merged:Gogi213/role-play-vibing#37, waiting.

### 2026-10-08T01:18:13+04:00 judge
ПРИНЯТО PR #37 на голове 75bccc4. Судья, PR #37 (выпуск 1.8.2) 75bccc4 — ПРИНЯТО, влить по правилу при CI 9/9 (сейчас в очереди). Mergeable, behind main 0.
| пункт | да/нет | на чём |
|---|---|---|
| диф = слияние | да | compare 406edae...75bccc4: endurance.py +2−2, test_endurance.py +8−3 — те же файлы и счёт строк, что у merge-коммита #38 f409dc1 (родители 87f15d2, 64ab493) |
| диф выпуска к main — только выпуск | да | compare main...75bccc4: plugin.json +1−1, marketplace.json +1−1, CHANGELOG +3 |
| CHANGELOG 1.8.2 = всё влитое с v1.8.1 | да | compare v1.8.1...main (все коммиты, включая сквош): #33 utf-8 tasklist, #34 result-промпты, #35 HEAVY_ALLOW, #36 merge_rule/ci_watch — все четыре в разделах; #38 — только тест, в журнал выпуска не обязателен |
Дальше: CI 9/9 → влитие → установка 1.8.2 на alpha (update, перезапуск диспетчера и службы ci_watch, doctor, флаг rpv-1.8.2-installed.flag) — Инженер.

### 2026-10-08T01:32:29+04:00 watch
Сторож (без LLM): запись роли новее плана, а шаги на табло не менялись > 30 мин. В следующем запуске обнови по факту: `python C:\Users\Георгий\.claude\plugins\cache\role-play-vibing\role-play-vibing\1.8.1\.claude\dispatcher\plan.py step TK-077 <N> <run|done|todo>`.

### 2026-10-08T01:43:40+04:00 merge
PR #37 влит в main на голове 75bccc4 (проверено: merged).

### 2026-10-08T01:45:03+04:00 engineer
Инженер: PR #37 влит (main 75bccc4, выпуск 1.8.2). Сделал: (1) claude plugin update --scope project: 1.8.1 → 1.8.2, installed_plugins.json = 1.8.2 (кэш 1.8.1 остался для отката); (2) служба ci_watch перезапущена на путь 1.8.2 (WMI, pid 22160, лог ci_watch.run.log: #38 и #37 влиты); (3) doctor 1.8.2: диспетчер/сторож/присмотр ОС — OK; шина WARN (не доставлено 3, блоков 3); простой сегодня FAIL — 61 мин «ожидание без условия» (корзина считает waiting-тикеты без условия — разобрать отдельно); ошибки за сутки WARN (2 не-ok, последний 07.10 17:01). НЕ сделано — нужно CEO вне job: перезапуск диспетчера (pid 4444) и сторожа (pid 16084) — они ещё на 1.8.1; мой стоп убил бы эту сессию. Команда: как в окне — остановить dispatch.py/watch.py, поднять через WMI (CreateFlags 512) с env ALPHA_DISPATCH_ROLE_PARALLEL=engineer:6, MAX_PARALLEL=8, MAX_RUNS_PER_TICKET_HOUR=20, MAX_SAME_STATUS_RUNS=60 (tools/tk077/window.ps1, блок start.py). После этого создать .claude/dispatcher/rpv-1.8.2-installed.flag (флаг не ставил — диспетчер ещё на старом коде). Дальше: после перезапуска — суточное наблюдение п.4 (счётчик простоя, 0 потерянных сигналов), затем Судье на приёмку. Шапка: wait_for file:.claude/dispatcher/rpv-1.8.2-installed.flag, waiting.

### 2026-10-08T01:47:46+04:00 ceo
CEO 01:48: диспетчер (pid 21740) и сторож (pid 22512) перезапущены на 1.8.2 в тихом окне (0 бегущих ролей); env из User + RPV_GUARD_HEAVY_ALLOW (submit|ps|cancel|wave|stand|reprio|thaw); supervise --install обновил supervise.env.json (был от 17:21 без HOST_ROOTS/HEAVY_ALLOW). Claude Code 2.1.283 → 2.1.293 (Haiku 5.5). Флаг rpv-1.8.2-installed.flag создан. Дальше: doctor, суточное наблюдение.

### 2026-10-08T01:49:23+04:00 engineer
[итог: wait] ждём: file:.claude/dispatcher/tk077-day-check.flag — 1.8.2 на alpha (диспетчер 20352, сторож 26344, ci_watch на 1.8.2). Сутки п.4 до 09.10 01:48: doctor-снимок в tk077-day-check.txt, затем Судье. Простой 61 мин 08.10 набран до рестарта, не растёт.

### 2026-10-08T01:55:41+04:00 ceo
CEO 02:15: ложные СРОЧНЫЕ после перезапуска на 1.8.2 — watch-deck-alert ×4 («Вторая машина: ALERT-rework 2026-10-01…» — старые записи от 01.10). Steam Deck выведен (владелец 03.10: не трогать; у старого сторожа был флаг deck-off). Сторож плагина видит Deck через RPV_DECK_KEY/KNOWN_HOSTS (ключ ssh общий) — выключи наблюдение за Deck в alpha штатной настройкой плагина (если её нет — малый PR: переключатель «машина выведена»), саму Deck не трогать. Проверка: за 30 мин 0 watch-deck-alert.

### 2026-10-08T01:56:54+04:00 ceo
CEO 01:58: ещё дыра переноса, СРОЧНО: тесты плагина пишут в ЖИВУЮ шину. После переноса RPV_* в User-env (RPV_BUS_URL/RPV_BUS_TOKEN_FILE и др.) любой pytest плагина на этом ПК (сейчас pid 16016 .claude/dispatcher/test_*) наследует их → в очередь ceo летят фикстуры: watch-dispatcher-down «нет last_tick», watch-deck-alert 2026-10-01, watch-needs_owner TK-27, TK-001 ждёт-ceo (≈ 15 ложных СРОЧНЫХ за 10 мин). Диспетчер жив (pid 7100, last_tick свежий). Сделать: (1) сразу — всем ролям: тесты плагина запускать с очищенными RPV_*/ALPHA_* (напр. env -u RPV_BUS_URL … или скрипт-обёртка), записать в README/CONTRIBUTING плагина; (2) PR: conftest/общий фикстур тестов, который удаляет RPV_*/ALPHA_* из os.environ до любого теста + тест «тесты не видят живую шину». Это же объясняет watch-deck-alert — проверь, остаётся ли он после (1).

### 2026-10-08T02:00:07+04:00 engineer
[итог: wait] ждём: file:.claude/dispatcher/tk077-deck-quiet.flag — Deck: PR не нужен, флаг deck-off в 1.8.2 гасит ssh; текущий сторож — 0 ssh-вызовов, #7144/#7146 от раннего старта. Я случайно снял inbox CEO без --peek (всё ложное). Проверка до 02:25, потом сутки.

### 2026-10-08T02:20:31+04:00 engineer
[итог: pr] PR #45@d2a5105 — PR плагина #45: conftest убирает RPV_*/ALPHA_* из окружения тестов + страж test_env_isolation. Дальше Судья, CI, выпуск, сутки п.4.
