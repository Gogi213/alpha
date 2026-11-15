"""Каталог для стенда макета: из pickle chart_mock_data.py (srv) — arb.json со строками таблицы (+ g, z) и chart-1.jsonl с рядами Se/Sl/Sh/Be/Bl/Bh/C.

Запуск: python build_mock_dir.py <mock.pkl> <каталог>; затем python mock_server.py <репо> <каталог> <порт>.
"""
import json
import pickle
import sys
import time
from pathlib import Path


def r1(v):
    return None if v is None else round(v, 1)


def table_part(st, pk, tau, net):
    return {"pk": pk and [r1(x) for x in pk], "tau": tau and [round(x, 3) for x in tau], "net": net and [r1(x) for x in net]}


def q10(a):
    return [None if v is None else int(round(v * 10)) for v in a]


def main(src, out):
    d = pickle.load(open(src, "rb"))
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rows, off = [], 0
    with open(out / "chart-1.jsonl", "wb") as f:
        for k in sorted(d["det"]):
            v = d["det"][k]
            s, z = v["std"], v["z"]
            line = (json.dumps({n: q10(v["ch"][n]) for n in ("Se", "Sl", "Sh", "Be", "Bl", "Bh", "C")}, separators=(",", ":")) + "\n").encode()
            f.write(line)
            st = s["st"]
            row = {"c": k[0], "a": k[1], "b": k[2], "cnt": s["cnt"], "big": s["big"], **table_part(st, st["pk"], st["tau"], st["net"]),
                   "g": v["g"], "z": {"cnt": z["cnt"], "big": z["big"], **table_part(z, z["pk"], z["tau"], z["net"])}, "o": off, "l": len(line)}
            rows.append(row)
            off += len(line)
    doc = {"gen": int(time.time() * 1000), "t0": d["t0"], "t1": d["t1"], "start": d["t0"], "slot": d["slot"], "fee": d["fee"], "chart": "chart-1.jsonl",
           "names": d["names"], "written": int(time.time() * 1000), "period": 300, "rows": rows}
    (out / "arb.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(len(rows), "rows;", sum(1 for r in rows if r["g"]), "with gap now")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
