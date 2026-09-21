#!/usr/bin/env python3
"""TypeSafe System One — тонкий клиент для мета-контура alpha (В-81, владелец 21.09).

Типизированные суждения над текстом состояния: `noul` (вероятность «да»), `choice`
(один из вариантов + распределение), `score` (позиция на шкале). Горячего пути не
касается — только предрегистрация, чтение ночи, сверка план↔лог↔SETTLED, ревью.

Ключ — **только** из окружения `TYPESAFE_API_KEY` (правило проекта: секреты — имена
переменных, значения не печатаются и не пишутся). Без ключа — `MissingKey`, не тихий пропуск.

Поля вопроса — `instructions` и `criteria` (у `choice` — карта «вариант → описание»), не
`question`/`options`: с последними API отдаёт 422 (грабля 21.09). Батч вопросов в одном
запросе в ~4 раза дешевле по токенам и в 6 раз быстрее раздельных (замер DSH 21.09).

    from judge import Judge
    j = Judge()
    a = j.ask("набор a45-bid:age=2700,flow=100", {
        "both_floors": j.noul("Объединяет ли набор возраст И силу?"),
        "family": j.choice("Какая семья флоров?", {"age": "…", "flow": "…", "both": "…"}),
    })
    a["both_floors"]["noul"], a["family"]["choice"]
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
ENV_KEY = "TYPESAFE_API_KEY"


class MissingKey(RuntimeError):
    pass


class Judge:
    def __init__(self, model: str = MODEL, timeout_s: float = 20.0, retries: int = 3):
        key = os.environ.get(ENV_KEY, "")
        if not key:
            raise MissingKey(f"{ENV_KEY} не задан в окружении — суждения TypeSafe недоступны")
        self._key = key
        self.model = model
        self.timeout_s = timeout_s
        self.retries = retries
        self.usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0}

    # --- конструкторы вопросов -------------------------------------------------
    @staticmethod
    def noul(instructions: str) -> dict:
        return {"type": "noul", "instructions": instructions}

    @staticmethod
    def choice(instructions: str, criteria: dict[str, str]) -> dict:
        return {"type": "choice", "instructions": instructions, "criteria": criteria}

    @staticmethod
    def score(instructions: str, criteria: dict[str, str]) -> dict:
        return {"type": "score", "instructions": instructions, "criteria": criteria}

    # --- вызов ----------------------------------------------------------------
    def ask(self, state: str, questions: dict[str, dict]) -> dict[str, dict]:
        body = json.dumps(
            {"model": self.model, "state": state, "questions": questions}, ensure_ascii=False
        ).encode("utf-8")
        req = urllib.request.Request(
            API_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
            },
        )
        delay = 1.0
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                u = data.get("usage", {})
                self.usage["input_tokens"] += int(u.get("input_tokens", 0))
                self.usage["output_tokens"] += int(u.get("output_tokens", 0))
                self.usage["requests"] += 1
                return data["answers"]
            except urllib.error.HTTPError as e:
                text = e.read().decode("utf-8", "replace")[:400]
                if e.code in (429, 529) and attempt < self.retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise RuntimeError(f"TypeSafe HTTP {e.code}: {text}") from None
            except urllib.error.URLError as e:
                if attempt < self.retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise RuntimeError(f"TypeSafe недоступен: {e.reason}") from None
        raise RuntimeError("TypeSafe: попытки исчерпаны")


if __name__ == "__main__":
    # Самопроверка без данных проекта: ключ, эндпоинт, форма ответа.
    j = Judge()
    ans = j.ask(
        "ping",
        {"alive": Judge.noul("Is the state exactly the word 'ping'?")},
    )
    print(json.dumps({"alive": ans["alive"], "usage": j.usage}, ensure_ascii=False))
    sys.exit(0 if ans["alive"]["noul"] > 0.5 else 1)
