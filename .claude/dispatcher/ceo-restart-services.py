import os, sys, subprocess
from pathlib import Path
code = Path(r"C:\Users\Георгий\.claude\plugins\cache\role-play-vibing\role-play-vibing\1.8.21")
sys.path.insert(0, str(code / ".claude" / "dispatcher"))
os.environ["RPV_CI_REPO"] = "Gogi213/role-play-vibing"
os.environ.pop("RPV_CEO_TRIAGE", None)
os.environ["RPV_DISPATCH_ROLE_PARALLEL"] = "engineer:6,researcher:4,judge:4"
import release
env = release.clean_env()
args = sys.argv[1:]
r = subprocess.run([sys.executable, str(code / ".claude" / "dispatcher" / args[0]), *args[1:]], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=r"C:\visual projects\alpha")
print("rc", r.returncode); print(r.stdout[-3000:]); print(r.stderr[-3000:])
