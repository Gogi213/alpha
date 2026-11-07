#!/usr/bin/env python3
"""TK-493, задача C (не код репозитория): conv_report.main с подменой пути K0 (зашит ~/xdl9/c2/calib_start.json) на c2g/calib_start.json.
  python gx_tables.py OUT DEST
"""
import os
import sys

CODE = os.path.expanduser("~/xdl9/c2g/repo/tools/compute/statarb/xdl9/conv")
sys.path.insert(0, CODE)
import conv_report as R       # noqa: E402

_orig = os.path.expanduser
OLD = "~/xdl9/c2/calib_start.json"


def patched(p):
    return _orig("~/xdl9/c2g/calib_start.json") if p == OLD else _orig(p)


os.path.expanduser = patched

if __name__ == "__main__":
    out, dest = sys.argv[1:3]
    sys.exit(R.main(["--out", out, "--dest", dest]))
