"""Аргументы вместо неявного окружения P12_* (TK-135/С-61): флаг, если задан, кладётся в os.environ (его читают p12lib и головы при импорте);
не задан — остаётся прежнее окружение, умолчания и выход байт в байт как были. Вызывать ДО import p12lib; флаги вырезаются из sys.argv."""
import argparse, os, sys

FLAGS = {"pool-from": "P12_POOL_FROM", "only": "P12_ONLY", "out": "P12_OUT", "dir": "P12_DIR", "tag": "P12_TAG", "mode": "P12_MODE",
         "sharpe-csv": "P12_SHARPE_CSV", "ntr": "P12_NTR", "drop": "P12_DROP", "rows-a": "P12_ROWS_A"}


def apply(argv=None):
    ap = argparse.ArgumentParser(add_help=False)
    for f, env in FLAGS.items():
        ap.add_argument("--" + f, default=None, help=f"= {env}")
    ns, rest = ap.parse_known_args(sys.argv[1:] if argv is None else argv)
    for f, env in FLAGS.items():
        v = getattr(ns, f.replace("-", "_"))
        if v is not None:
            os.environ[env] = v
    if argv is None:
        sys.argv[1:] = rest
    return ns
