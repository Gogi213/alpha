"""П-42 §13.25 (TK-600): рука X — обёртка над кодом TK-598 с матрицей признаков X (55) вместо f12 (§13.25 п. 4); код tk598_fit / tk598_table не правится.

  python3 tk600_fit.py table --run <out/NAME> --variant L_u0 --win F --feat <feat-X-….parquet> --path <path-….parquet> --out <tab-X-F-….parquet>   (= tk598_table.py build)
  python3 tk600_fit.py fit   --side long|short --u0 <tab-X-F-U0> --u1 <tab-X-F-U1> --out <каталог> [--procs 6]                                     (= tk598_fit.py fit)
Подмена — `tk598_lib.FEATS = X_FEATS` до вызова: `xmat`, слияние таблицы и важности читают список в момент вызова (проверка G-P — test_tk600.py::PipelineIdentity).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk598_lib as L  # noqa: E402
import tk600_feat as F6  # noqa: E402

F12_ORIG = list(L.FEATS)
L.FEATS = list(F6.X_FEATS)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("table", "fit"):
        raise SystemExit("table | fit")
    cmd = sys.argv.pop(1)
    if cmd == "table":
        import tk598_table as M
        sys.argv.insert(1, "build")
    else:
        import tk598_fit as M
        sys.argv.insert(1, "fit")
    M.main()


if __name__ == "__main__":
    main()
