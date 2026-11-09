"""TK-510: aux_feats.py — час без файла mark/OI у поставщика: пустой ряд, строка в .auxholes.csv, признаки в окне дыры — NaN/−1, не ноль.
Запуск: python -m pytest tools/p40core/test_aux_feats.py"""
import calendar, csv, math, os, subprocess, sys

import pyarrow as pa
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
DAY = "2026-09-25"
T0 = calendar.timegm((2026, 9, 25, 0, 0, 0)) * 1000
SYM = "XUSDT"


def hms(h, m):
    return T0 + h * 3600000 + m * 60000


def make_root(tmp, no_mark=(), no_oi=(), ex="bitget"):
    root = os.path.join(tmp, "chft", f"{ex}_futures", DAY)
    for h in range(24):
        d = os.path.join(root, f"{h:02d}")
        os.makedirs(d, exist_ok=True)
        ts = [hms(h, m) for m in range(60)]
        if h not in no_mark:
            pq.write_table(pa.table({"event_time": ts, "funding_rate": [1e-4] * 60, "next_funding_time": [T0 + 8 * 3600000] * 60}), f"{d}/{SYM}_mark_price.parquet")
        if h not in no_oi:
            pq.write_table(pa.table({"timestamp": ts, "sum_open_interest_value": [100.0 + 60 * h + m for m in range(60)]}), f"{d}/{SYM}_open_interest.parquet")
    return root


def run_aux(tmp, root, times, root_b="-"):
    out = os.path.join(tmp, "o")
    with open(out + ".features.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["theta", "t_start"])
        w.writerows([["5.04", t] for t in times])
    r = subprocess.run([sys.executable, os.path.join(HERE, "aux_feats.py"), out, root, SYM, root_b, SYM if root_b != "-" else "-"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    aux = {int(float(x["t_start"])): x for x in csv.DictReader(open(out + ".aux.csv"))}
    holes = [tuple(x.values()) for x in csv.DictReader(open(out + ".auxholes.csv"))]
    return aux, holes


def nan(x):
    return math.isnan(float(x))


def test_missing_hour_files_nan_not_zero(tmp_path):
    root = make_root(str(tmp_path), no_mark=(9,), no_oi=(9, 12))
    ts = {k: hms(*k) for k in [(6, 30), (9, 30), (10, 2), (10, 30), (12, 30)]}
    aux, holes = run_aux(str(tmp_path), root, list(ts.values()))
    assert ("bitget", SYM, DAY, "9", "mark_price") in holes and ("bitget", SYM, DAY, "9", "open_interest") in holes
    assert ("bitget", SYM, DAY, "12", "open_interest") in holes and ("bitget", SYM, DAY, "9", "liquidations") in holes
    assert not any(h[3] == "12" and h[4] == "liquidations" for h in holes)  # час есть (mark), ликвидаций в нём нет — не дыра
    ok = aux[ts[(6, 30)]]  # контроль без дыр: значения есть
    assert not nan(ok["oi5m"]) and ok["liq5m"] == "0" and not nan(ok["rA"])
    h9 = aux[ts[(9, 30)]]  # сам час дыры
    assert nan(h9["oi5m"]) and nan(h9["phi_bps"]) and nan(h9["rA"]) and h9["n_s"] == "-1" and h9["liq5m"] == "-1"
    near = aux[ts[(10, 2)]]  # окно 5 мин задевает дыру: OI и ликвидации неизвестны, mark есть
    assert nan(near["oi5m"]) and near["liq5m"] == "-1" and not nan(near["rA"])
    after = aux[ts[(10, 30)]]  # дыра далеко позади
    assert not nan(after["oi5m"]) and after["liq5m"] == "0"
    oi_only = aux[ts[(12, 30)]]  # нет только OI: oi5m NaN, liq и mark на месте
    assert nan(oi_only["oi5m"]) and oi_only["liq5m"] == "0" and not nan(oi_only["rA"])


def test_no_holes_unchanged_columns(tmp_path):
    root = make_root(str(tmp_path))
    aux, holes = run_aux(str(tmp_path), root, [hms(6, 30)])
    assert holes == []
    a = aux[hms(6, 30)]
    assert float(a["oi5m"]) > 0 and a["liq5m"] == "0" and a["srcA"] == "1" and a["srcB"] == "0" and a["srcOIA"] == "1" and a["srcOIB"] == "0"


def test_aster_no_oi_source_not_hole(tmp_path):
    """TK-512: у Aster OI нет вовсе — не дыра (auxholes без OI), srcOIA = 0; oi5m — по второй ноге с рядом OI; без OI на обеих ногах — NaN"""
    ra = make_root(str(tmp_path), no_oi=range(24), ex="aster")
    rb = make_root(str(tmp_path), ex="bitget")
    ts = [hms(6, 30)]
    aux, holes = run_aux(str(tmp_path), ra, ts, root_b=rb)
    assert not any(h[0] == "aster" for h in holes) and holes == []
    a = aux[ts[0]]
    assert a["srcA"] == "1" and a["srcB"] == "1" and a["srcOIA"] == "0" and a["srcOIB"] == "1"
    assert not nan(a["oi5m"]) and not nan(a["rA"]) and not nan(a["rB"]) and a["liq5m"] == "0"  # OI по Bitget, mark Aster читается
    solo, holes = run_aux(str(tmp_path), ra, ts)  # Aster × нога без рядов: OI ни у кого
    assert holes == [] and nan(solo[ts[0]]["oi5m"]) and solo[ts[0]]["srcOIA"] == "0" and solo[ts[0]]["srcOIB"] == "0" and not nan(solo[ts[0]]["rA"])


if __name__ == "__main__":  # без pytest (сервер счёта): python3 test_aux_feats.py
    import pathlib, tempfile
    for fn in (test_missing_hour_files_nan_not_zero, test_no_holes_unchanged_columns, test_aster_no_oi_source_not_hole):
        with tempfile.TemporaryDirectory() as d:
            fn(pathlib.Path(d))
        print("ok", fn.__name__)
