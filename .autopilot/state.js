window.STATE =
{
  "slug": "lob-density-ed3",
  "dir": "2026-09-11-lob-density-ed3--wip",
  "title": "Бот по плотностям стакана. Фаза 1 — вердикт по профилям",
  "mode": "semi",
  "depth": "deep",
  "polish": null,
  "tier": "T3",
  "briefFile": "2026-09-11-brief.md",
  "memoryFile": "CLAUDE.md",
  "skillDir": "~/.claude/skills/autopilot",
  "startedAt": "2026-09-11T00:20:00+04:00",
  "updatedAt": "2026-09-11T03:33:00+04:00",
  "finishedAt": null,
  "note": "Вторая редакция плана за день. Владелец поправил три вещи: архитектура сразу под бота; система быстрая с первого дня; тестовые прогоны не дольше 5 минут. Плюс разведка на трёх инструментах пула: данные не совпали с ожиданием по определению «крупного» уровня — H3 переопределён как пол. Прогон 2026-09-08 закрыт, его состояние в archive/.",

  "stages": [
    { "id": "preflight", "status": "done", "startedAt": "2026-09-11T00:20:00+04:00", "finishedAt": "2026-09-11T00:32:00+04:00", "note": "каталог прогона, бриф дословно" },
    { "id": "manifest",  "status": "done", "startedAt": "2026-09-11T00:32:00+04:00", "finishedAt": "2026-09-11T02:00:00+04:00", "note": "82 требования и 3 добавления; R78–R82 — дополнения владельца 2026-09-11" },
    { "id": "briefing",  "status": "done", "startedAt": "2026-09-11T00:45:00+04:00", "finishedAt": "2026-09-11T01:55:00+04:00", "note": "три ответа владельца: продукт — бот; 5 минут — тесты; форма — autopilot. Две развилки: CLUSDT, G_min" },
    { "id": "spec",      "status": "done", "startedAt": "2026-09-11T01:00:00+04:00", "finishedAt": "2026-09-11T02:03:00+04:00", "note": "вторая редакция: 49 историй, шесть швов; 17 находок G2 внесены; повторный G2 запущен" },
    { "id": "plan",      "status": "done", "startedAt": "2026-09-11T01:10:00+04:00", "finishedAt": "2026-09-11T02:05:00+04:00", "note": "15 тасков в 7 волн; G3 в обе стороны" },
    { "id": "build",     "status": "active", "startedAt": "2026-09-11T02:20:00+04:00", "note": "волна 1: таск 01 — ревью дало 4 blocking, дозапрос свежему исполнителю (разрез pick.rs, docstring H10, В-29) — 0 из 15 готово" },
    { "id": "review",    "status": "pending", "note": "пять осей + семь запретов горячего пути; три постоянных ревьюера; десять обязательных раундов" },
    { "id": "final",     "status": "pending" }
  ],

  "requirements": {
    "total": 85, "done": 8, "inTicket": 73, "deferred": 4, "dropped": 0, "placeholder": 0, "open": 0,
    "note": "done узкое: механизм есть, тесты зелёные, служит редакции 3 без изменений. Ни одного артефакта задачи на диске нет."
  },

  "tickets": [
    { "id": "01", "title": "Вычистить отменённое и разрезать commands/lob.rs", "requirements": ["R18","R19","R32","R37","R43","R49","R71","R73"], "blockedBy": [], "wave": 1, "zone": ["src/","docs/plan/"], "status": "repair", "startedAt": "2026-09-11T02:20:00+04:00", "returnedAt": "2026-09-11T03:32:00+04:00", "tests": "448 passed, 0 failed, 5 ignored", "retries": 0, "repairs": 1, "handoffs": 0, "review": { "R-A": "1 blocking: pick.rs 2666 строк", "R-B": "нет; 2 находки: docstring H10 в pilot.rs/watch.rs", "R-C": "3 blocking: pick.rs; D-H3 против §9; G0 продление без записи" } },
    { "id": "02", "title": "H3 в двух режимах; размер как ось", "requirements": ["R14","R40"], "blockedBy": ["01"], "wave": 2, "zone": ["src/lob/levels.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "03", "title": "net_fill: формула и совместный интервал", "requirements": ["R02","R05","R06","R07","R08","R50"], "blockedBy": ["01"], "wave": 2, "zone": ["src/lob/costs.rs","src/stats/mod.rs","src/lob/cells.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "04", "title": "lob session: сессия по пулу — и это Feed бота", "requirements": ["R12","R37","R38","R41","R43","R75i","R78","R79","R80"], "blockedBy": ["01"], "wave": 2, "zone": ["src/commands/record.rs","src/commands/lob/session.rs","src/feed/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "05", "title": "lob power: планка до сбора (G-POWER-A)", "requirements": ["R47","R59","R68","A01"], "blockedBy": ["01"], "wave": 2, "zone": ["src/lob/final_metrics.rs","src/commands/lob/power.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "06", "title": "Сетка семи осей, час суток как ось", "requirements": ["R01","R16","R17","R20","R23","R24","R41","R46","R61"], "blockedBy": ["01"], "wave": 2, "zone": ["src/lob/shortlist.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "08", "title": "lob pick начисто: пул заморожен, пол H3", "requirements": ["R14","R21","R22","R23","R24","R25","R26","R27","R28","R29","R30","R31","R33","R34","R35","R36","R69"], "blockedBy": ["01"], "wave": 2, "zone": ["src/commands/lob/pick.rs","docs/plan/candidates.csv","instruments.csv"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "07", "title": "lob watch на сессиях", "requirements": ["R39","R42","R49","R77i"], "blockedBy": ["01","04"], "wave": 3, "zone": ["src/lob/watch.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "09", "title": "Пилот: отладка ≤5 мин, затем два часа; G-DEBUG, G0, G-POWER-B", "requirements": ["R40","R51","R68","R78","R81","A01"], "blockedBy": ["01","02","08"], "wave": 3, "zone": ["src/commands/lob/pilot.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "15", "title": "Горячий путь: on_event под Bot<MD>, ордер до триггера, G-LAT, dry-run", "requirements": ["R09","R74i","R78","R79","R80","A04"], "blockedBy": ["01","04"], "wave": 3, "zone": ["src/lob/strategy.rs","src/bybit/trade_ws.rs","src/commands/lob/react.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "10", "title": "lob profiles: таблица профилей", "requirements": ["R01","R15","R55","R57","R58","R59","R65","R66"], "blockedBy": ["03","06","09","15"], "wave": 4, "zone": ["src/commands/lob/profiles.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "11", "title": "lob backtest: отчёт на N профилей, та же on_event", "requirements": ["R03","R09","R10","R11","R59","R80"], "blockedBy": ["03","09","15"], "wave": 4, "zone": ["src/lob/backtest.rs","src/commands/lob/backtest.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "12", "title": "Сбор сессиями, шорт-лист, подтверждение", "requirements": ["R44","R45","R46","R47","R48","R56","R59","R67","R77i"], "blockedBy": ["07","10"], "wave": 5, "zone": ["docs/findings/","src/commands/lob/shortlist.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "13", "title": "Вердикт в шапке шорт-листа, DSR подключён", "requirements": ["R47","R49","R50","R51","R52","R53","R54","R59","R68","A03"], "blockedBy": ["11","12"], "wave": 6, "zone": ["src/commands/lob/shortlist.rs","src/lob/final_metrics.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "14", "title": "Чистка репозитория и память проекта", "requirements": ["R65","R70","R71","R73"], "blockedBy": ["13"], "wave": 7, "zone": ["data/",".autopilot/","."], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 }
  ],

  "gates": [
    { "id": "РВ-0",      "status": "passed",  "note": "пять независимых ревью — docs/plan/REVIEW-2026-09-11.md" },
    { "id": "РВ-Р",      "status": "passed",  "note": "разведка: 3 инструмента пула × 5 мин — docs/plan/RECON-2026-09-11.md. Сверка 6/6 ok; levels=0 при прогреве 60 мин; p99 даёт 1 уровень/мин и 0 eaten" },
    { "id": "G2",        "status": "passed",  "note": "дважды: 17 + 5 находок, все внесены. D01 снят по уточнению владельца: ≤5 мин — фаза отладки, окно repeat_count — скользящий час по §3" },
    { "id": "G-DEBUG",   "status": "pending", "note": "сквозной отладочный прогон ≤5 мин без дефекта — граница между отладкой и двухчасовым пилотом §11" },
    { "id": "G3",        "status": "passed",  "note": "15 тасков; трассировка в обе стороны" },
    { "id": "GC",        "status": "pending", "note": "каждый таск с кодом" },
    { "id": "G-LAT",     "status": "pending", "note": "реакционный путь p99 < 5 мс на живом потоке — таск 15" },
    { "id": "G-POWER-A", "status": "pending", "note": "N, SR0, требуемый Шарп — таск 05" },
    { "id": "G-POWER-B", "status": "pending", "note": "замеренный Шарп против требуемого — таск 09. Разведка: −0.40 / −0.32 / +0.24 против ≈2.7" },
    { "id": "G0",        "status": "pending", "note": "таск 09" },
    { "id": "G1",        "status": "pending", "note": "eaten и pulled ≥ 5%. Разведка: eaten 2.3% / 7.1% / 1.5%" },
    { "id": "G2-в",      "status": "pending", "note": "шорт-лист заморожен — таск 12" },
    { "id": "G3-в",      "status": "pending", "note": "net_fill с поправкой DSR — таск 13" },
    { "id": "G4-в",      "status": "pending", "note": "PnL на медианном и p95 RTT — таск 11" },
    { "id": "РВ-М",      "status": "pending", "note": "методическое перед вердиктом" }
  ],

  "reviewers": {
    "R-A": { "axes": ["manifest","spec"], "handle": "spawned 2026-09-11T03:33 на таске 01", "lifetime": "до границы волны" },
    "R-B": { "axes": ["craft"],           "handle": "spawned 2026-09-11T03:33 на таске 01", "lifetime": "весь прогон" },
    "R-C": { "axes": ["data","prereg"],   "handle": "spawned 2026-09-11T03:33 на таске 01", "lifetime": "весь прогон, освежать нельзя" }
  },

  "coverage": { "findings": 22, "acted": 22, "note": "G2 дважды: 17 находок на первую редакцию спеки, 5 на вторую (окно repeat_count → D01; семантика теста 3; две кривые PnL; число 30 суток; родитель A01 → R81). Все внесены" },

  "concerns": [
    { "ticket": "01", "file": "src/commands/lob/pick.rs", "what": "2665 строк при потолке 900 — отбор пула, его тесты и сетевая оболочка в одном файле; разрез отложен в таск 08, который владеет pick.rs", "kind": "structural" },
    { "ticket": "01", "file": "—", "what": "исполнитель сделал 281 вызов инструментов за 70 минут без handoff при потолке ~50 — нарезка таска 01 слишком крупная: вычистка и разрез стоило резать на два таска", "kind": "plan-cut" }
  ],

  "debt": [
    { "what": "CLUSDT: baseCoin CL, признака в API нет; CL — тикер нефти WTI", "default": "исключить до подтверждения", "owner": "владелец", "ticket": "08" },
    { "what": "G_min: задача 7, код 12", "default": "7, с печатью df-цены и джекнайфа", "owner": "владелец", "ticket": "01" }
  ],

  "additions": [
    { "id": "A01", "what": "планка после дефляции считается до сбора; красный — сбор не начинается", "parent": "R47 + R68" },
    { "id": "A03", "what": "джекнайф-по-суткам рядом с вердиктом", "parent": "R68 (в брифе не просилось)" },
    { "id": "A04", "what": "бюджет реакции по этапам, не только весь путь", "parent": "R79" }
  ],

  "recon": {
    "at": "2026-09-10T21:30:00Z", "instruments": ["SOLUSDT","NEARUSDT","ZECUSDT"], "minutes": 5,
    "verify_ok": "6/6", "levels_with_default_warmup": 0,
    "levels_per_min_floor1": { "SOLUSDT": 106, "NEARUSDT": 136, "ZECUSDT": 2925 },
    "eaten_share": { "SOLUSDT": 0.023, "NEARUSDT": 0.071, "ZECUSDT": 0.015 },
    "levels_at_p99_per_5min": { "SOLUSDT": 5, "NEARUSDT": 6, "ZECUSDT": 145 }, "eaten_at_p99": 0,
    "m_10s_bps_at_p99": { "SOLUSDT": -0.60, "NEARUSDT": -2.65, "ZECUSDT": 1.32 },
    "sharpe_m_10s": { "SOLUSDT": -0.40, "NEARUSDT": -0.32, "ZECUSDT": 0.24 }, "required_sharpe": 2.7
  },

  "blind": null,
  "tests": { "passed": 448, "failed": 0, "ignored": 5, "at": "2026-09-11T00:40:00+04:00" }
}
