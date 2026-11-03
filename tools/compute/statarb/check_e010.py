#!/usr/bin/env python3
"""E-010: ставки фандинга на эпохе минут панелей. Проверка для errors.py (удалённо на calc, rc = код проверки):
  check_e010.py       vps-check.sh <корень дерева> test funding — среди тестов
                      funding_minutes_share_the_epoch_of_panels и funding_from_exchange_files_lands_in_panel_minutes.
Красная на 75d318ab (unix-эпоха ставок в загрузчике), зелёная на a62ef6eb и позже."""
import os
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
GIT_BASH = os.environ.get("GIT_BASH", "C:/Program Files/Git/bin/bash.exe")

if __name__ == "__main__":
    sys.exit(subprocess.run([GIT_BASH, "tools/vps-check.sh", ".", "test", "funding"], cwd=REPO).returncode)
