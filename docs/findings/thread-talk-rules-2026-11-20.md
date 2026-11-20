# Как сократили болтовню тредов — 20.11.2026

Что сделано в alpha (bb + Claude Code), чтобы координатор и дочерние треды писали меньше без потери смысла. Можно перенести на Mac один в один.

## 1. Решения владельца (карточки `kb/decision/`)

- **В-163** — «слушай мне кажется ты очень много пишешь. мы можем это оптимизировать как то без урона смыслам? приказом». Координатор пишет владельцу только при результате, нужном решении, сдвиге срока или аварии. Промежуточные события («идёт», «критик PASS», технический FIX) не пересказывает, максимум одной строкой. Ответ не длиннее 5 строк, таблица — только для результата, без повторов и без хвоста про /clear.
- **В-164** — «я хочу как то сократить вывод исполнителей…», «меньше 8 строк или больше но обоснованно», «и это ОБЯЗАТЕЛЬНО а не советы». Итоговый отчёт исполнителя или проверяющего — не длиннее 8 строк: статус · ответ и ключевые числа · ветка + SHA · путь к карточке · отступления. Больше 8 строк — только со строкой «Reason: …». Подробности идут в карточку задачи. Ход, в котором тред только ждёт, — одна строка «waiting: …» или ничего.
- **Событие без новостей** (20.11): на сообщение треда, в котором для владельца ничего нового, координатор отвечает одним номером задачи, например «TK-638».

## 2. Где записано

| Файл | Что там |
|---|---|
| `AGENTS.md`, начало | Дочерние треды: только английский, без болтовни; отчёт ≤ 8 строк или «Reason: …»; промежуточные этапы не комментировать; ожидание — «waiting: <что>»; не пересказывать бриф, вердикты, прошлые отчёты |
| `.claude/preferences.md` п. 4 | Ответ владельцу: результат и число первыми, ≤ 5 строк, только результат / решение / срок / авария; событие без новостей — один номер задачи |
| `.claude/preferences.md` п. 33 | Ожидание долгой работы — фоном (`tools/srv-wait.sh`); сообщения родителю «ещё жду / в очереди / ещё идёт» запрещены, только итог READY / BLOCKED |
| `.claude/preferences.md` п. 34 | Вывод команд — через tail/grep/head; длинные логи — помощнику-читателю, в контекст только сводка; не пересказывать бриф и свои шаги |
| `.claude/hooks/report_length.py` + `.claude/settings.json` (Stop) | Принудительная проверка длины отчёта (ниже) |

## 3. Хук, который заставляет укладываться в 8 строк

Stop-хук Claude Code. Срабатывает только в дочерних тредах (рабочая папка содержит `/worktrees/`), координатора не трогает. Если итоговое сообщение длиннее 8 непустых строк и в нём нет строки «Reason: …», хук один раз возвращает тред переписать отчёт.

`.claude/settings.json`:

```json
"hooks": {
  "Stop": [
    { "hooks": [ { "type": "command",
      "command": "python \"$CLAUDE_PROJECT_DIR/.claude/hooks/report_length.py\"" } ] }
  ]
}
```

`.claude/hooks/report_length.py`:

```python
import json, os, sys

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
    rows = [r for r in last_text(path).splitlines() if r.strip()]
    if len(rows) <= LIMIT or any(r.lstrip("-* ").startswith(("Reason:", "Обоснование:")) for r in rows):
        return
    print(json.dumps({"decision": "block", "reason": f"В-164 MANDATORY: final message has {len(rows)} lines > {LIMIT}. "
                      "Rewrite it in ≤ 8 lines (status · answer+numbers · SHA · kb card path · deviations); "
                      "move details to the kb card. Only if more is truly needed, add one line «Reason: …»."}))

if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
```

На Mac: `python` → `python3`, если `python` там нет.

## 4. Как координатор работает с тредами

- Тред запускается только под текущую задачу владельца, всегда с `--parent-self` (иначе итог не придёт координатору). Название — `<роль> · TK-<NNN> · <тема> · v<N>`.
- В бриф — слова владельца дословно плюс нужный контекст, своих разделов не добавлять.
- Новая версия треда в той же папке → старую сразу `bb thread stop`.
- Исполнитель сам проверяющего не запускает: пишет «готово к вердикту», ветку и SHA и останавливается.
- Ответ треду — `bb thread tell`, одно сообщение за раз, без подтверждений и благодарностей.
- Ждать — только фоном; опрашивать сервер в цикле (ssh + sleep) запрещено.

## 5. Коммиты (master)

`6a7cc4834` В-163 · `7eedde15e` В-164 · `c5c7b84e0` больше 8 строк — только с обоснованием · `abc094444` Stop-хук `report_length.py` · `41a794956` событие без новостей — номер задачи.
