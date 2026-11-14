import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import parse_inc  # noqa: E402

parse_inc.DECOMP = ["cat"]


def row(delta_ns, lag, sym, bid, ask):
    # поля: вид, дельта времени (нс), лаг (мс), -, символ, -, bid, bsz, ask, asz
    return f"b\t{delta_ns}\t{lag}\t-\t{sym}\t-\t{bid}\t1.5\t{ask}\t2.5\n".encode()


def chunks(out):
    r = {}
    for f in sorted(Path(out, "x").glob("*.npz")):
        sym = f.name.split(".")[0]
        d = np.load(f)
        r.setdefault(sym, []).append({k: d[k].copy() for k in d.files})
    return {s: {k: np.concatenate([c[k] for c in v]) for k in v[0]} for s, v in r.items()}


def test_incremental_equals_whole(tmp_path):
    rows = [row(1_000_000_000, 5, "AAA", 100, 101), row(1_000_000, 0, "AAA", 100, 101),    # повтор цены — снимается
            row(2_000_000_000, 3, "BBB", 50, 51), row(1_000_000, 0, "ZZZ", 1, 2),           # ZZZ не в списке
            row(3_000_000_000, 4000, "AAA", 100, 101),                                      # лаг > 2 с — не повтор
            row(1_000_000_000, 1, "AAA", 102, 103)]
    full = tmp_path / "whole.tsv"
    full.write_bytes(b"".join(rows))
    part = tmp_path / "part.tsv"
    want = {b"AAA": "AAA", b"BBB": "BBB"}
    out1, out2, st1, st2 = (tmp_path / n for n in ("o1", "o2", "s1", "s2"))
    for d in (st1, st2):
        d.mkdir()
    parse_inc.run("x", "d", str(full), want, st1, out1, 0)
    # по частям: сначала 3 строки и половина четвёртой, потом файл дописан
    blob = b"".join(rows)
    cut = len(b"".join(rows[:3])) + 7
    part.write_bytes(blob[:cut])
    parse_inc.run("x", "d", str(part), want, st2, out2, 0)
    part.write_bytes(blob)
    parse_inc.run("x", "d", str(part), want, st2, out2, 0)
    a, b = chunks(out1), chunks(out2)
    assert a.keys() == b.keys() == {"AAA", "BBB"}
    for s in a:
        for k in a[s]:
            assert np.array_equal(a[s][k], b[s][k]), (s, k)
    assert a["AAA"]["rx"].tolist() == [1000, 6002, 7002] and a["BBB"]["rx"].tolist() == [3001]
    assert a["AAA"]["e"][1] == 6002 - 4000


def test_keep_from_and_done(tmp_path):
    rows = [row(1_000_000_000, 0, "AAA", 100, 101), row(1_000_000_000, 0, "AAA", 100, 102)]
    f = tmp_path / "f.tsv.zst"
    f.write_bytes(b"".join(rows))
    Path(str(f) + ".ok").write_text("ok")
    st, out = tmp_path / "s", tmp_path / "o"
    st.mkdir()
    n = parse_inc.run("x", "d", str(f), {b"AAA": "AAA"}, st, out, 1500)   # первая запись (1000 мс) раньше keep_from
    assert n == 1
    assert parse_inc.load_state(st, "x", parse_inc.tag_of("d", str(f)))["done"] is True
    assert parse_inc.run("x", "d", str(f), {b"AAA": "AAA"}, st, out, 1500) == 0
    assert parse_inc.prune(st, out, "x", 10_000) == 1
    assert not list(Path(out, "x").glob("*.npz"))
