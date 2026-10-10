import os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(HERE, ".."))
import importlib.util
spec = importlib.util.spec_from_file_location("analyze_main", os.path.join(HERE, "..", "__main__.py"))
am = importlib.util.module_from_spec(spec); spec.loader.exec_module(am)


def test_every_subcommand_script_exists():
    for k, (rel, _) in am.SUBS.items():
        assert os.path.isfile(os.path.normpath(os.path.join(am.HERE, rel))), k


def test_help_lists_and_unknown_fails():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "analyze"), "--help"], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0 and "monthly" in r.stdout
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "analyze"), "nope"], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 2
