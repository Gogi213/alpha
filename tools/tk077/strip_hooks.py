"""Убрать из settings.json хуки, ведущие в <проект>/.claude/hooks/ (их даёт плагин). --dry-run: только напечатать."""
import json
import sys

path = sys.argv[1]
dry = "--dry-run" in sys.argv
d = json.load(open(path, encoding="utf-8"))
hooks = d.get("hooks", {})
removed = []
for ev in list(hooks):
    keep = []
    for group in hooks[ev]:
        if any("/.claude/hooks/" in h.get("command", "") for h in group.get("hooks", [])):
            removed.append(ev)
        else:
            keep.append(group)
    if keep:
        hooks[ev] = keep
    else:
        del hooks[ev]
if not hooks:
    d.pop("hooks", None)
if dry:
    print("DRY: убрал бы хуки событий:", ",".join(removed) or "-", "| останется:", ",".join(d.get("hooks", {})) or "-")
else:
    json.dump(d, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print("убраны хуки событий:", ",".join(removed) or "-")
