"""TK-086 (4): замер «после» — окно 24 ч от включения (08.10 04:01 GMT+4) -> docs/findings/ceo-triage-measure-2026-10-09.json.
Запускается одноразовым заданием Планировщика на 09.10 04:05."""
import json, subprocess, sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
py = sys.executable
dd = Path(sys.argv[1])  # каталог плагина .claude/dispatcher
def run(*a):
    return json.loads(subprocess.run([py, *map(str, a)], capture_output=True, text=True, encoding="utf-8", cwd=root).stdout)
out = {"window": "2026-10-08T04:01+04:00 .. +24h",
       "interactive_ceo": run(root / "tools/tk086/baseline.py", "2026-10-08T04:01", "24"),
       "triage_report": run(dd / "ceo_triage.py", "--project", root, "--report")}
(root / "docs/findings/ceo-triage-measure-2026-10-09.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
