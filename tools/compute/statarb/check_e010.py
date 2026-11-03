#!/usr/bin/env python3
"""E-010: ставки фандинга на эпохе минут панелей. Проверка для errors.py (удалённо на calc, rc = код проверки):
  check_e010.py [дерево]   vps-check.sh <дерево, по умолчанию корень> test funding — среди тестов
                           funding_minutes_share_the_epoch_of_panels и funding_from_exchange_files_lands_in_panel_minutes.
Красная на 75d318ab (unix-эпоха ставок в загрузчике), зелёная на a62ef6eb и позже.
E-011: слот сборки на calc общий, tar хранит mtime файлов, а cargo считает крейт свежим, если исходники не новее
прежней сборки в слоте — после дерева 75d318ab исправленный cli.rs (mtime старше) не пересобирался, тест шёл на
чужом бинарнике. Перед отправкой исходникам ставится mtime в будущем (+30 мин, запас на расхождение часов): крейт
пересобирается всегда, результат зависит только от содержимого дерева."""
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
GIT_BASH = os.environ.get("GIT_BASH", "C:/Program Files/Git/bin/bash.exe")
FUTURE_S = 1800


def bust_cargo_cache(tree: str) -> int:
    """mtime исходников крейта = сейчас + FUTURE_S; -> сколько файлов тронуто."""
    t = time.time() + FUTURE_S
    root = Path(tree)
    files = [*root.joinpath("src").rglob("*.rs"), *(p for p in (root / "build.rs", root / "Cargo.toml") if p.exists())]
    for p in files:
        os.utime(p, (t, t))
    return len(files)


def main(tree: str) -> int:
    n = bust_cargo_cache(tree)
    print(f"check_e010: {tree}: mtime +{FUTURE_S} с у {n} файлов, vps-check.sh test funding", flush=True)
    vps_check = os.path.join(REPO, "tools", "vps-check.sh").replace("\\", "/")
    return subprocess.run([GIT_BASH, vps_check, tree.replace("\\", "/"), "test", "funding"], cwd=tree).returncode


if __name__ == "__main__":
    sys.exit(main(os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else REPO))
