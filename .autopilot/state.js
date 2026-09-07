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
  "updatedAt": "2026-09-08T02:24:00+04:00",
  "finishedAt": null,
  "stages": [
    { "id": "preflight", "status": "done", "startedAt": "2026-09-08T01:40:00+04:00", "finishedAt": "2026-09-08T02:05:00+04:00", "note": "репозиторий обнулён, скелет компилируется" },
    { "id": "manifest",  "status": "done", "startedAt": "2026-09-08T02:05:00+04:00", "finishedAt": "2026-09-08T02:10:00+04:00", "note": "бриф разобран на 10 требований" },
    { "id": "briefing",  "status": "done", "startedAt": "2026-09-08T02:10:00+04:00", "finishedAt": "2026-09-08T02:20:00+04:00", "note": "6 вопросов в OPEN_QUESTIONS.md, ответы батчем в конце" },
    { "id": "spec",      "status": "done", "startedAt": "2026-09-08T02:12:00+04:00", "finishedAt": "2026-09-08T02:22:00+04:00", "note": "11 решений, 5 гейтов G0-G4, предрегистрация" },
    { "id": "plan",      "status": "active", "startedAt": "2026-09-08T02:22:00+04:00", "note": "цикл planner-critic, проход 1 закрыт: 6 блокеров settled" },
    { "id": "build",     "status": "pending" },
    { "id": "review",    "status": "pending" },
    { "id": "final",     "status": "pending" }
  ],
  "requirements": {
    "total": 10, "done": 0, "inTicket": 10, "inSpec": 0,
    "placeholder": 0, "deferred": 0, "dropped": 0
  },
  "tickets": [
    { "id": "0.1", "title": "Bybit WS: книга из snapshot+delta, u=1, size=0, cts", "requirements": ["R06"], "blockedBy": [], "wave": 1, "zone": ["src/bybit/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "4.1", "title": "Замер RTT полного цикла с VPS, post-only", "requirements": ["R04", "R08"], "blockedBy": [], "wave": 1, "zone": ["src/bybit/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "0.2", "title": "Бинарный лог: сутки самодостаточны, надмножество схемы hftbacktest", "requirements": ["R06", "R08"], "blockedBy": ["0.1"], "wave": 2, "zone": ["src/bybit/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "0.4", "title": "Отбор инструмента по времени-взвешенной глубине", "requirements": ["R07"], "blockedBy": ["0.1"], "wave": 2, "zone": ["src/commands/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "0.3", "title": "lob record: ротация по суткам UTC, gaps.csv", "requirements": ["R06"], "blockedBy": ["0.2"], "wave": 3, "zone": ["src/commands/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "1.1", "title": "lob levels: рождение, смерть, пересоздание уровня", "requirements": ["R01", "R10"], "blockedBy": ["0.2"], "wave": 3, "zone": ["src/lob/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "0.5", "title": "lob verify: сверка с REST, инварианты, трейды внутри книги", "requirements": ["R06"], "blockedBy": ["0.1", "0.3"], "wave": 4, "zone": ["src/bybit/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "0.0", "title": "Пилотный час и гейт G0: пол по издержкам до недели записи", "requirements": ["R03"], "blockedBy": ["0.1", "0.2", "0.3"], "wave": 4, "zone": ["docs/findings/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "0.6", "title": "Недельная запись, 7 суток чистого покрытия", "requirements": ["R06"], "blockedBy": ["0.4", "0.5", "0.0"], "wave": 5, "zone": ["data/bybit/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "1.2", "title": "Классификация eaten/pulled/mixed, гейт G1", "requirements": ["R01"], "blockedBy": ["1.1", "0.6"], "wave": 6, "zone": ["src/lob/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "4.0", "title": "lob export: конвертация в 8 полей hftbacktest", "requirements": ["R04"], "blockedBy": ["0.2", "0.6"], "wave": 6, "zone": ["src/lob/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "2.1", "title": "lob markout: база до исчезновения уровня", "requirements": ["R02"], "blockedBy": ["1.2"], "wave": 7, "zone": ["src/lob/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "5.1", "title": "runs.csv: журнал всех прогонов", "requirements": ["R05"], "blockedBy": ["1.2"], "wave": 7, "zone": ["docs/plan/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "2.2", "title": "Первичная ячейка + разведочная таблица, гейт G2", "requirements": ["R02"], "blockedBy": ["2.1"], "wave": 8, "zone": ["src/lob/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "3.1", "title": "Издержки на первичную ячейку, гейт G3", "requirements": ["R03"], "blockedBy": ["2.2", "0.0"], "wave": 9, "zone": ["src/lob/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "4.2", "title": "hftbacktest с очередью и измеренным RTT, гейт G4", "requirements": ["R04"], "blockedBy": ["3.1", "4.0", "4.1"], "wave": 10, "zone": ["src/lob/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "5.2", "title": "DSR, PBO, CPCV с purging и embargo", "requirements": ["R05"], "blockedBy": ["4.2", "5.1"], "wave": 11, "zone": ["src/stats/"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 }
  ],
  "singlePass": null,
  "tests": null,
  "debt": {
    "placeholders": [],
    "assumptions": [
      "H1 данные собираются самостоятельно, 7 суток",
      "H2 инструмент назначается в шаге 0.4",
      "H3 порог крупного уровня: 99-й перцентиль по времени-взвешенной выборке",
      "H4 комиссии 0.02 / 0.055, круговая мейкер-мейкер 4 bps",
      "H5 объём записи 5-15 ГБ за неделю",
      "H6 размер ордера 200 долларов, порог зелёного 3x издержек и не менее 10 долларов",
      "H7 рекордер и замер RTT на одном VPS в регионе Bybit",
      "H8 разрыв длиннее 6 часов выбрасывает сутки целиком",
      "H9 замер RTT на живом счёте, post-only, минимальный размер"
    ],
    "emptyEnv": []
  },
  "additions": [],
  "coverage": null,
  "concerns": [
    "Bybit не даёт order-level: единица анализа уровень-кластер, пер-ордерная атрибуция невозможна",
    "20 мс это каденция снапшота, не поток событий: add+cancel внутри окна не наблюдаем",
    "data/bybit не восстановим, поэтому lob verify стоит до недельной записи"
  ],
  "reviewers": { "manifestSpec": null, "craft": null },
  "blind": null
}
