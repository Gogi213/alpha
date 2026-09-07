window.STATE =
{
  "slug": "lob-density",
  "dir": "2026-09-08-lob-density--wip",
  "title": "Плотности в стакане: есть ли предсказательная сила у исхода уровня",
  "mode": "semi",
  "depth": "deep",
  "polish": null,
  "tier": "T3",
  "briefFile": "lob-density-signal.md",
  "memoryFile": "CLAUDE.md",
  "skillDir": "/c/Users/Георгий/.claude/skills/autopilot",
  "startedAt": "2026-09-08T01:40:00+04:00",
  "updatedAt": "2026-09-08T02:58:00+04:00",
  "finishedAt": null,
  "stages": [
    { "id": "preflight", "status": "done", "startedAt": "2026-09-08T01:40:00+04:00", "finishedAt": "2026-09-08T02:05:00+04:00", "note": "репозиторий обнулён, скелет компилируется, rustc 1.93.1" },
    { "id": "manifest",  "status": "done", "startedAt": "2026-09-08T02:05:00+04:00", "finishedAt": "2026-09-08T02:10:00+04:00", "note": "бриф разобран на 10 требований" },
    { "id": "briefing",  "status": "done", "startedAt": "2026-09-08T02:10:00+04:00", "finishedAt": "2026-09-08T02:20:00+04:00", "note": "9 вопросов в OPEN_QUESTIONS.md, ответы батчем в конце" },
    { "id": "spec",      "status": "done", "startedAt": "2026-09-08T02:12:00+04:00", "finishedAt": "2026-09-08T02:22:00+04:00", "note": "13 решений, гейты G0-G4 и GC, предрегистрация" },
    { "id": "plan",      "status": "active", "startedAt": "2026-09-08T02:22:00+04:00", "note": "ревизия 2: закрыты 11 блокеров за два прохода, порядок шагов переставлен" },
    { "id": "build",     "status": "pending" },
    { "id": "review",    "status": "pending" },
    { "id": "final",     "status": "pending" }
  ],
  "requirements": {
    "total": 10, "done": 0, "inTicket": 10, "inSpec": 0,
    "placeholder": 0, "deferred": 0, "dropped": 0
  },
  "tickets": [
    { "id": "0.0", "title": "Пин тулчейна: rust-toolchain.toml 1.93.1, rust-version 1.90", "requirements": ["R04"], "blockedBy": [], "wave": 1, "zone": ["Cargo.toml"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "0.1", "title": "Bybit WS: книга из snapshot+delta, u=1, size=0, cts", "requirements": ["R06"], "blockedBy": ["0.0"], "wave": 2, "zone": ["src/bybit/ws.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "7.2", "title": "src/stats: wild cluster bootstrap-t, DSR, PBO, CPCV", "requirements": ["R05"], "blockedBy": ["0.0"], "wave": 2, "zone": ["src/stats/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "6.2", "title": "lob probe: RTT полного цикла, HMAC-SHA256, ключи из окружения", "requirements": ["R04", "R08"], "blockedBy": ["0.0"], "wave": 2, "zone": ["src/bybit/probe.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "0.2", "title": "Бинарный лог: сутки самодостаточны, кадры с u32 длины", "requirements": ["R06", "R08"], "blockedBy": ["0.1"], "wave": 3, "zone": ["src/bybit/binlog.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "0.4", "title": "lob pick: отбор по времени-взвешенной глубине, два кандидата", "requirements": ["R07"], "blockedBy": ["0.1"], "wave": 3, "zone": ["src/commands/lob.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "0.3", "title": "lob record: ротация по суткам UTC, gaps.csv", "requirements": ["R06"], "blockedBy": ["0.2"], "wave": 4, "zone": ["src/commands/lob.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "1.1", "title": "lob levels: рождение, смерть, пересоздание, прогрев окна", "requirements": ["R01", "R10"], "blockedBy": ["0.2"], "wave": 4, "zone": ["src/lob/levels.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "1.2", "title": "Классификация eaten/pulled/mixed, фильтр BT и стороны агрессора", "requirements": ["R01"], "blockedBy": ["1.1"], "wave": 5, "zone": ["src/lob/levels.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "4.1", "title": "lob verify: сверка с REST по u, инварианты, трейды внутри книги", "requirements": ["R06"], "blockedBy": ["0.3"], "wave": 5, "zone": ["src/bybit/verify.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "2.1", "title": "lob markout: база до исчезновения уровня", "requirements": ["R02"], "blockedBy": ["1.2"], "wave": 6, "zone": ["src/lob/markout.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "3.1", "title": "Пилот 2 часа по двум кандидатам, гейт G0 — до недели записи", "requirements": ["R03"], "blockedBy": ["2.1", "0.3", "0.4"], "wave": 7, "zone": ["docs/findings/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "4.2", "title": "Недельная запись, 7 суток чистого покрытия", "requirements": ["R06"], "blockedBy": ["4.1", "3.1"], "wave": 8, "zone": ["data/bybit/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "7.1", "title": "runs.csv: журнал прогонов, ведётся с пилота", "requirements": ["R05"], "blockedBy": ["3.1"], "wave": 8, "zone": ["docs/plan/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "5.1", "title": "Разметка недели, гейт G1", "requirements": ["R01"], "blockedBy": ["4.2", "1.2"], "wave": 9, "zone": ["docs/findings/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "6.1", "title": "lob export: 8 полей hftbacktest, exch_ts < local_ts", "requirements": ["R04"], "blockedBy": ["0.2", "4.2"], "wave": 9, "zone": ["src/lob/export.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "5.2", "title": "Первичная ячейка + bootstrap-t, гейт G2", "requirements": ["R02"], "blockedBy": ["5.1", "2.1", "7.2"], "wave": 10, "zone": ["src/lob/markout.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "5.3", "title": "Издержки на первичную ячейку, гейт G3", "requirements": ["R03"], "blockedBy": ["5.2"], "wave": 11, "zone": ["docs/findings/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "6.3", "title": "hftbacktest с очередью и измеренным RTT, гейт G4", "requirements": ["R04"], "blockedBy": ["5.3", "6.1", "6.2"], "wave": 12, "zone": ["src/lob/backtest.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "7.3", "title": "DSR, PBO, CPCV на итоговом прогоне", "requirements": ["R05"], "blockedBy": ["6.3", "7.1", "7.2"], "wave": 13, "zone": ["docs/findings/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 }
  ],
  "singlePass": null,
  "tests": null,
  "debt": {
    "placeholders": [],
    "assumptions": [
      "H1 данные собираются самостоятельно, 7 суток",
      "H2 инструмент назначается в шаге 0.4",
      "H3 порог крупного уровня: 99-й перцентиль по времени-взвешенной выборке, прогрев 60 минут",
      "H4 комиссии 0.02 / 0.055, круговая мейкер-мейкер 4 bps",
      "H5 объём записи 5-15 ГБ за неделю",
      "H6 размер ордера 200 долларов, порог зелёного 3x издержек и не менее 10 долларов",
      "H7 правило отбора инструмента: глубина, при равенстве частота событий",
      "H8 разрыв или провал verify выбрасывает сутки целиком, потолок 10 суток",
      "H9 замер RTT на живом счёте, post-only, ключи из переменных окружения",
      "H10 пилот идёт по двум кандидатам, G0 красный только если оба",
      "H11 недобор выборки продлевает запись до потолка один раз, потом красный"
    ],
    "emptyEnv": ["BYBIT_API_KEY", "BYBIT_API_SECRET"]
  },
  "additions": [],
  "coverage": null,
  "concerns": [
    "Bybit не даёт order-level: единица анализа уровень-кластер, пер-ордерная атрибуция невозможна",
    "20 мс это каденция снапшота, не поток событий: add+cancel внутри окна не наблюдаем",
    "data/bybit не восстановим, поэтому пилот и verify стоят до недельной записи",
    "7 суток дают 7 кластеров: обычная робастная дисперсия смещена, нужен wild cluster bootstrap-t",
    "гейт GC на каждом шаге с кодом: аллокации, байт на событие, CPU, RSS, clippy"
  ],
  "reviewers": { "manifestSpec": null, "craft": null },
  "blind": null
}
