# Критик плагина, часть Б — присмотр, выпуск, шина (TK-098, v2 после возврата Судьи 18:45)

Плагин role-play-vibing **1.8.6** (кэш; v1 — 1.8.5, номера строк сверены заново). Часть Б: watch, lifewatch, supervise, doctor,
start, release, endurance, ceo_triage, bus_link, `.claude/bus/*` + тесты. Прод-код части: 3 440 строк в `dispatcher/` (без
dispatch.py) + 1 066 в `bus/` (с тестами 259). Вход — аудит TK-080 (`plugin-code-audit-2026-10-08.md`), его находки не
переоткрываю, ссылаюсь «TK-080 №N». Критик А (`plugin-critic-A`) — по шине и ssh: схему согласую с ним (п. «Шина»).

Что поменялось против v1 по замечаниям Судьи: срез «шина → аддон» **снят** (против В-192, неверно описан); «watch → в тик»
разбит на безопасный и условный; `triage_waits` и `autorelease` прочитаны; `bump_problem` оставлен (его зовёт merge_rule);
сумма пересчитана по независимым срезам; добавлены колонки 5–9 и замер сложности.

## Замер и доказательства

- **Сложность (radon cc, 1.8.6), функции > 15:** `Harness.round` (endurance) 51 · **`triage_waits` 47** (самая сложная в
  части; TK-080: 48 — совпадает) · `ceo_triage.run_once` 26 · `notify_findings` 25 · `release.main` 20 · `triage_stalls` 19 ·
  `check_second_machine` 18 · `doctor.check_queue` 18 · `check_server_idle` 17 · `check_strays` 17 · `rule_class` 17 ·
  `route_actions` 17 · `check_stale_plan` 16 · `check_deck_frozen` 16. Из 14 функций > 15 в части Б **11 лежат в
  watch.py/ceo_triage.py** — слоях, которые режутся. В supervise/start/lifewatch/bus_link/bus — нет ни одной > 14.
- **Мёртвый код (vulture + grep вызовов вне определения):** `watch._ticket_status` (0 вызовов, 7 строк; TK-080), неиспользуемая
  переменная `start.py:88 attempt`, импорт `urllib.error` в bus_link (TK-080). Остальное vulture-«unused» живое: `job_done`
  (dispatch), `bump_problem`/`tag_release` (merge_rule), `spawn_auto` (release), `Link.take_ack/give_back/maybe_snapshot`
  (dispatch). Мёртвого — ≈ 10 строк; чистка не источник выигрыша.
- **Глушилки `except Exception`:** watch 20 (из них `pass`/return-пустышек 5), start 5, endurance 5, bus_link 5, ceo_triage 4,
  lifewatch 4, doctor 2, release 1, supervise 0. Магические числа: ≈ 30 порогов `WATCH_*` в watch.py, из них alpha и
  тесты не задают ни одного (TK-080 №env: «37 из 45 DISPATCH/WATCH не задаёт никто» — константами).
- **Живые логи alpha.** `ceo-wake.log` 1 448 строк: deck-alert 154, blocked 99, orphan 87, bus-down 43 + bus-up 42 (пары через
  5–6 с), plan-stale-waiting 32, wait-for 21, deck-ssh-error 15. `deck-off` стоит с 03.10, `RPV_DECK_HOST` не задан →
  deck/server-блок watch.py в alpha не исполняется. `supervise.log`: 9 строк, реальный подъём диспетчера 08.10 15:29.
  `triage.jsonl` 186: правила 126, Haiku 50 (18 «action»); ceo_triage в alpha выключен с 1.8.5. `ssh-calls.log` 2 596
  (из критика А: «аварийный» 1 270 — шина лежала > 600 с).
- **Шина — по существу.** `bus.py` (Bus: journal+routes+очереди с ack+hold по блокерам+stale-скан) — самостоятельная и
  аккуратная: CC всех функций ≤ 12, нет alpha-имён. `watcher.py` **параметризован** (`--patterns` по умолчанию `rpv-*`,
  `--progress-glob ~/rpv/progress/*.json`) — моя v1 ошиблась, назвав это утечкой; остаётся только «Linux/systemd».
  `rpv-bus.service`: `/opt/rpv-bus` + `CPUQuota=5%` — шаблон юнита, норма.

