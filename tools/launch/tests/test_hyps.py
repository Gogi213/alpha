import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
import hyps, launch

OFF = [(r, launch.OFF_LABELS[r]) for r in launch.OFF_ROUTES]
FULL = 0b111   # три месяца
MON = ["2026-01", "2026-02", "2026-03"]


def h(i, pool="не проверялась", fam="стена", **ext):
    return {"id": i, "family": fam, "title": "t " + i, "ext": {"status_pool_0925": pool, "mechanism": "m", **ext}}


def cell(mask=0, q=0, off=0):
    return [0, mask, q, off]


def st(hyp, cells):
    return hyps.classify(hyp, cells, 3, OFF)[:2]


def test_status_from_cells():
    assert st(h("Г-1"), [cell(FULL), cell(FULL)])[0] == "ok"
    assert st(h("Г-1"), [cell(FULL), cell(0b1)])[0] == "part"
    assert st(h("Г-1"), [cell(FULL), cell(0, q=0b1)])[0] == "run"   # очередь важнее готового
    assert st(h("Г-1"), [cell(0)])[0] == "wait"
    assert st(h("Г-1"), [cell(0, off=launch.OFF_ROUTES.index("nodata") + 1)])[0] == "nodata"
    assert st(h("Г-1"), [cell(0, off=launch.OFF_ROUTES.index("noline") + 1)])[0] == "nocode"
    assert st(h("Г-1"), [cell(0, off=launch.OFF_ROUTES.index("outside") + 1)])[0] == "ok"


def test_status_from_pool_when_no_grid():
    assert st(h("Г-2", "**опровергнуто** (R2)"), [])[0] == "bad"
    assert st(h("Г-2", "поиск− (E28)"), [cell(FULL)])[0] == "bad"   # отвергнутая остаётся отвергнутой, хотя клетки посчитаны
    assert st(h("Г-2", "не ясно (R2)"), [])[0] == "ok"
    assert st(h("Г-2", "не проверялась", data="внешнее (фандинг)"), [])[0] == "nodata"
    assert st(h("Г-2", "не проверялась", data="сделки"), [])[0] == "nocode"
    assert st(h("Г-2", "не проверялась, заблокирована В-106", data="внешнее"), [])[0] == "wait"
    assert st(h("Г-2", "не проверялась", priority="не повторять"), [])[0] == "bad"


def test_view_rows_sorted_texts_and_methods():
    rows = [h("Г-10"), h("Г-9"), h("Г-3", fam="вход"), h("Г-1", fam="подход", **{"class": "method"})]
    v = hyps.view(rows, {"Г-9": {"cells": [cell(FULL)]}}, MON, OFF, texts={"Г-9": {"what": "просто", "why": "потому"}}, sieve_ok={"Г-9": 1})
    assert [r["id"] for r in v["rows"]] == ["Г-1", "Г-9", "Г-10", "Г-3"]   # методы первыми, затем класс, затем номер
    assert v["rows"][0]["cls"] == "Методы"
    g9 = v["rows"][1]
    assert (g9["what"], g9["why_text"], g9["status"], g9["elig"], g9["f1"]["full"]) == ("просто", "потому", "ok", 1, 1)
    assert v["texts_n"] == 1 and [s[0] for s in v["status"]][:2] == ["ok", "part"]


def test_real_registry_every_hypothesis_has_status():
    v = launch.hyp_view()
    ids = [json.loads(l)["id"] for l in open(os.path.join(launch.REG, "hypotheses.jsonl"), encoding="utf-8") if l.strip()]
    assert sorted(r["id"] for r in v["rows"]) == sorted(ids)
    known = {s[0] for s in v["status"]}
    assert all(r["status"] in known and r["why"] for r in v["rows"])
    assert {r["id"] for r in v["rows"] if r["cls"] == "Методы"} == {"Г-01", "Г-02", "Г-03", "М-04"}
