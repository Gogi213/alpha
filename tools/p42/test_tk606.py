"""TK-606: данные вкладки «Признаки» (ml_features.json) — схема, ссылки на источники, повторы имён, цифры только у готовых наборов."""
import json
import os
import sys

import jsonschema

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def load(name):
    return json.load(open(os.path.join(HERE, name), encoding="utf-8"))


def check_features():
    # tk593_page.py тянет pandas при импорте; нужная функция — отдельный разбор исходника
    import ast
    src = open(os.path.join(HERE, "tk593_page.py"), encoding="utf-8").read()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "check_features")
    ns = {}
    exec(compile(ast.Module([fn], []), "tk593_page.py", "exec"), ns)
    return ns["check_features"]


def test_schema_and_semantics():
    f = load("ml_features.json")
    jsonschema.validate(f, load("ml_features.schema.json"))
    check_features()(f)


def test_counts_and_known_names():
    f = load("ml_features.json")
    n = {s["id"]: sum(len(g["features"]) for g in s["groups"]) for s in f["sets"]}
    assert n == {"f12": 12, "flow": 30, "setup": 46, "toxic": 13, "breakability": 6}, n
    names = {x["name"] for s in f["sets"] for g in s["groups"] for x in g["features"]}
    assert {"lturn24", "bd_thin30", "mk60_1800", "bk60"} <= names


def test_semantic_checks_catch_bad_data():
    chk = check_features()
    f = load("ml_features.json")
    for mutate, msg in [
        (lambda d: d["sets"][0]["groups"][0]["features"][1].update(name=d["sets"][0]["groups"][0]["features"][0]["name"]), "повторяется"),
        (lambda d: d["sets"][0]["groups"][0]["features"][0].update(src=["нет-такого"]), "не из sources"),
        (lambda d: d["sets"][3].update(numbers=["+0,5"]), "цифр быть не должно"),
    ]:
        d = json.loads(json.dumps(f))
        mutate(d)
        try:
            chk(d)
        except AssertionError as e:
            assert msg in str(e), (msg, str(e))
        else:
            raise AssertionError("проверка не поймала: " + msg)
