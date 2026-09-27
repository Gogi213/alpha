# Диспетчер задач alpha (v1.1, по образцу Paperclip)

Вместо четырёх постоянных чатов ролей — очередь тикетов `.claude/tickets/*.md` и цикл, который сам
решает, кого будить `claude -p`. Только stdlib (`ticket.py`, `dispatch.py`, `tickets.py`).

## Формат тикета — `.claude/tickets/<ID>.md`

Шапка между строками `---`, простые `ключ: значение`:
`id, title, owner` (researcher|engineer|judge), `status`
(backlog|todo|in_progress|waiting|in_review|done|blocked|needs_owner), `reviewer` (опц.),
`wait_for` (опц.: `file:<путь>` | `deck:<путь>` через ssh | `mention`), `updated`.
Тело — описание, затем `## Лог`: записи `### <ISO-время> <автор>` + текст; упоминания
`@researcher`/`@engineer`/`@judge`/`@ceo`. Автор и роль в упоминаниях — латинские ключи, не русские
названия. ID по умолчанию `TK-NNN` (своя нумерация, не путать с `T-NN` из `TASKS.md`/прозы CLAUDE.md).
`backlog` — перенесённая, но ещё не взятая в работу задача: диспетчер её не трогает ни по одному
правилу (даже упоминание не будит); `tickets.py start <ID>` переводит в `todo`.

## Кого будит `dispatch.tick()`

(а) `todo` → роль-владелец. (б) новая запись лога с `@роль` после последнего запуска этой роли по
задаче → эта роль (работает при любом статусе, кроме `backlog`; `@ceo` не запускает роль — строка в
`ceo-inbox.md`). (в) `waiting` и условие `wait_for` выполнено (`file:`/`deck:`) → владелец задачи;
`mention` само не снимается, ждём (б). (г) `done` с заданным `reviewer` и без записи ревьюера после
`updated` → статус `in_review`, будит ревьюера. (д) прогон завершился без пригодного результата — один
повтор с напоминанием, второй раз подряд → `status: blocked` + строка в `ceo-inbox.md`. «Без пригодного
результата» — это ИЛИ новой записи роли в логе нет (или таймаут), ИЛИ запись есть, но `status` так и
остался `todo` (роль забыла увести задачу — тоже ошибка роли, v1.1). `blocked`/`needs_owner` всегда идут
строкой в `ceo-inbox.md` (дедуп по статус+`updated`). Не больше одного активного запуска на задачу;
всего `MAX_PARALLEL` (умолч. 2, `ALPHA_DISPATCH_MAX_PARALLEL`).

## Защита от петли и перерасхода (v1.1)

- **`MAX_RUNS_PER_TICKET_HOUR`** (умолч. 6, `ALPHA_DISPATCH_MAX_RUNS_PER_TICKET_HOUR`) и **`MIN_GAP_S`**
  (умолч. 60 с, `ALPHA_DISPATCH_MIN_GAP_S`) — троттлинг решений (а)-(г): тикет просто пропускается этот
  тик (не ошибка, задача остаётся как есть, попробуем следующим тиком). Ретраи правила (д) их не
  проходят — они и так ограничены одной попыткой.
- **`DAILY_COST_USD`** (умолч. 150, `ALPHA_DISPATCH_DAILY_COST_USD`) — суточный потолок расхода
  (сумма `total_cost_usd` из JSON-вывода `claude`, копится в `state.json["daily_cost"][<дата>]` по
  календарной дате). Превышен → новые запуски (включая ретраи) не стартуют, одна строка в
  `ceo-inbox.md` на сутки (дедуп).

## Активные запуски переживают перезапуск диспетчера (v1.1)

Каждый запуск зеркалится в `state.json["active_runs"][<ID>]` (`pid`, роль, время старта, `run_file`) и
сохраняется на диск сразу же (не только в конце тика). При старте `tick()` вызывает
`recover_active_runs()`: если зеркало есть, а в памяти процесса записи нет (диспетчер перезапускался) —
живой `pid` подхватывается заново (без повторного запуска `claude`, опрос идёт по `pid`, не по объекту
`Popen`); уже умерший `pid` (прогон успел закончиться, пока диспетчер не работал) обрабатывается как
обычное завершение — лог проверяется, при необходимости ретрай/`blocked`, как всегда.

## Область сессии на роль — `SESSION_SCOPE`

`{"judge": "role", "researcher": "ticket", "engineer": "ticket"}` (правится в `dispatch.py` или
`ALPHA_DISPATCH_SESSION_SCOPE=judge:role,engineer:ticket`). `"ticket"` — `--resume` в пределах
(задача, роль), как раньше. `"role"` — одна долгая сессия роли на все задачи; диспетчер не запускает
вторую задачу такой роли, пока не закончена текущая (задачи — по очереди). **Ротация:** если контекст
**последнего хода** прошлого запуска роли превысил `ROTATE_TOKENS` (умолч. 250 000,
`ALPHA_DISPATCH_ROTATE_TOKENS`) — следующий запуск идёт без `--resume`, в промпт добавляется просьба
перечитать блокнот и `docs/research/reviews/` по нужной задаче. `usage` в JSON `claude -p` — сумма
`input + cache_read + cache_creation` по ВСЕМ ходам запуска, не одного хода (CEO 27.09, живой прогон:
сумма 333 886 при реальном контексте хода ~52 тыс.) — контекст последнего хода
(`_context_tokens_last`) берётся из `usage.iterations[-1]`, а без неё — оценкой `сумма // num_turns`;
оба числа видны в `runs.log` (`ctx_last=`/`ctx_sum=`).

