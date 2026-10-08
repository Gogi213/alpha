# Критик-2 плагина, часть В — хуки, табло, доки, тесты (TK-108), 09.10.2026

Плагин 1.8.21 (`~/.claude/plugins/cache/.../1.8.21`). Срок — 20 мин запуска, глубина по риску. **Блокеров не найдено.**
Три «важно», остальное — «потом». Каждое «нет проблем» ниже — только про прочитанное; непрочитанное перечислено в конце.

## Замеры

- Тесты хуков + табло: `pytest .claude/hooks .claude/board` — **359 passed, 108,9 с** (Windows, py3.11). CI: матрица 3 ОС × py 3.11/3.12/3.13
  на main/тегах, 1 версия на PR (`.github/workflows/ci.yml`) — Mac/Linux покрыты CI, не руками.
- Всего тестов в плагине 984 (ast). «Без `assert`» по ast — 237, но это методы с хелперами `self.ok/denied/refused`
  (проверено на `test_delete_guard.py:47`, `test_result.py:133`) — **ложные пустышки не найдены**, счёт по ast ничего не говорит.
- complexipy 8.0.1, часть В (hooks + board + dispatcher view2/board_push), функции > 15 (из 674 всего по hooks, board, dispatcher):
  `view2.make` **195**, `delete_guard.scan_one` **142**, `python_scan` 80, `find_close` 64, `git_scan` 60, `_Lexer.run` 56,
  `const_str` 53, `heavy_guard.tar_label` 57, `heavy_label` 51, `find_scan` 47, `git_discard` 45, `cp_scan` 45, `ssh_scan` 37.
  Всего **83 функции > 15** по замеренным каталогам. Прошлый проход мерил radon cc (цикломатика), не complexipy — прямого сравнения «стало хуже»
  нет; по критик-2 А (29 → 29) тренд «не улучшилось».

## Находки

### В-1. Важно — подсказка роли `python …`, а на Mac/Linux чаще только `python3`
`role_context.py:153` (`tickets_command()` → `python "…/tickets.py"`), `dispatch.py:1562,1567` (`"python " + …`), `watch.py:775-792`,
`commands/ceo.md:20`, `commands/rpv-start.md:11`. Хуки сами умеют `python3 → python → py -3` (`hooks/run-hook.sh`,
`hooks.json` PreToolUse), а команда, которую хук ВЫДАЁТ роли, жёстко `python`. На macOS (нет `python` из коробки) каждая
роль на первой же команде `result` получит `command not found`; `install-macos.md:7` признаёт («добавьте `python` ссылкой») — то есть
обязательная ручная предпосылка, которую не проверяет `rpv-doctor` (`doctor.py`: `grep "python\|shutil.which"` — только строки справки и подсказки, проверки `python` в PATH нет).
Доказательство: `grep` выше; `run-hook.sh` ≠ формат команды в инъекции. Правка малая: хук знает `sys.executable` — подставлять его
(абсолютный путь) вместо слова `python`.

### В-2. Важно — SessionEnd: таймаут хука 30 с, Haiku внутри — до 90 с (+ вложенный `claude -p` без изоляции)
`hooks.json` SessionEnd `timeout: 30`; `role_memory.py:117-168` строит конспект ПОСЛЕ вызова `haiku_aux.compact` (`TIMEOUT_S = 90`,
`haiku_aux.py:19`). Медленный Haiku → хук убит на 30 с, файл конспекта не записан; «догон» на следующем старте
(`on_session_start`) идёт тем же путём через тот же Haiku в хуке на 30 с — повтор той же ловушки. Побочное:
`haiku_aux.ask` запускает `claude -p` с унаследованным окружением (`RPV_ROLE/RPV_TICKET/CLAUDE_PROJECT_DIR` не чистятся,
`hide.py`/`haiku_aux.py` — ни слова про `env`) и без отключения хуков: вложенная сессия в tmp-каталоге может подхватить
`role_context`/`role_memory` через `CLAUDE_PROJECT_DIR` (**не проверено запуском**; рекурсию останавливает `--no-session-persistence`
— нет транскрипта → `write_digest` возвращает None — тоже по чтению, не по запуску). Также `EFFORT = xhigh` для механического сжатия
(`haiku_aux.py:18`) — дорого без нужды. Правка: писать конспект без Haiku сразу, Haiku — отдельной попыткой с таймаутом < таймаута хука; `env=` с вычищенными `RPV_*`.

### В-3. Важно — Диспетчерская (`board/server.py`): HTTP по умолчанию на `0.0.0.0`, секрет в пути URL
`server.py:40` (`HOST = … "0.0.0.0"`), токен — префикс пути (`:206`), TLS сервер не умеет; `docs/dashboard.md:5,13` показывает строку
`https://host/<токен>/#<ключ>`, но в доках **нет ни слова** про реверс-прокси/TLS (grep `nginx|TLS|https` в dashboard.md — только эта строка)
— unit `rpv-board.service` ставит прослушивание наружу без TLS. Токен в URL оседает в логах прокси/истории браузера. Смягчает: 404 на всё
вне префикса, `hmac.compare_digest`, ключи хэшем, лимит тела 4 МБ, лог запросов выключен (`:178`). Нет таймаута сокета
(медленный клиент держит поток — slowloris), `MAX_TEAMS=20`. Правка: default `127.0.0.1` + раздел «TLS» в dashboard.md.