## Таблица (1 обяз. · 2 унив. · 3 дизайн · 4 решение · 5 мёртвое · 6 дубли · 7 запахи · 8 CC · 9 ответственность)

| Файл / функция | 1 ОБЯЗАТЕЛЬНО? | 2 УНИВ.? | 3 ДИЗАЙН / 6 ДУБЛИ | 4 РЕШЕНИЕ (строки) | 5–8 | 9 Одна ответственность |
|---|---|---|---|---|---|---|
| **watch.py** `check_dispatcher_alive` (18) | нет: supervise уже сверяет `last_tick` (BEATS), doctor показывает | да | третье место проверки одного факта (supervise, doctor, watch) | **Удалить** −18 | CC <10 | «диспетчер жив» — должен быть у supervise |
| watch `check_blocked_and_needs_owner`, `check_orphan_tickets` (≈ 60) | пересобирают то, что диспетчер сам пишет в ceo-inbox (комментарий в коде: «не полагаясь…»); 99 + 87 строк wake.log — одно событие дважды | да | второй проход по тем же тикетам (диспетчер тикает 15 с, ssh не нужен) | **Влить в тик диспетчера** (чистое чтение файлов, мс — тик не блокирует). −60 / −80 тест | CC 12 | состояние тикета — диспетчер |
| watch `check_no_progress_view`, `check_stale_plan` (≈ 90) | нет: табло без процента, ничего не падает; 32 wake `plan-stale-waiting` | да, но читает чужой `pulse/status.json` | требование табло, оформленное находкой надзора | **Удалить** (табло показывает «нет плана»). −90 / −170 | CC 12, 16 | план шагов — табло/plan.py |
| watch `triage_waits` + `_triage_job/_triage_ci_run/_apply_owned_jobs/probe_wait_target/_wait_target_state` (≈ 320) | сейчас нужен: мёртвая цель, ssh-молчание N раз, «met, но не разбужен». Но то же делает диспетчер — см. ниже «wait_for» | формы `host:calc|vps|deck`, алиасы — окружение alpha | **три пути у `wait_for host:`:** (а) события шины (`dispatch.py:368`), (б) ssh-опрос диспетчера: аварийный `_wait_poller:460` + сверка `_reconcile:654` + `_host_wait_met:522`, (в) `probe_wait_target` сторожа. (в) — второй исполнитель того же условия в другом процессе, через файлы `watch-state.json`/`job-state.json` | **Упростить, в два шага.** Шаг 1: удалить из watch (в) — дубль `_host_wait_met`/`check_wait_for`. Шаг 2: четыре счётчика «страйков» (мёртвая цель, ssh-fail, met-grace, empty-wait) перенести в `_reconcile` диспетчера — он уже делает один ssh на машину раз в 300 с. **Нетто −150…−250**; точное — после чтения тела `_reconcile` (не читал: решение «не доказано» для строк, помеченных ≈) | **CC 47** (топ части); 4 карты `dead/ssh_fail/met_since/empty_wait` в одном цикле | «дожить `wait_for` до конца»: должна быть у диспетчера (одна), сейчас у двух процессов |
| watch `check_server_idle`, `analyze_server`, `holder_ticket`, `check_strays` (≈ 120) | в alpha нужны серверу счёта; в плагине без `benchrun` не срабатывают | **утечка:** `SERVER_ALIAS="calc"`, `benchrun.sh`, `flock` (TK-080 №16) | то же делает lifewatch правильно — команда проекта через `RPV_*_CMD` | **Вынести в адаптер alpha** (`RPV_STRAY_CMD` уже есть). −120 / −100 | CC 17, 17, 15 | «хост простаивает при очереди» — проект |
| watch deck-блок: `check_second_machine`, `check_deck_frozen`, `_ssh_run*`, `DECK_*`, `deck-off`, `_apply_ssh_fail_streak` (≈ 230) | в alpha выключен флагом с 03.10; 154+15+4 wake — историческая | **утечка:** «Steam Deck», `queue/ALERT-*`, `HOLD`, `sync/DISK-FULL`, `df -BG` (TK-080 №16, №17) | то же | **Вынести в адаптер** `RPV_HOST_ALERTS_CMD` → строки; −230 / −450 (классы Deck/Ssh*/Hold/ServerIdle в test_watch ≈ 450 строк) | CC 18, 16 | сигнал «вторая машина» — проект |
| watch `run_once`, `notify_findings`, `normalize_signature`, дедуп (≈ 170) | дедуп нужен, пока находки несут время в подписи | да | корень — нестабильная подпись находки (шапка v2); нормализация постфактум — симптом | **Упростить:** ключ = (вид, ключ), текст не сравнивать. −100 / −150 | **CC 25** (`notify_findings`) | «не повторять тревогу» |
| watch — остаток (heartbeat, instance-lock, main, глобальные пути) | сердцебиение читает supervise | да | `global` на 5 путей (TK-080) | при слиянии уходит с процессом; иначе оставить | — | — |
| **lifewatch.py** `probe_job`, `job_done`, `save_*`, `fetch_owners` (≈ 80) | да: `wait_for job:` (TK-092), `job_done` зовёт dispatch | да: адаптер `RPV_JOB_STATE_CMD`, ОС-независим — **образец** | — | **Оставить** | CC <10; 0 мёртвого | «состояние задания проекта» |
| lifewatch `reason()` → Haiku (≈ 15) | нет: только человекочитаемая причина | да | модель в слое надзора (решение TK-087) | **Решение владельца:** если «без моделей в надзоре» — убрать, оставить хвост лога как есть; иначе оставить. Я не предлагаю `tail -1`: это другая семантика | — | «объяснить причину» ≠ «следить» |
| lifewatch `fetch_strays` (25) | только `check_strays` | да (адаптер) | уйдёт со срезом server-блока | **Удалить вместе с адаптерным блоком** −25 | — | — |
| **supervise.py** (204) | **да — единственный перезапускатель** (supervise.log 08.10 15:29, сердцебиение 11 с) | да: schtasks / systemd timer / launchd | `SESSION_ENV` дублирует `release.SESSION_DROP` (TK-080); `BEATS` содержит ceo_triage | **Оставить.** Чистка: один список session-env в `project.py` (−10), `ceo_triage` из `BEATS` вместе со срезом ниже | CC ≤ 13; 0 мёртвого | «поднять упавшую службу» |
| **doctor.py** (214) | не нужен для работы; нужен как проверка `release.auto` | да | `check_service` дублирует `supervise.decide`; **`check_idle` делает выпуск откатываемым** (TK-080 №13, блокер); `run_checks` смотрит только dispatch/watch, не `services()` (TK-080 №12) | **Оставить, исправить по TK-080 №12/13** (иначе «оставить» не доказано); `check_service` через `supervise.decide` −20 | CC `check_queue` 18 | «показать состояние» (read-only) |
| **start.py** (451) | **да:** detach WMI/systemd/Popen (CLAUDE.md alpha — «вне job») | да, три ОС | `stop_running` гасит только pid из файла — полу-выпуск (TK-080 №6/11); дубль `_pid_alive*` в dispatch (TK-080 proc.py); `ALPHA_` в `FORWARD_ENV_PREFIXES` | **Оставить**; убрать префикс `ALPHA_` (миграция: одна строка в `supervise.env.json`) −10; остальное — по TK-080 | CC 14, 13, 13; `attempt` (5) | «запустить/остановить службы» |
| **release.py** `update`/`rollback`/`autorelease`/`run_auto_locked` (≈ 230) | **да:** прочитан `autorelease` 240–270: update → restart → verify → откат + журнал `release-journal`; замок+`pending` — один выпуск за раз. Без него новая версия не подхватывается службами | зависит от `claude plugin`+GitHub, кроссплатформенно | `verify_alive` = `doctor` rc 0 (TK-080 №13) | **Оставить** (после исправления №13) | `main` CC 20 | «выпустить и откатить» |
| release `bump`, `check` (≈ 40) | инструмент хозяина репозитория (CLI `bump`/`check`) | да | лежит в поставке пользователя | **Вынести в `tools/`** репозитория. −40 в поставке. **`bump_problem`, `tag_release` — оставить:** их зовёт `merge_rule.py:192` (рабочая проверка) | — | — |
| **endurance.py** (494) | тест-обвязка: CI `endurance.yml`, `conftest.py` | да | продакшн-каталог несёт харнесс; нет cleanup шины/сторожа (TK-080 №14); `Harness.round` **CC 51** — самая сложная функция плагина | **Вынести в `tests/`** (−494 из поставки), cleanup — по TK-080 №14 | CC 51 | «стресс-тест диспетчера» |
| **ceo_triage.py** (317) | **нет:** опт-ин `RPV_CEO_TRIAGE=1`, в alpha выключен; маршруты TK-094 ведут `next:` без CEO | да, но тянет `claude -p` в надзор | четвёртый слой над тремя; сортирует шум, который создаёт сам надзор | **Удалить**, тем же срезом: `BEATS["ceo_triage"]` (supervise.py:30), `OPTIONAL` в start.py:49, тесты `test_supervise.py:88-89,204`, хук-сводка `role_memory.py:268` (часть В). −317 −15 / −144 | CC 26, 17, 17, 15 | «разгрузить CEO от шума» — источник шума лечить в нём |
| **bus_link.py** (153) | да: клиент шины диспетчера, long-poll, ack; без него события не доходят (`dispatch.py:368`) | да | `Link.take_ack/give_back/maybe_snapshot` использованы (dispatch) | **Оставить**; убрать неиспользуемый `urllib.error` | CC <10 | «связь диспетчера с шиной» |
| **bus/** bus.py 266, busclient 107 | да, В-192 (SETTLED-index:195: «все пробросы только через шину»); события закрывают `wait_for host:` | да: нет alpha-имён, stdlib | sqlite+routes+hold — цельная; CC ≤ 12 | **Оставить** | CC ≤ 12; vulture — только обработчики http (ложные) | «журнал событий и очереди с ack» |
| bus/watcher.py 175, jobrun.sh 19, 2 юнита | да, поставляет события машин; ssh-опрос лишь запасной | Linux/systemd; параметры вынесены в аргументы | — | **Оставить**; в README указать «только Linux» | CC 12 | «события машины → шина» |

## Слои надзора

По факту: диспетчер (тик 15 с) → watch (120 с) → supervise (ОС, 5 мин) → doctor (по запросу) → lifewatch (внутри watch) →
ceo_triage (опт-ин). Необходимые: **диспетчер** (владеет тикетами, wait_for и сердцебиением) и **supervise** (единственный,
кто перезапускает). doctor — запрос, а не слой. watch сегодня выполняет **две несовместимые работы**: (а) чтение файлов
тикетов (blocked/orphan/plan — безопасно в тик диспетчера, ssh нет); (б) ssh-проверки хостов и ожидаемых целей (блокируют
15-секундный тик: `ssh timeout=20`, `_ssh_run` 10 с с повтором). Для (б) механизм уже есть в диспетчере — поток
`_wait_poller` и `_reconcile` (один ssh на машину раз в 300 с); новый поток не нужен. alpha-хостовые проверки — адаптер проекта.
Итог: watch как процесс исчезает **после** шага 2 (перенос счётчиков в `_reconcile`); до тех пор остаётся ~400 строк.

## Шина: цена схем (согласовано с частью А)

- **Схема (а), как сейчас:** события + сверка раз в 300 с; аварийный ssh включается только когда шина лежит > 600 с.
  Остаток ssh/сутки определяется доступностью шины; шина стоит на calc-сервере, диспетчер на ПК — отсюда 1 270 «аварийных».
- **Схема (б), без шины:** постоянный опрос; задержка реакции = шаг опроса, ssh/сутки ≈ число ждущих × (86 400 / шаг).
  Не лучше: В-192 фиксирует (а).
- **Рекомендация:** (а) остаётся; лечить **корень** 1 270 — почему шина недоступна (вне моей части, критик А); пока не
  вылечено — `bus-down/up` не будят CEO короче N мин (−85 строк шума wake.log, 5,9 %).

## Топ-10 срезов (независимые, по выигрышу; пересекающиеся — подпунктами)

1. **Адаптер alpha для хост-находок** (server + deck блок watch → `RPV_HOST_ALERTS_CMD`): −350 прод / −550 тест. Закрывает утечки `calc`, `benchrun`, «Steam Deck» (TK-080 №16/17).
2. **Слить дубль `wait_for` сторожа с диспетчером** (шаги 1–2, `triage_waits` CC 47 → ≈ 20 в `_reconcile`): −150…−250. *Не доказано до чтения `_reconcile`.*
   - 2а. **ticket-проверки watch (blocked/orphan) в тик, plan-проверки удалить:** −150 прод / −250 тест. Безопасно (нет ssh).
3. **endurance.py → `tests/`**: −494 из поставки (код остаётся; cleanup — TK-080 №14).
4. **ceo_triage удалить + BEATS/OPTIONAL/тесты:** −332 / −150 (шина и хук части В — отдельно).
5. **Нестабильная подпись находки → стабильный ключ** (убрать `normalize_signature` и сравнение текста): −100 / −150.
6. **release `bump`/`check` в `tools/`:** −40 в поставке (`bump_problem`/`tag_release` остаются — merge_rule).
7. **Одна копия session-env + снять `ALPHA_*` совместимость:** −25.
8. **`check_dispatcher_alive` удалить** (supervise и doctor): −18.
9. **bus-down/up не будят CEO < N мин:** −85 строк шума в `ceo-wake.log`.
10. **Константами 37 неиспользуемых `P.env` частей watch/start** (TK-080): ≈ −40 вызовов, поведение прежнее.

**Сумма независимых 1–8:** ≈ 350 + 200 + 150 + 494 + 332 + 100 + 40 + 25 + 18 ≈ **1 700 прод** (из них 494 — перенос в tests/, не удаление) и ≈ 1 100 тест. v1 называл 2 700 прод — разница: снят срез шины (−1 060) и учтена пересекаемость №2/№5.

## Карта «ответственность → один модуль» и пересечения (для сведения CEO с частями А/В)

| Ответственность | Сейчас | Должен |
|---|---|---|
| Файлы тикетов (чтение/запись) | ticket.py (владелец) **+ watch пишет** `append_log("watch")`, `write_header_updates` + dispatch | диспетчер/ticket.py; watch писать не должен |
| Сердцебиение и перезапуск | dispatch пишет; supervise (BEATS); doctor `check_service`; watch `check_dispatcher_alive` | dispatch пишет, supervise читает; doctor показывает через `supervise.decide` |
| pid-замок, живость процесса | start.py `is_ours`/`stop_running` + dispatch `_pid_alive*` ×3 + ci_watch + board_push (TK-080 proc.py) | один `proc.py` |
| `wait_for host:` до конца | dispatch (события, `_wait_poller`, `_reconcile`) + watch `triage_waits` + lifewatch `job-state.json` | диспетчер; lifewatch — адаптер состояния |
| Запись CEO-входа (`ceo-inbox.md`/`ceo-wake.log`/очередь `ceo`) | dispatch `_ceo_file_write`, watch `notify_findings`, ceo_triage `route_actions`, ci_watch, bus_link `ceo_line` | диспетчер (одна функция записи); остальные зовут её |
| Шина | bus.py (сервер) + busclient + bus_link + ci_watch + ceo_triage + doctor + endurance — 6 клиентов | busclient — единственный клиент, остальные через него (часть А/В — подтвердить) |
| Установка/выпуск версии | release (update/auto) + start.start (перезапуск) + supervise (подъём) — три «поднять службы» (TK-080 №6) | `start.start_all` одним вызовом из release и supervise |
| Тест-харнесс | endurance.py в `dispatcher/` | `tests/` |

Пересечения с частью А (дисп./тикеты/ssh): wait_for, запись CEO-входа, pid-замок. С частью В (хуки/табло): `role_memory.py:268`
(сводка ceo_triage), `pulse/status.json`, `plan.py`.

## Что оставляю и чем доказано

`supervise` (supervise.log 08.10 15:29; единственный перезапускатель), `start` (CLAUDE.md: WMI вне job), `lifewatch`
(TK-092, `RPV_JOB_STATE_CMD`), `release.update/rollback/auto` (прочитан `autorelease`; без него службы не подхватят новую
версию после влития PR), `bus*` (В-192; события закрывают `wait_for host:`), `doctor` (после TK-080 №12/13).

## Что не проверено

Тело `dispatch._reconcile` и `_wait_poller` (не читал целиком) — от них зависит размер среза 2; `bus.py` стороны hold/stale
прочитаны по сигнатурам и схеме, не построчно; сложность замерена `radon cc` (complexipy не ставил — тот же класс метрики,
`triage_waits` 47 у radon и 48 у TK-080).

## Нужно решение владельца

1. Оставлять ли модель Haiku в слое надзора (`lifewatch.reason`, `ceo_triage`) — от этого зависит удаление `reason()`.
2. Согласие на двухшаговое слияние watch → диспетчер (шаг 1 — без ssh, безопасный; шаг 2 — после чтения `_reconcile`).
Остальное — удаления без последствий для alpha (ceo_triage и deck выключены).