## Запуск

```bash
python .claude/dispatcher/dispatch.py --once   # один тик, для проверки/крона
python .claude/dispatcher/dispatch.py          # цикл раз в 15 с (ALPHA_DISPATCH_INTERVAL)
python .claude/dispatcher/tickets.py new --owner researcher --title "..." [--reviewer judge]
python .claude/dispatcher/tickets.py new --owner researcher --title "..." --backlog   # перенос из TASKS.md
python .claude/dispatcher/tickets.py start TK-001                                     # backlog → todo
python .claude/dispatcher/tickets.py comment TK-001 --author engineer --text "... @judge глянь"
python .claude/dispatcher/tickets.py status
```

`CLAUDE_BIN` — путь к `claude` (по умолчанию из PATH, иначе `/c/Users/Георгий/.local/bin/claude`).
CLI на этой машине залогинен, `--permission-mode bypassPermissions` проверен и живым смоуком (27.09,
см. ниже); `dispatch.py` всегда добавляет этот флаг и `ALPHA_ROLE=<role>` в окружение запуска — по нему
`role_context.py` берёт роль без определения по названию сессии Desktop. Вывод каждого запуска —
`.claude/dispatcher/runs/<ts>-<ID>-<role>.json` (+ `.err.log`), сводка — `.claude/dispatcher/runs.log`,
состояние (`session_id` по scope, `retries`, `last_woken`, `active_runs`, `daily_cost`) —
`.claude/dispatcher/state.json`.

## Будим CEO — `ceo-inbox.md` + `ceo-wake.log` + хук (v1.1)

Каждая запись в `ceo-inbox.md` дублируется короткой строкой (время, задача, причина) в
`.claude/dispatcher/ceo-wake.log` — CEO держит на нём `Monitor`. Отдельно, в сессии CEO хук
`.claude/hooks/role_memory.py` (`ceo_inbox_alert()`) на каждом сообщении подмешивает в контекст
непрочитанные строки `ceo-inbox.md` (после отметки `.claude/dispatcher/.ceo-inbox-seen`, которая тут же
сдвигается) — так же, как уже подмешивается тревога Steam Deck (`deck_alert()`).

## Роль в задаче

Получив тикет через `-p`, роль работает как раньше (устав, блокнот, журнал `.claude/roles/journal/`), но
без диалога: делает следующий шаг, `python .claude/dispatcher/tickets.py comment <ID> --author <role>
--text "..."` (или прямой правкой файла) — что сделала, что дальше; сама правит `status`/`wait_for` в
шапке (диспетчер не понимает прозу — не поменяла `status: todo` → тикет уйдёт в очередь заново, v1.1
это уже считает ошибкой роли и в итоге блокирует задачу). Нужен другой участник — упоминание `@роль` в
тексте записи, не отдельное сообщение. Фоновая работа (юнит на Steam Deck, долгий счёт) —
`status: waiting` + `wait_for: file:<путь>|deck:<путь>` и выйти, не ждать в сессии. Срочно и не терпит
следующего тика — `SendMessage` на `CEO` напрямую (диспетчер это не заменяет, только разгружает поток
обычных шагов).

## Тесты и известные ограничения

`python -m unittest discover -s .claude/dispatcher -p test_dispatch.py -v` — 40 тестов, без сети (кроме
одного ручного `_deck_file_exists()` при разработке, см. ниже), без настоящего `claude` (свой seam
`dispatch._popen`, фейковый `python`-скрипт). Один живой смоук с настоящим `claude -p` — 27.09,
исследователь-роль, задача «допиши в лог слово ок и поставь status: done», ~$0.5, см. `runs.log`.

Ограничения v1.1: ревьюер, оставивший `status: done` с тем же `reviewer:`, может получить повторный
запуск — по завершении ревью стоит снять `reviewer:` или сменить статус; `wait_for: deck:` при пути с
пробелами не гарантирует безопасное экранирование (только простые пути `~/alpha/...` без пробелов —
реальный формат путей на Steam Deck); `MAX_RUNS_PER_TICKET_HOUR`/`DAILY_COST_USD` — защита от петли,
не жёсткая гарантия (данные лежат в `state.json`, ручная правка возможна). Базовый контекст свежей
сессии роли ≈ 52 тыс. токенов (замер владельца); `skillOverrides` в `.claude/settings.json` скрывает
навык строкой-значением `"off"` (уже стоит на 23 навыках) — диспетчерские роли наследуют это
автоматически, отдельно настраивать не нужно.
