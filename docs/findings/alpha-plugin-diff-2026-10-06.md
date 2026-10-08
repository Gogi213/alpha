# Опись расхождений: механика команды в alpha и плагин role-play-vibing (TK-077 п.1, В-194)

Сняли 06.10. Плагин — `origin/main` = `9c9c74f` (PR #11 влит; #12–#15 цепочки TK-076, плагин 1.8.0, ещё открыты).
Alpha — `.claude/dispatcher/`, `.claude/hooks/`, `tools/bus/`, `tools/pulse/`. Сравнение — `diff --strip-trailing-cr`
(в alpha файлы с CRLF, строки считались вдвое — числа ниже честные). Статусы: **плагин** — уже есть, alpha-копию можно
убрать после переключения; **особое** — есть только в alpha, едет в плагин как общий механизм + настройка;
**разошлось** — есть и там и там, но по-разному.

Происхождение: плагин вынут из alpha ≈ 04–05.10 и обобщён (проект по `--project`/`RPV_PROJECT`, `RPV_*` с запасными `ALPHA_*`,
`project.py`, `doctor.py`, `start.py`, `board_push.py`). Alpha после этого получила TK-074 (шина как стандарт сигналов CEO,
сверка wait_for, хуки) и TK-060/061 (план на табло) — их в плагине нет.

## Диспетчер `dispatch.py` (alpha 2159 / плагин 2078 строк; 266 строк только в alpha, 185 только в плагине)

| Что | Статус | Решение |
|---|---|---|
| Пути проекта: `configure_project`/`ensure_project`/`project.py`, `CODE_DIR`, `PROJECT_FOUND`, `PROGRESS_DIR` (плагин) против констант `STATE_FILE/PID_FILE/RUNS_*/…` (alpha) | разошлось | брать плагин; alpha-констант не нужно |
| `plan_cli`/`tickets_cli`, `_proc_comm`/`_ps_field` (проверка pid на macOS, fix/macos-pid-check) | плагин | alpha не хватает — берётся |
| Сверка `_reconcile` (+`_parse_recon`, `_recon_script`, `WAIT_RECON_S`, `_RECON_LAST`, `_RECON_MISS`, тревога `recon-miss`, `_needs_probe`, `WATCHED_ALIASES`, `_WL_REG`, `_is_progress_json`) — один ssh раз в 5 мин по всем ждущим `host:…`, подтверждённая регистрация пути у сторожа машины | особое | в плагин: общий механизм «сверка wait_for по машине»; настройка — алиасы машин (`RPV_<АЛИАС>_HOST`), период, путь списка слежения (`watch.list`). **Опасность:** живой случай chain55 — нужен тест плагина |
| `_log_ssh_call`, `SSH_CALLS_LOG` — журнал ssh-вызовов диспетчера с причиной | особое | в плагин как есть (путь — в каталоге состояния) |
| `signal_prio`, `NORMAL_KINDS`, `_ceo_file_fallback`, `_bus_down_long`, `BUS_DOWN_SSH_S`, `_SSH_ALERTED` — стандарт сигналов (В-192): CEO получает сигналы только очередью шины с приоритетом `urgent`/обычное; файл `ceo-inbox.md` — запасной путь при лежащей шине; ssh-опрос wait_for только при падении шины > 10 мин | особое | в плагин: механизм общий (приоритеты по виду сигнала), настройка — порог 10 мин, список «срочных» видов |
| `CEO_WAKE_LOG`, `CEO_INBOX` — те же имена в плагине (inbox как «одна строка за период») | разошлось | брать alpha-логику (шина → очередь), файл — запасной |
| Потеря `next: ceo` (живой случай 06.10) | разошлось | чинится в плагине #13/#15 (цепочка TK-076) — после влива alpha-копия ничего не добавляет |
| TK-070 (429, простой сервера, событие юнита только с ssh-подтверждением), TK-072 (InvocationID, `юнит.запущен`) | плагин (один и тот же код, поведение совпадает по числу вхождений) | — |
| Лимиты ролей: `ROLE_PARALLEL`, `MAX_PARALLEL`, `MAX_RUNS_PER_TICKET_HOUR`, `MAX_SAME_STATUS_RUNS`, `MAX_REVIEW_R` | плагин (читается из окружения) | alpha ставит `ALPHA_DISPATCH_*` как сейчас; в плагине — `RPV_DISPATCH_*` с запасными `ALPHA_*` (проверить в п.3) |

## Сторож `watch.py` (810 / 750; 149 / 89)

| Что | Статус | Решение |
|---|---|---|
| `check_steam_deck`, `DECK_OFF_FLAG` (флаг deck-off) | особое (деки нет в плане, В: «не трогать») | в плагин как общий `check_machine` с настройкой машин; плагин уже имеет `check_second_machine`/`DECK_ROOT` — свести к одному |
| `check_no_progress_view`, `check_stale_plan`, `_wake_for_plan`, `_remind_plan_waiting`, `NO_PLAN_MINUTES`, `PLAN_LAG_MINUTES` (TK-060/061: нет плана — будит; застывший план — напоминание в лог) | особое | в плагин: общий механизм «план на табло» (в плагине есть `plan.py`/`pulsedata.py`/`view2.py`, но нет проверок сторожа); настройки — минуты |
| `WATCH_PID_FILE`/`WATCH_STATE_FILE`/`WATCH_HEARTBEAT_FILE` против `_set_paths`/`configure_project` | разошлось | плагин |
| Подтверждённая регистрация пути + слив spool (TK-074) | особое | вместе со сверкой выше |

## `tickets.py` (333 / 262) и `ticket.py` (492 / 496)

| Что | Статус | Решение |
|---|---|---|
| `cmd_inbox` / `_ceo_fallback_unread` / `bus_emit` — очередь CEO (`tickets.py inbox [--peek]`), события `задача.<ID>.*` на шину | особое | в плагин (шина в плагине уже есть, `bus_link.py`); зависит от стандарта сигналов выше |
| Авто-`next` для записи Судьи без `--next` (todo/in_progress/in_review/waiting → владелец) | особое (TK-044, 04.10) | в плагин. Живая связка с #15 («ход без метки») — проверить, не дублирует ли |
| «ВОПРОС ВЛАДЕЛЬЦУ» → срочное событие `owner-question` | особое | в плагин вместе со стандартом сигналов |
| `--project`, `ensure_project`, `D.P.env("ROLE")` | плагин | брать плагин |
| `ticket.py`: `WAIT_FOR_HOSTS` словарь адресов `root@89.163.242.211…` против кортежа алиасов + `RPV_<АЛИАС>_HOST` | разошлось | **адреса из кода — в настройку** (плагин верно), alpha задаёт `RPV_CALC_HOST/VPS/DECK` |
| Форма `host:vps:<путь\|unit:имя>` | плагин (форма `host:<алиас>:…` в обоих; `vps` в обоих алиасах) | — |

## Шина событий

| Что | Статус | Решение |
|---|---|---|
| `bus.py` (alpha `tools/bus/` ↔ плагин `.claude/bus/`): 10/6 строк | разошлось (мелочи) | уточнить по diff при подготовке PR |
| `busclient.py`: `post(..., spool=True)` (alpha) — флаг «не писать в spool, вызывающий пойдёт запасным путём»; `DEFAULT_URL` = адрес сервера счёта, токен `/opt/alpha-compute/bus/token`, `ALPHA_BUS_*` | особое + разошлось | в плагин: параметр `spool`; адрес/токен — только настройка (`RPV_BUS_URL`, `RPV_BUS_TOKEN_FILE`) — в плагине уже так |
| `watcher.py`: `flush` spool на каждом шаге (TK-074), шаблоны `tk*`,`alpha-*`, `/data/progress/*.json`, `watch.list` | особое + настройка | в плагин: слив spool; шаблоны и пути — аргументы/настройка (в плагине `rpv-*`, `~/rpv/progress/`) |
| `routes.json`: маршрут `диск.*` → ceo | особое | в плагин как пример маршрута или настройка проекта |
| Порт 8788 по адресам nftables | настройка машины (не код) | в настройку alpha |
| `bus_link.py`: очередь `ceo` читает диспетчер (плагин) против CEO командой `inbox` (alpha) | разошлось | брать alpha (очередь CEO читает только CEO, В-192) — в плагин |

## Присмотр `supervise.py` (109 / 166)

| Что | Статус | Решение |
|---|---|---|
| Alpha: Планировщик Windows 5 мин, WMI-старт (`wmi_command`, `wmi_start`), `TARGETS`, `DISPATCH_ENV` (env диспетчера), задача `alpha-supervise` | особое по формату, но плагин умеет то же шире | брать плагин: `schtasks_create`, `systemd_files`, `launchd_plist`, `load_env`/`snapshot_env`, `task_name`. Проверить: WMI-старт вне job (CreateFlags 512, переживает закрытие Claude) — в плагине ли; если нет — в плагин. Env диспетчера alpha → `load_env` |

## Хуки `.claude/hooks/`

| Файл | Статус | Решение |
|---|---|---|
| `role_context.py`, `role_memory.py` | разошлось (плагин: `RPV_*`, метка сессии `/ceo`, `STATE_DIR`) | плагин |
| `delete_guard.py` (alpha 219 строк расхождений, `test_delete_guard` 680) | разошлось: плагин обобщён (`RPV_GUARD_*`), alpha держит жёстко прошитые корни хостов (Steam Deck, VPS, `/data/tk0*`, Storage Box порт 23, `root/`, `deep/`) | плагин + настройка `RPV_GUARD_*` (корни, запретные хосты); тесты alpha по жёстким путям — в настройку. Самое рискованное: страж денег/данных |
| `bench_guard.py` (замок замеров, 06.10) | особое (сервер счёта 89.163.242.211, `benchrun.sh`) | **не механика команды, а охрана конкретного сервера** — остаётся в alpha как проектный хук; но `delete_guard` в alpha импортирует `bench_guard` → в плагине нужна точка расширения «проектные проверки команд» (список хуков из настройки) |
| `ceo_signal_guard.py` (запрет записи в ceo-inbox/wake.log) | особое | в плагин (имена файлов — из каталога состояния) |
| Регистрация: `.claude/settings.json` у alpha против `hooks/hooks.json` плагина | разошлось | после переключения — убрать записи alpha-копии из `settings.json`, остаётся только проектное |

## Табло / Диспетчерская

| Что | Статус | Решение |
|---|---|---|
| `tools/pulse/{plan,ask,pulsedata,view2,plainify,mcp_server,page2}` ↔ плагин `.claude/dispatcher/{plan,ask,pulsedata,view2}`, `.claude/board/*` | разошлось: `view2` 213/134, `plainify` 363/118, `mcp_server` 25/45, `pulsedata` 11/19 | отдельная опись: табло свёрстано по нескольким версиям (1.5–1.7); живой адрес `.claude/pulse/board-url.txt`, сборщик `collect.py` (только alpha) |
| `collect.py`, `pulse.py`, `tools/pulse/web/`, `alpha-board.service` | особое | `collect.py` — проектный сборщик данных → в настройку/плагин `board_push.py` (в плагине уже есть) |
| `tools/dashboard` | не механика | остаётся в alpha |

## Тесты

`test_dispatch` 4070 / 4128 (198 / 256), `test_watch` 949 / 879, `test_supervise` 59 / 115, `test_bus_link` 114 / 126.
`test_signals.py` (163 строки: очередь CEO, приоритеты, запасной файл, ssh при лежащей шине, журнал ssh-вызовов,
`ALPHA_BUS_DISABLE`) — только в alpha → переезжает в плагин вместе со стандартом сигналов. Тесты живых случаев 06.10
(chain55, `next: ceo`, waiting без хода) — chain55: `test_dispatch` alpha (TK-074); `next: ceo` и waiting — тесты #13/#15 плагина.

## Объём работ в плагин (PR после влива #12–#15)

1. **PR-A «сигналы CEO шиной» (стандарт В-192)**: `signal_prio`, запасной файл, `tickets.py inbox`, авто-`next` Судьи, `owner-question`, `ceo_signal_guard`, `bus_link` без чтения `ceo`, `busclient.post(spool=)`, `test_signals`. Настройка: порог 10 мин, виды «срочных».
2. **PR-B «сверка wait_for»**: `_reconcile`, регистрация пути у сторожа, слив spool, журнал ssh-вызовов, тревога `recon-miss`. Настройка: алиасы машин (`RPV_<АЛИАС>_HOST`), период, путь `watch.list`, шаблоны юнитов/прогресса.
3. **PR-C «сторож: план и машины»**: `check_no_progress_view`, `check_stale_plan`, `_wake_for_plan`; `check_steam_deck` → общий `check_machine` + флаг-пауза; настройка — минуты, флаг.
4. **PR-D «охрана»**: точка расширения проектных проверок в `delete_guard` (для `bench_guard`); перенос обобщённых правил alpha в `RPV_GUARD_*`.
5. **Присмотр**: сверить WMI-старт с плагином; env диспетчера alpha → `load_env`.
6. **Табло**: отдельная опись `view2`/`plainify`/`mcp_server` — расхождение велико и не блокирует переключение диспетчера.

## Что не проверено (честно)

Не читал построчно: тела `dispatch.py` (27 блоков diff), `bus.py`, `view2.py`/`plainify.py`; статусы «плагин» по ним —
по набору функций и счётчикам вхождений, не по поведению. Перед каждым PR — построчный diff своей части. Версия плагина main
на момент описи — до 1.8.0; после влива #12–#15 опись обновить (особенно `dispatch.py`, `watch.py`, `release.py`).
