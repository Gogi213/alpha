#!/usr/bin/env python3
"""TK-516: pq2bin на srv с ПК без оболочки: `python tools/p40core/tk516_run.py <push|build|test|ab|bench|all>`.
push — заливает pq2bin/{Cargo.toml,src} и tk516.sh в /root/p40core-tk516 (LF); остальное — запуск tk516.sh на srv (build/test: cargo, ab: байт в байт против базы, bench: МБ/с user-CPU).
Код выхода — удалённого шага (0 — ок); 2 — не залилось."""
import io
import os
import subprocess
import sys
import tarfile

os.environ.setdefault("P40_DIR", "/root/p40core-tk516")
from gate import D, REMOTE, SSH, lf, ssh  # noqa: E402


def push() -> bool:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        files = [(D / "tk516.sh", "tk516.sh", 0o755), (D / "pq2bin" / "Cargo.toml", "pq2bin/Cargo.toml", 0o644)]
        files += [(p, f"pq2bin/src/{p.name}", 0o644) for p in sorted((D / "pq2bin" / "src").glob("*.rs"))]
        for p, name, mode in files:
            b = lf(p)
            ti = tarfile.TarInfo(name)
            ti.size, ti.mode = len(b), mode
            t.addfile(ti, io.BytesIO(b))
    r = ssh(f"mkdir -p {REMOTE}/pq2bin/src && tar xf - -C {REMOTE}", buf.getvalue())
    if r.returncode:
        sys.stderr.write(r.stderr.decode(errors="replace"))
    return r.returncode == 0


def run(step: str) -> int:
    p = subprocess.Popen(SSH + [f"Q={REMOTE} bash {REMOTE}/tk516.sh {step}"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for line in p.stdout:
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()
    return p.wait()


def main() -> int:
    steps = sys.argv[1:] or ["all"]
    if not push():
        return 2
    if steps == ["push"]:
        return 0
    if steps == ["all"]:
        steps = ["build", "test", "ab", "bench"]
    for s in steps:
        rc = run(s)
        print(f"== {s}: rc={rc}")
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
