#!/usr/bin/env python3
"""TypeSafe System One — тонкий клиент для мета-контура alpha (В-81, владелец 21.09).

Типизированные суждения над текстом состояния: `noul` (вероятность «да»), `choice`
(один из вариантов + распределение), `score` (позиция на шкале). Горячего пути не
касается — только предрегистрация, чтение ночи, сверка план↔лог↔SETTLED, ревью.

Ключ — **только** из окружения `TYPESAFE_API_KEY` (правило проекта: секреты — имена
переменных, значения не печатаются и не пишутся). Без ключа — `MissingKey`, не тихий пропуск.

Поля вопроса — `instructions` и `criteria`: у `choice` — карта «вариант → описание», у `score` —
**список** уровней от низшего к высшему (карта даёт 422 — проверено живьём 22.09, аудит дизайна §1);
не `question`/`options` (тоже 422, грабля 21.09). Батч вопросов в одном запросе в ~4 раза дешевле по
токенам и в 6 раз быстрее раздельных (замер DSH 21.09).

Граница применения (аудит 22.09, `docs/findings/design-audit-2026-09-22.md` §1): правила, числа и
статусы, которые код может вычислить, вычисляет **код** и решает по ним сам (жёсткий отказ, без
ключа тоже). Модель — только там, где нужна семантика свободного текста. Ответ модели — **мнение**:
печатать с пометкой «мнение модели» и версией (`Judge.model_version`), не рядом с метриками как
измерение; `confidence` у `choice`/`score` — концентрация распределения, а не вероятность верности.

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
        # Версия, которая реально ответила (`jev-latest` меняется со временем): пишется в
        # вывод судей, чтобы суждение было воспроизводимо.
        self.model_version: str | None = None

    # --- конструкторы вопросов -------------------------------------------------
    @staticmethod
    def noul(instructions: str) -> dict:
        return {"type": "noul", "instructions": instructions}

    @staticmethod
    def choice(instructions: str, criteria: dict[str, str]) -> dict:
        return {"type": "choice", "instructions": instructions, "criteria": criteria}

    @staticmethod
    def score(instructions: str, levels: list[str]) -> dict:
        """Уровни — список от низшего к высшему (2–10); `score` в ответе — средневзвешенный номер уровня."""
        if not isinstance(levels, list) or not 2 <= len(levels) <= 10:
            raise ValueError("score: уровни — список из 2–10 описаний от низшего к высшему")
        return {"type": "score", "instructions": instructions, "criteria": levels}

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
                self.model_version = data.get("model", self.model_version)
                self.usage["model"] = self.model_version
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
    # Даже тривиальный факт модель даёт не 1.0 (живой замер 22.09: 0.73) — самопроверка
    # проверяет связь и форму ответа, а не «ум»; порог 0.5 здесь только «ответ не абсурден».
    sys.exit(0 if ans["alive"]["noul"] > 0.5 else 1)
