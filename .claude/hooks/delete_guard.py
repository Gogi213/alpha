"""Прослойка для ~/.claude/hooks/alpha_one_build.py: он импортирует `delete_guard` из этой папки. Сам страж — в плагине."""
import importlib.util
import json
import sys
from pathlib import Path

_installed = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
_path = max(
    (Path(e["installPath"]) / ".claude" / "hooks" / "delete_guard.py"
     for k, v in json.loads(_installed.read_text(encoding="utf-8")).get("plugins", {}).items()
     if k.startswith("role-play-vibing") for e in v),
    key=lambda p: p.stat().st_mtime)
sys.path.insert(0, str(_path.parent))
_spec = importlib.util.spec_from_file_location("rpv_delete_guard", _path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
check_tool = _mod.check_tool
