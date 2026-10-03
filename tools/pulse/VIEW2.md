# Табло v2 — контракт данных (`status.json` → раздел `view2`)

Концепт владельца — канвас https://claude.ai/artifact/3N12ZHATKRWT6zdjM3s7L6, артборды «Концепт · Обзор» и
«Концепт · Процесс подробно (enter)». Сборщик (`collect.py`) пишет `view2`; TUI (`board.py`) и страница (`page.html`)
только рисуют его. Пример — `sample-view2.json`.

```jsonc
"view2": {
  "time": "02:30",                       // GMT+4, время сборки
  "headline": {"state": "ok|wait|bad", "text": "ждёт вас: 2"},   // bad = нет связи / диспетчер стоит
  "counters": {"done": 5, "run": 1, "wait": 2, "todo": 4, "bad": 2},   // по шагам всех процессов
  "tags": {                              // метки машин, цвета фиксированы
    "pc":   {"tag": "ПК",   "name": "этот ПК",       "color": "purple"},
    "vps":  {"tag": "VPS",  "name": "VPS София",     "color": "teal"},
    "calc": {"tag": "СЧЁТ", "name": "сервер счёта",  "color": "blue"},
    "col":  {"tag": "КОЛ",  "name": "коллектор",     "color": "gray"},
    "you":  {"tag": "ВЫ",   "name": "вы",            "color": "amber"}
  },
  "machines": [{
    "id": "pc", "state": "ok|down|off",
    "load": "инженер · Умная проверка",  // кто/какая задача на машине (по-людски)
    "now": {"state": "run|wait|idle|bad|off", "text": "ждёт вас"},
    "orphans": 0, "cpu": 10, "mem": 76, "disk_mb_s": null
  }],
  "processes": [{                        // активные сверху, готовые — в конце (свёрнуты)
    "id": "TK-044", "n": 2, "title": "Умная проверка данных",
    "flow": [{"text": "код пишет", "on": "pc"}, {"text": "запускает и проверяет", "on": "vps"}],
    "for": {"text": "данные дней", "on": "vps"},
    "state": "run|wait|bad|done|todo",
    "step_now": 3, "steps_total": 5, "eta_min": null, "wait_min": 6,
    "steps": [{
      "n": 1, "title": "правила проверки", "who": "инженер|автомат|вы", "on": "pc",
      "for": "код для VPS", "state": "done|run|wait|todo|bad",
      "pct": null, "detail": null, "eta_min": null,       // detail: «231 из 492 монето-месяцев»
      "started": "02:10", "finished": "02:16", "question": null   // question: id вопроса для шага «вы»
    }]
  }],
  "questions": [{
    "id": "q-TK-044-1", "n": 1, "process": "TK-044", "from": "инженер", "on": "pc",
    "text": "Признак «цены не было»: только число или браковать сутки?",
    "options": [{"key": "a", "label": "только число", "effect": "запустит «прогон» на VPS"},
                {"key": "b", "label": "браковать сутки", "effect": "запустит «прогон» на VPS"}],
    "since": "02:24", "wait_min": 6
  }],
  "feed": [{"time": "02:16", "state": "done|run|wait|bad", "on": ["pc"], "to": null, "text": "правила проверки"}]
}
```

Источники: план шагов — `.claude/pulse/plans/<ID>.json` (пишет исполнитель через `tools/pulse/plan.py`);
вопросы — `.claude/pulse/questions/<id>.json` (`tools/pulse/ask.py`); ход шагов на серверах — `/data/progress/*.json`
(поле `step_n` — номер шага плана); машины, процессы без хозяина — сборщик; тексты по-людски — Haiku (`plainify.py`).
Ответ владельца (клавиша в TUI или кнопка на странице) — `ask.py answer <id> <key>` / MCP `pulse_answer`.

Уточнения сборщика (03.10, `view2.py`): `for.text` может быть пустым (плана нет); `feed` — по возрастанию времени, свежие
внизу, ≤ 8 (Haiku-строки v1 + завершённые шаги плана + вопросы/ответы); у шага без плана — один шаг из текущего состояния;
у вопроса процесс может быть не тикетом (`orphans-<машина>`); `pulse_answer {id, key}` — ответ владельца (MCP).

## Веб-табло (04.10): то же табло из браузера и с телефона

Сервер счёта `89.163.242.211`, юнит `alpha-board` (CPUQuota 20 %, Nice 10, MemoryMax 200M), порт 8787, адрес с секретом
`http://89.163.242.211:8787/<токен>/` (токен — `/etc/alpha-board/token`; у CEO — `.claude/pulse/board-url.txt`, gitignored).
Код — `tools/pulse/web/` (`server.py` — HTTP на stdlib, `bridge.py` — приём сводки и выдача ответов, `board.html` — страница
из `page2.html`, `alpha-board.service`); на сервере — `/opt/alpha-board/` (код) и `/data/board/` (`status.json`, `answers.jsonl`).
Маршруты под токеном: `GET /` страница, `GET /status.json` (`view2` + `built_at` + `age_s`), `POST /answer {id,key}` (вопрос и
вариант есть в текущей сводке; ≤ 30 ответов в час; один ответ на вопрос); остальное — 404. Доставка: `BoardLink` в
`collect.py` держит ОДНО ssh к счёту (`bridge.py`): сводка строкой JSON раз в 5 с в stdin, ответы возвращаются в stdout и
исполняются `ask.py answer <id> <key>`; смещение обработанного — `.claude/pulse/board-offset.txt`, журнал — `board-link.log`.
Деплой: `tar` файлов `tools/pulse/web` в `/opt/alpha-board`, `systemctl restart alpha-board`; сборщик — файл `.claude/pulse/stop`.
