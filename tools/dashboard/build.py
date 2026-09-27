#!/usr/bin/env python3
"""Сборка новой страницы дашборда: шаблон `tools/dashboard/dashboard.html` + данные `data-*.json`
(от `tools/compute/titration-dashboard-merge.py`) → один самодостаточный файл, данные встроены.

    python tools/dashboard/build.py [data/titration-dashboard/data-v34.json] \
        [--out data/titration-dashboard/index-new.html]
"""
import argparse
import glob
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
LIMIT = 16 * 1024 * 1024  # предел размера страницы-артефакта


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data", nargs="?", help="путь к data-*.json; по умолчанию — самый новый data-vNN.json")
    ap.add_argument("--template", default=os.path.join(HERE, "dashboard.html"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "titration-dashboard", "index-new.html"))
    a = ap.parse_args()
    if not a.data:
        found = glob.glob(os.path.join(ROOT, "data", "titration-dashboard", "data-v*.json"))
        ver = lambda p: int(re.search(r"data-v(\d+)\.json$", p).group(1)) if re.search(r"data-v(\d+)\.json$", p) else -1
        found = [p for p in found if ver(p) >= 0]
        if not found:
            raise SystemExit("нет data-vNN.json в data/titration-dashboard/")
        a.data = max(found, key=ver)
    print(f"данные: {a.data}")
    with open(a.data, encoding="utf-8") as f:
        data = f.read()
    data = data.replace("</", "<\/")  # JSON внутри <script>: закрывающий тег не должен встретиться
    with open(a.template, encoding="utf-8") as f:
        page = f.read()
    assert page.count("__DATA__") == 1, "в шаблоне ровно одно место для данных"
    out = page.replace("__DATA__", data)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(out)
    size = os.path.getsize(a.out)
    print(f"{a.out}: {size / 1e6:.2f} МБ")
    if size >= LIMIT:
        raise SystemExit(f"страница {size} байт ≥ 16 МБ")


if __name__ == "__main__":
    main()