### Потом

- **П-1 Утечка alpha в «универсальном» ядре.** `heavy_guard.py:42,176` — `.binlog|.abin`, `/data/alpha/`; `test_delete_guard.py:20-27` —
  `C:\visual projects\alpha`, `benchrun.sh`, `ssh deck`, `ssh vps`; `plan.py:9` (`pc|vps|calc|col|you`, из прошлого прохода — не исправлено);
  `machines.py:6-7`, `dispatch.py:499-505` — `RPV_DECK_KEY/RPV_DECK_KNOWN_HOSTS`, `DISPATCH_DECK_CACHE_S` (имя «deck» — Steam Deck, который
  в alpha списан). `dispatch.py:119-120,145,171,195` — комментарии с В-14/В-153/В-173 владельца alpha. Рабочее, но «универсальный плагин» это опровергает.
- **П-2 Док-долг по переменным.** В коде и не в `docs/` (README + configuration): `RPV_STOP_STRICT`, `RPV_DISABLE`, `RPV_HAIKU_COMPACT/DIAG/EFFORT/MODEL`,
  `RPV_AUTORELEASE`, `RPV_CEO_WAKE`, `RPV_WATCH_LINK_PROBES`, `RPV_CI_WATCH_INTERVAL_S`, `RPV_SPOOL`. (Ложные срабатывания скрипта —
  `RPV_BUS_TOKEN*`, `RPV_URL`, `RPV_RUNS_DIR` — читаются через `_env()`/`jobrun.sh`, проверено; в «нет в коде» их нет.)
- **П-3 Шаблон ролей против живого** — не сверял построчно (`templates/roles/*` vs `.claude/roles/*` alpha).
- **П-4 `find_title` (`role_context.py:73-87`).** Один битый `local_*.json` в `claude-code-sessions` → `json.load` бросает, весь поиск проваливается в
  «роль не определена (JSONDecodeError)» — одна плохая метаданная ломает определение роли для всех сессий. Ловить `ValueError` по файлу, `continue`.
- **П-5 Подстрочный поиск роли по названию** (`role_context.py:180`, `ROLES`): `"ceo" in title.lower()` — любая сессия с «ceo» внутри слова
  («surfaceoptimization») получит роль CEO; порядок списка — первое совпадение. Низкая вероятность, правка — слово целиком.
- **П-6 Сложность.** `view2.make` 195 и `scan_one` 142 — по-прежнему не разрезаны (оба в списке прошлого прохода, срез №10); `delete_guard.py` 2038 строк
  и 80 функций в одном файле при том, что 1.8.16 добавил в него ещё `ceo_signal_write`.
- **П-7 Покрытие.** Файлов без одноимённого `test_*.py` в dispatcher: `ticket` (591 стр.), `tickets` (500), `view2` (489), `ask` (230), `plan` (196), `routes`,
  `lifewatch`, `usage`, `project`, `pulsedata`. Реально покрыто через другие файлы (упомянуты в 1–8 тест-файлах), кроме **`pulsedata.py`: ни одного упоминания
  в тестах**. Табло: `board/server.py`, `machines.py`, `plainify.py`, `mcp_server.py`, `bridge.py` тесты есть; `dispetcher.html`/`phosphor.js` (600 строк UI) — не покрыты ничем.

## Что проверено и нормально (по чтению)

- `hooks.json` PreToolUse fail-closed: нет Python 3 → `exit 2` с понятным текстом; проект без `.claude/roles` → молча пропуск. `run-hook.sh`
  корректно ищет `python3 → python → py -3`, при отсутствии — `exit 0`.
- `stop_result.py`: не падает (весь блок в `try/except`), без вечной петли (`stop_hook_active`), строгий режим — `RPV_STOP_STRICT`.
- `role_context.py`: итог всегда ≤ `LIMIT` (`fit`), личность не режется, сбой — короткая строка.
- `heavy_guard.py`: без `RPV_GUARD_HEAVY_HOST` не действует; чистая классификация, битый регэксп исключений → «исключений нет».
- Сервер табло: `compare_digest` на префиксе безопасен по длине; остальные POST — 404.
- Ссылки в `docs/*.md`, `README.md`, `commands/*.md` — **мёртвых нет** (проверено скриптом, вывод пуст).

## Не прочитано (честно)

`delete_guard.py` — только входы/крэш-ветки и замер сложности, 2000 строк построчно не читал (в т.ч. Windows-пути, `ps_*`, `git_discard`); `role_memory.py` — первые
80 строк, `write_digest`/`on_prompt_all`/`on_notification` частично; `dispetcher.html`, `phosphor.js/css`, `plainify.py`, `bridge.py`, `mcp_server.py`;
`view2.py`, `pulsedata.py`, `board_push.py` (только метрики); `docs/install-windows.md`, `release.md`, `signals.md`, `wait-for.md`; установка
на чистую Mac/Linux не прогонялась — только чтение `install-macos/linux.md`; `templates/roles/*`; навыков в плагине нет (`ls`: agents/commands/hooks/templates).

## Для CEO

Блокеров к закрытию разработки нет. В правку до закрытия, если успеть дёшево: **В-1** (подставить `sys.executable` вместо `python` — 3 места в `role_context.py:153`,
`dispatch.py:1562/1567`), **В-2** (запись конспекта до Haiku + `env` без `RPV_*`). В-3 и всё «потом» — в список «докидываем после суток».
