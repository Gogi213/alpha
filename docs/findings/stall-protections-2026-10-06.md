# Защиты от простоев: как в зрелых системах (обзор 06.10, для TK-070/TK-071)

Повод — простои ночи 06.10 (разбор CEO владельцу ~10:45). Источники сверены с документацией помощником-исследователем;
не проверялись: Chromium/V8 perf bots, Nomad, Erlang supervisors, CrewAI, AutoGen. «Взять» — вывод, не замер.

| № | сбой | механизмы | взять нам |
|---|---|---|---|
| 1 | долгое задание на 1 ядре держит замок 2,5 ч | Slurm backfill по объявленному `--time`; reservations (окна, FLEX); preemption `PreemptMode=SUSPEND,GANG` через cgroup freezer; Argo — семафор ёмкости N; K8s PriorityClass | обязательный `max_runtime` у задания + `systemd-run -p RuntimeMaxSec=`; окна замеров как бронь; долгое пускать, только если кончится до окна или будет заморожено; замок — лиза с TTL |
| 2 | наблюдающая сессия закрылась, простой 1,5 ч | systemd `WatchdogSec=`+`Restart=on-watchdog`; dead man's switch (внешний heartbeat); Slurm `strigger`; Temporal heartbeat timeout | инвариант сторожа: «сервер простаивает И есть готовое в очереди > N мин»; сторож под watchdog; тревога владельцу независимым каналом |
| 3 | 5 замеров испорчены ssh-командами мимо замка | pyperf `system show` (isolcpus, ctxt switches); LLVM `cset shield`; cgroup v2 `cpuset.cpus.partition=isolated`; rustc-perf — машина-коллектор с одним циклом очереди | замок рекомендательный → проверка до/после волны (посторонние юниты, переключения) и авто-«недействительна»; изоляция ядер не спасает от шума HDD |
| 4 | 429 «session limit» принят за холостой ход | Anthropic 429: `retry-after`, `anthropic-ratelimit-*-reset`; Claude Code statusline `rate_limits.*.resets_at`; Agent SDK `rate_limit_event` (`resetsAt`); circuit breaker closed/open/half-open | «лимит» — отдельный исход, не холостой; общий `paused_until = resetsAt + джиттер`, затем один пробный запуск, потом остальные |
| 5 | «ожидание» без условия пробуждения | Temporal: любое ожидание = durable-таймер + сигнал; Airflow sensors: обязательные `timeout`/`poke_interval`, режим `reschedule` | `waiting` без `wake_on` (условие или срок) — отказ; срок по умолчанию → «ожидание просрочено», тикет в ready |
| 6 | хуки блокируют нормальную работу | Claude Code `permissions.additionalDirectories`/`--add-dir`; PreToolUse `permissionDecision: allow|deny|ask|defer` + причина, `if: "Bash(...)"` | список разрешённых путей роли — в конфиге, не в коде хука; для «своих» путей `ask/defer` вместо `deny`; отказы считать |
| 7 | ssh-опрос с таймаутами; ложное событие старого юнита (84 пустых запуска) | systemd `InvocationID` ($INVOCATION_ID, новый на каждую активацию); `systemd-run --wait --collect`; `OnSuccess=/OnFailure=`; path-юниты (inotify); Airflow deferrable + triggerer; OpenHands StuckDetector (4 одинаковых пары действие/результат) | уникальное имя юнита на запуск + сверка InvocationID; ожидание конца — одно долгое ssh с `systemd-run --wait` или событие шины; 3 пробуждения без новой информации → блокировать источник пробуждения, не тикет |

**Топ-5 по пользе/цене:** (1) `max_runtime` + `RuntimeMaxSec`, окна замеров как бронь; (2) пауза очереди по `resetsAt` + пробный запуск;
(3) уникальное имя юнита + InvocationID + `systemd-run --wait` вместо опроса; (4) `waiting` только с `wake_on`/сроком;
(5) сторож по состоянию сервера под watchdog + внешний heartbeat. Дальше: проверка волны на шум (3), `ask` вместо `deny` (6).

Ссылки: slurm.schedmd.com/{sched_config,reservations,preempt,cgroups,strigger}.html; kubernetes.io pod-priority-preemption;
argo-workflows synchronization; man7 systemd.service/systemd.exec/systemd.unit/systemd-run; pyperf.readthedocs.io/system.html;
llvm.org/docs/Benchmarking.html; lwn.net/Articles/936555; platform.claude.com/docs/en/api/rate-limits; code.claude.com/docs/en/{statusline,permissions,hooks};
docs.temporal.io detecting-activity-failures; airflow sensors, deferring; docs.openhands.dev agent-stuck-detector;
martin.kleppmann.com 2016 distributed locking; training.promlabs.com end-to-end watchdog alerts.
