# Диспетчер задач alpha (v1, по образцу Paperclip)

Вместо четырёх постоянных чатов ролей — очередь тикетов `.claude/tickets/*.md` и цикл, который сам
решает, кого будить `claude -p`. Только stdlib (`ticket.py`, `dispatch.py`, `tickets.py`).

## Формат тикета — `.claude/tickets/<ID>.md`

Шапка между строками `---`, простые `ключ: значение`:
`id, title, owner` (researcher|engineer|judge), `status`
(todo|in_progress|waiting|in_review|done|blocked|needs_owner), `reviewer` (опц.),
`wait_for` (опц.: `file:<путь>` | `deck:<путь>` через ssh | `mention`), `updated`.
Тело — описание, затем `## Лог`: записи `### <ISO-время> <автор>` + текст; упоминания
`@researcher`/`@engineer`/`@judge`/`@ceo`. Автор и роль в упоминаниях — латинские ключи, не русские
названия. ID по умолчанию `TK-NNN` (своя нумерация, не путать с `T-NN` из `TASKS.md`/прозы CLAUDE.md).

## Кого будит `dispatch.tick()`

(а) `todo` → роль-владелец. (б) новая запись лога с `@роль` после последнего запуска этой роли по
задаче → эта роль (работает при любом статусе; `@ceo` не запускает роль — строка в `ceo-inbox.md`).
(в) `waiting` и условие `wait_for` выполнено (`file:`/`deck:`) → владелец задачи; `mention` само не
снимается, ждём (б). (г) `done` с заданным `reviewer` и без записи ревьюера после `updated` → статус
`in_review`, будит ревьюера. (д) прогон завершился без новой записи роли в логе (или по таймауту) —
один повтор с напоминанием записать итог; второй раз подряд → `status: blocked` + строка в
`ceo-inbox.md`. `blocked`/`needs_owner` всегда идут строкой в `ceo-inbox.md` (дедуп по статус+`updated`).
Не больше одного активного запуска на задачу; всего `MAX_PARALLEL` (умолч. 2, `ALPHA_DISPATCH_MAX_PARALLEL`).

## Область сессии на роль — `SESSION_SCOPE`

`{"judge": "role", "researcher": "ticket", "engineer": "ticket"}` (правится в `dispatch.py` или
`ALPHA_DISPATCH_SESSION_SCOPE=judge:role,engineer:ticket`). `"ticket"` — `--resume` в пределах
(задача, роль), как раньше. `"role"` — одна долгая сессия роли на все задачи; диспетчер не запускает
вторую задачу такой роли, пока не закончена текущая (задачи — по очереди). **Ротация:** если контекст
прошлого запуска роли (`input + cache_read + cache_creation` из `usage`) превысил `ROTATE_TOKENS`
(умолч. 250 000, `ALPHA_DISPATCH_ROTATE_TOKENS`) — следующий запуск идёт без `--resume`, в промпт
добавляется просьба перечитать блокнот и `docs/research/reviews/` по нужной задаче.

## Запуск

```bash
python .claude/dispatcher/dispatch.py --once   # один тик, для проверки/крона
python .claude/dispatcher/dispatch.py          # цикл раз в 15 с (ALPHA_DISPATCH_INTERVAL)
python .claude/dispatcher/tickets.py new --owner researcher --title "..." [--reviewer judge]
python .claude/dispatcher/tickets.py comment TK-001 --author engineer --text "... @judge глянь"
python .claude/dispatcher/tickets.py status
```

`CLAUDE_BIN` — путь к `claude` (по умолчанию из PATH, иначе `/c/Users/Георгий/.local/bin/claude`).
CLI на этой машине залогинен, `--permission-mode bypassPermissions` проверен вручную; `dispatch.py`
всегда добавляет этот флаг и `ALPHA_ROLE=<role>` в окружение запуска — по нему `role_context.py`
берёт роль без определения по названию сессии Desktop. Вывод каждого запуска —
`.claude/dispatcher/runs/<ts>-<ID>-<role>.json` (+ `.err.log`), сводка — `.claude/dispatcher/runs.log`,
состояние (`session_id` по scope, `retries`, `last_woken`) — `.claude/dispatcher/state.json`.

## Как этим пользоваться CEO и ролям

CEO заводит тикет `tickets.py new`, роль работает по нему как раньше (устав, блокнот, журнал), но
получает задачу через `-p` вместо диалога: делает шаг, дописывает `## Лог`, правит `status`/`wait_for`
сама (диспетчер не понимает прозу — если роль не поменяет `status`, тикет снова уйдёт в очередь).
`needs_owner`/`blocked` — читать `ceo-inbox.md` (может дублироваться с упоминанием `@ceo` в том же
логе — безобидно, не чинили в v1). Фоновая работа — `status: waiting` + `wait_for`, не ждать в сессии.

## Тесты и известные ограничения

`python -m unittest discover -s .claude/dispatcher -p test_dispatch.py -v` — 21 тестов, без сети,
без настоящего `claude` (свой seam `dispatch._popen`, фейковый `python`-скрипт). Ограничения v1:
активные запуски (`Popen`) живут только в памяти процесса — перезапуск диспетчера теряет их (тикет
просто попадёт в тот же цикл заново на следующем тике); ревьюер, оставивший `status: done` с тем же
`reviewer:`, может получить повторный запуск — по завершении ревью стоит снять `reviewer:` или сменить
статус; `wait_for: deck:` дергает ssh на `deck@192.168.1.49` — не проверено вживую. Базовый контекст
свежей сессии роли ≈ 52 тыс. токенов (замер владельца); `skillOverrides` в `.claude/settings.json`
скрывает навык строкой-значением `"off"` (уже стоит на 23 навыках) — диспетчерские роли наследуют
это автоматически, отдельно настраивать не нужно.
