import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import parse_inc  # noqa: E402
import parse_trades  # noqa: E402

parse_inc.DECOMP = ["cat"]


def trow(delta_ns, lag, et, sym, p, q, m):
    # t dns lag ET sym da p q lf f m nq
    return f"t\t{delta_ns}\t{lag}\t{et}\t{sym}\t0\t{p}\t{q}\t0\t0\t{m}\t\n".encode()


def chunks(out):
    r = {}
    for f in sorted(Path(out, "x").glob("*.npz")):
        d = np.load(f)
        r.setdefault(f.name.split(".")[0], []).append({k: d[k].copy() for k in d.files})
    return {s: {k: np.concatenate([c[k] for c in v]) for k in v[0]} for s, v in r.items()}


def test_trades_incremental_and_fields(tmp_path):
    rows = [trow(1_000_000_000, 5, 120, "AAA", "10.5", "2", 1),
            b"b\t1000000\t3\t0\tAAA\t1\t10.4\t1\t10.6\t1\n",                 # книга — не читается, но время приёма идёт
            trow(2_000_000_000, 3, 0, "BBB", "5", "1", 0), trow(1_000_000, 0, 0, "ZZZ", "1", "1", 0),
            trow(1_000_000_000, 7, 30, "AAA", "10.4", "3", 0)]
    blob = b"".join(rows)
    whole, part = tmp_path / "whole.tsv", tmp_path / "part.tsv"
    whole.write_bytes(blob)
    want = {b"AAA": "AAA", b"BBB": "BBB"}
    s1, s2, o1, o2 = (tmp_path / n for n in ("s1", "s2", "o1", "o2"))
    s1.mkdir()
    s2.mkdir()
    parse_trades.run("x", "d", str(whole), want, s1, o1, 0)
    part.write_bytes(blob[:len(b"".join(rows[:3])) + 9])                      # четвёртая строка оборвана
    parse_trades.run("x", "d", str(part), want, s2, o2, 0)
    part.write_bytes(blob)
    parse_trades.run("x", "d", str(part), want, s2, o2, 0)
    a, b = chunks(o1), chunks(o2)
    assert a.keys() == b.keys() == {"AAA", "BBB"}
    for s in a:
        for k in a[s]:
            assert np.array_equal(a[s][k], b[s][k]), (s, k)
    assert a["AAA"]["p"].tolist() == [10.5, 10.4] and a["AAA"]["q"].tolist() == [2.0, 3.0]
    assert a["AAA"]["m"].tolist() == [1, 0] and a["AAA"]["et"].tolist() == [120, 30]
    assert a["AAA"]["rx"].tolist() == [1000, 4002] and a["AAA"]["e"].tolist() == [1000 - 5, 4002 - 7]
