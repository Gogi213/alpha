#!/usr/bin/env python3
"""Достаёт текст документации BingX из бандла сайта (https://bingx-api.github.io/docs/static/js/app.*.js): модули вида "KEY":function(e,t){e.exports=[…]}.
  bingxdoc.py app.js KEYWORD [KEYWORD…]   — печатает значения text:/name:/description:/uri:/dataType: модулей, где встречается любое из слов (регистр важен)"""
import re, sys

s = open(sys.argv[1], encoding="utf-8").read()
kws = sys.argv[2:]
mods = re.split(r'\},"[A-Za-z0-9+/_$]{4}":function\(', s)
n = 0
for m in mods:
    if any(k in m for k in kws) and "exports=" in m[:80] and len(m) < 60000:
        n += 1
        print("=" * 20, "module", len(m))
        for x in re.finditer(r'(text|name|description|uri|dataType|desc):"((?:[^"\\]|\\.)*)"', m):
            print(f"{x.group(1)}: {x.group(2)}")
        for x in re.finditer(r'(response|payload):(\{.{0,1500})', m):
            print(f"{x.group(1)} => {x.group(2)[:1500]}")
print("modules", n, file=sys.stderr)
