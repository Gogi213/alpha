"""Сторож контекста треда (владелец 13.11): при контексте ≥ 350 тыс. токенов — предупреждение треду и владельцу.

Контекст = input + cache_read + cache_creation последнего ответа модели в транскрипте.
UserPromptSubmit — всегда, если порог пройден; PostToolUse — только при переходе через очередную ступень 25 тыс.
(чтобы дочерние треды, которые работают без запросов владельца, тоже получили сигнал, но не на каждый вызов).
"""
import json
import os
import sys
import tempfile

LIMIT = 350_000
STEP = 25_000


def context_tokens(path):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - 400_000))
        tail = f.read().decode("utf-8", "ignore").splitlines()
    for line in reversed(tail):
        if '"usage"' not in line:
            continue
        try:
            u = json.loads(line)["message"]["usage"]
        except (ValueError, KeyError, TypeError):
            continue
        return u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
    return 0


def main():
    data = json.load(sys.stdin)
    path, event = data.get("transcript_path"), data.get("hook_event_name")
    if not path or not os.path.exists(path):
        return
    n = context_tokens(path)
    if n < LIMIT:
        return
    if event == "PostToolUse":
        mark = os.path.join(tempfile.gettempdir(), "ctxguard-" + str(data.get("session_id")))
        step = n // STEP
        try:
            last = int(open(mark).read())
        except (OSError, ValueError):
            last = 0
        if step <= last:
            return
        open(mark, "w").write(str(step))
    msg = (f"Context {n // 1000}k ≥ {LIMIT // 1000}k tokens. Stop starting new work: finish the current step, "
           "write the handoff (state, open items, next step) to disk, report, and ask for /clear.")
    print(json.dumps({"systemMessage": f"Контекст треда {n // 1000} тыс. токенов ≥ 350 тыс. — пора готовить клир.",
                      "hookSpecificOutput": {"hookEventName": event, "additionalContext": msg}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
