#!/usr/bin/env python3
"""Сборка страницы дашборда титрования: шаблон `titration-dashboard.html` + JSON от
`titration-dashboard-data.py` → одна самодостаточная страница (данные внутри).

    titration-dashboard-build.py --data titration-dashboard-v1.json --out titration-dashboard-v1.html
"""
import argparse
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--template", default=os.path.join(HERE, "titration-dashboard.html"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    with open(a.data, encoding="utf-8") as f:
        data = f.read()
    if "</" in data:
        data = data.replace("</", "<\\/")  # JSON внутри <script>: закрывающий тег не должен встретиться
    with open(a.template, encoding="utf-8") as f:
        page = f.read()
    assert page.count("__DATA__") == 1, "в шаблоне ровно одно место для данных"
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(page.replace("__DATA__", data))
    print(f"{a.out}: {os.path.getsize(a.out) / 1e6:.2f} МБ")


if __name__ == "__main__":
    main()
