import json, subprocess, sys
from pathlib import Path
# плагин 2.0.x: версия — установленная (installed_plugins.json); настройки — .claude/rpv.json, окружение не нужно.
# Использование: python .claude/dispatcher/ceo-restart-services.py [start.py|supervise.py --install|doctor.py] (по умолчанию start.py)
PROJECT = r"C:\visual projects\alpha"
_inst = json.loads((Path.home() / ".claude" / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
code = Path(next(e["installPath"] for k, v in _inst["plugins"].items() if k.startswith("role-play-vibing") for e in v
                 if e.get("projectPath", "").lower() == PROJECT.lower()))
args = sys.argv[1:] or ["start.py"]
r = subprocess.run([sys.executable, str(code / ".claude" / "dispatcher" / args[0]), "--project", PROJECT, *args[1:]],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=PROJECT)
print("rc", r.returncode); print(r.stdout[-3000:]); print(r.stderr[-3000:])
