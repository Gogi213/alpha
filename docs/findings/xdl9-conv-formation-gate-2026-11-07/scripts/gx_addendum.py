#!/usr/bin/env python3
"""TK-493, задача C (не код репозитория): conv_addendum.main для набора Gate (c2g/out); путь K0 подменяется на c2g/calib_start.json (в коде зашит ~/xdl9/c2/calib_start.json).
  python gx_addendum.py DEST PREFIX CSV_NAME WHEN
"""
import os
import sys

CODE = os.path.expanduser("~/xdl9/c2g/repo/tools/compute/statarb/xdl9/conv")
sys.path.insert(0, CODE)
import conv_addendum as A       # noqa: E402

_orig = os.path.expanduser
OLD = "~/xdl9/c2/calib_start.json"


def patched(p):
    if p == OLD:
        return _orig("~/xdl9/c2g/calib_start.json")
    return _orig(p)


os.path.expanduser = patched

if __name__ == "__main__":
    dest, prefix, csvname, when = sys.argv[1:5]
    sys.exit(A.main(["--out", _orig("~/xdl9/c2g/out"), "--dest", dest, "--prefix", prefix, "--when", when, "--code-dir", CODE, "--csv-name", csvname]))
