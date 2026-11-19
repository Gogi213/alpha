# TK-630 — правки памяти координатора (20.11.2026)

Память — `C:\Users\Георгий\.claude\projects\C--visual-projects-alpha\memory\` (вне git). Копия до правок — `memory-backup-2026-11-20\` рядом (62 файла, 99 382 Б). Слова владельца и «почему» удалённых приказов — `docs/plan/preferences-why.md` (по номеру пункта `.claude/preferences.md`). Правило: смысл приказов не терять, убрать дубли, устаревшее (calc, Steam Deck, rpv, CEO/Judge, 350k/400k/450k), битые `[[ссылки]]`.

Было 62 файла (61 + MEMORY.md), стало 31 (30 + MEMORY.md).

## Удалено — приказ уже в `preferences.md` (п.) и `preferences-why.md`
| файл | пункт | причина |
|---|---|---|
| optimal-algorithm-not-first | 6 | дубль; строка «Алгоритм: …» возвращена в п.6 |
| data-as-it-was | 11 | дубль; «точная модель, упрощение только по замеру» возвращено в п.11 |
| keep-raw-data | 3 | дубль |
| owner-command-means-now | 2 | дубль; «неясен срок — спросить, не выбирать» возвращено в п.2 |
| no-workflows-without-ask | 15 | дубль |
| no-notification-sound | 22 | дубль; источник «писка» (диспетчер rpv) устарел, rpv снят 12.11 |
| no-idle-waiting | 14 | дубль |
| estimates-claude-pace | 5 | дубль (формат «шаг → минуты → время» в п.5) |
| status-short-dense | 4 | дубль; правило ответа — в `alpha-owner-business-answers` |
| readiness-from-counters | 10 | дубль |
| port-proven-first | 20 | дубль |
| no-blocking-for-own-measurements | 14 | дубль |
| check-before-showing-owner | 25 | дубль |
| bb-spawn-parent-self | 29 | дубль; команда — в `team-bb-pstack` |
| no-workaround-proposals | 7 | дубль |
| no-spend-in-status | 4 | дубль |
| no-predictions-to-owner | 4 | дубль |
| measurement-mirror-reference | 23 | дубль |
| all-available-data-window | 31 | дубль |
| srv-no-pkill-by-name | AGENTS.md «Грабли» | дубль |
| usage-in-tokens | 4 | дубль |

## Слито
| из | в | причина |
|---|---|---|
| alpha-reasoning-rules, alpha-research-protocols, doubt-negative-verdicts, research-forks-no-owner, signal-first, no-quick-estimates-of-results, no-per-second-aggregates, no-break-fix-loop, convergence-search-rules, flaky-test-ab-first (+ из alpha-owner-preferences: «лучший выживальщик», KPI «дней до перехая» В-120, вход лесенкой, простые правила отбора; «спросить до тяжёлого счёта вне заданного объёма») | **research-rules** | 10 файлов одной темы — порядок исследования; Judge/CEO → проверяющий/координатор |
| alpha-context-economy, no-idle-after-wave | **coordinator-context** | порог по п.19 (передача 260 тыс., клир ≤ 265 тыс., В-161); 350k/400k/450k убраны; клир не останавливает делегирование и мониторы; закрыта волна — следующий шаг сразу |

## Переписано / урезано
| файл | что |
|---|---|
| alpha-owner-preferences | построчно: остались «исполнять шаг плана без «го»», «из одной реплики постоянных правил не выводить — спросить раз», «вопрос владельца не блокирует общую часть прогона»,  «быстрый режим запрещён» (27.09), «исправляй», «не понял» → 2–3 варианта, механические сторожа, «ниче не рви» (коллектор), потолок CPU ~93 %, «Обнови память», «одним проходом, не по одной», не экономить до остановки; остальное — дубли п.1–3/8/15 или устарело (плагин rpv, Судья, calc/Deck) |
| alpha-owner-business-answers | правило 2–5 строк, результат/число первым, GMT+4, граница TypeSafe В-85; без дублей |
| no-scope-inflation | без истории плагина |
| pc-no-heavy-compute | ПК ≤ 2 процессов Idle, пачки ≤ 3, пулы ≤ 2; тяжёлое — srv; без Deck/prio_guard |
| windows-ssh-cyrillic-home | хосты srv / София; без calc и Deck; `tar` по ssh вместо rsync |
| owner-pages-design-first | без Judge/CEO; макет только для новой страницы, вкладка/колонка/настройки — сразу в бой; проверка самому + click-test |
| owner-words-exact | без абзаца про CEO-роль |
| never-recompute-same | CEO → координатор, без Летописи; таблица «есть / считаем / стоимость» |
| team-bb-pstack | актуально на 20.11; rpv-метки в один абзац |
| pnl-simple-not-log | битые ссылки |
| 18 файлов без изменений текста | ссылки на удалённые заметки заменены на `research-rules` / `team-bb-pstack` / `alpha-owner-business-answers` / `.claude/preferences.md`: no-median-for-strategy, owner-dollars-sharpe, risk-reward-first, our-code-first-not-network, arb-goal-predictable-swings, owner-ambition-order-of-magnitude, one-modular-system, owner-text-not-artifacts, rejected-call-may-have-run, perf-hold-file, sofia-click-tests, spawn-merge-master, bb-stop-old-version, no-redundant-verification, viking-mcp-ram, windows-python-utf8-symbol-names, thread-titles-plain, alpha-term-prosadka |

## MEMORY.md
Пересобран: 30 строк, по одной на файл; строка про контекст — «передача 260 тыс., клир ≤ 265 тыс. (п.19, В-161)», не «350k».

## Неуверенные места (решение координатора 20.11: оставить как есть)
- «ниче не рви» (по молчанию соединение не рвать) — сентябрь 2026; живёт и в G-022 plan §2.
- Потолок CPU ~93 % (03.10) — `alpha-owner-preferences` + why п.8.
- «Быстрый режим запрещён» (27.09) — `alpha-owner-preferences`.
- KPI «дней до перехая» В-120, «лучший выживальщик», вход лесенкой (сент. 2026) — `research-rules`, помечено «не подтверждено позднее».
- «Обнови память» = переписать `AGENTS.md «Сейчас»`, память и `docs/plan/coord-handoff.md`, не дописывать — `alpha-owner-preferences`.

## Размеры (байты, `wc -c`)
| файл | до | после |
|---|---|---|
| AGENTS.md | 8 418 | 6 825 (с пунктом kb из master) |
| .claude/preferences.md | 17 093 | 11 255 (возвращены строки-правила п.2/4/6/9/11/14/23/34) |
| MEMORY.md | 9 454 | 4 424 |
| **итого, читается на старте** | **34 965** | **22 504** |
| вся папка памяти (файлов / байт) | 62 / 99 864 | 31 / 48 347 |
| docs/plan/coord-handoff.md | — (не было) | 4 127 |
