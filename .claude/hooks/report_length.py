"""В-164 (владелец 20.11: «и это ОБЯЗАТЕЛЬНО а не советы»): итоговое сообщение дочернего треда ≤ 8 непустых строк,
больше — только со строкой «Reason: …». Stop-хук: длинный ответ без обоснования возвращается на сокращение (один раз).
Координатор (основной checkout, не worktree) не проверяется."""
import json
import os
import sys

LIMIT = 8


def last_text(path):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - 400_000))
        lines = f.read().decode("utf-8", "ignore").splitlines()
    for line in reversed(lines):
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("type") != "assistant":
            continue
        parts = [c.get("text", "") for c in m.get("message", {}).get("content", []) if c.get("type") == "text"]
        if parts:
            return "\n".join(parts)
    return ""


def main():
    data = json.load(sys.stdin)
    if data.get("stop_hook_active"):
        return
    cwd = (data.get("cwd") or os.getcwd()).replace("\\", "/")
    if "/worktrees/" not in cwd:
        return
    path = data.get("transcript_path")
    if not path or not os.path.exists(path):
        return
    text = last_text(path)
    rows = [r for r in text.splitlines() if r.strip()]
    if len(rows) <= LIMIT or any(r.lstrip("-* ").startswith(("Reason:", "Обоснование:")) for r in rows):
        return
    print(json.dumps({"decision": "block", "reason": f"В-164 MANDATORY: final message has {len(rows)} lines > {LIMIT}. "
                      "Rewrite it in ≤ 8 lines (status · answer+numbers · SHA · kb card path · deviations); move details to the kb card. "
                      "Only if more is truly needed, add one line «Reason: …»."}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
