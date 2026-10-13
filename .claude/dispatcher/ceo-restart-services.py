import json, os, sys, subprocess
from pathlib import Path
# версия — установленная (installed_plugins.json), env служб — из .claude/settings.json проекта (бабочки, лимиты, стражи)
_inst = json.loads((Path.home() / ".claude" / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
code = Path(next(e["installPath"] for k, v in _inst["plugins"].items() if k.startswith("role-play-vibing") for e in v
                 if e.get("projectPath", "").lower() == r"c:\visual projects\alpha"))
sys.path.insert(0, str(code / ".claude" / "dispatcher"))
_settings = json.loads(Path(r"C:\visual projects\alpha\.claude\settings.json").read_text(encoding="utf-8"))
os.environ.update({k: str(v) for k, v in _settings.get("env", {}).items() if k.startswith("RPV_")})
os.environ["RPV_CI_REPO"] = "Gogi213/role-play-vibing"
os.environ.pop("RPV_CEO_TRIAGE", None)
import release
env = release.clean_env()
args = sys.argv[1:]
r = subprocess.run([sys.executable, str(code / ".claude" / "dispatcher" / args[0]), *args[1:]], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=r"C:\visual projects\alpha")
print("rc", r.returncode); print(r.stdout[-3000:]); print(r.stderr[-3000:])
