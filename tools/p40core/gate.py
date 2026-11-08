#!/usr/bin/env python3
"""TK-500: гейт приёмки ядра эпизодов П-40 одной командой без оболочки и WSL: `python tools/p40core/gate.py [база=old]`.

1) заливает на 5.231.25.224 (/root/p40core) srv-gate.sh, srv-test.sh и cmp_*.py из ветки (LF);
2) сверяет md5 исходников ядра (rs/Cargo.toml, rs/src/*.rs) на сервере с веткой — расхождение = отказ (код 3);
3) запускает srv-gate.sh на сервере (тесты, 5 таблиц байт в байт с базой /tmp/<база>.csv*, сверка с эталоном TK-501).
Код выхода = код удалённого гейта (0 — ок, 1 — отказ гейта); 2 — не залилось, 3 — исходники на сервере ≠ ветке.
На ПК только ssh (ключ и алиас srv — ~/.ssh/config)."""
import hashlib
import io
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

HOST = "srv"
REMOTE = "/root/p40core"
D = Path(__file__).resolve().parent
SSH = [shutil.which("ssh") or "ssh", "-o", "BatchMode=yes", HOST]


def lf(p: Path) -> bytes:
    return p.read_bytes().replace(b"\r\n", b"\n")


def ssh(cmd: str, data: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(SSH + [cmd], input=data, capture_output=True)


def push_scripts() -> bool:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for p in [D / "srv-gate.sh", D / "srv-test.sh", *sorted(D.glob("cmp_*.py"))]:
            b = lf(p)
            ti = tarfile.TarInfo(p.name)
            ti.size = len(b)
            ti.mode = 0o755 if p.suffix == ".sh" else 0o644
            t.addfile(ti, io.BytesIO(b))
    r = ssh(f"tar xf - -C {REMOTE}", buf.getvalue())
    if r.returncode:
        sys.stderr.write(r.stderr.decode(errors="replace"))
    return r.returncode == 0


def sources_match() -> bool:
    local = {f"{p.relative_to(D / 'rs').as_posix()}": hashlib.md5(lf(p)).hexdigest()
             for p in [D / "rs" / "Cargo.toml", *sorted((D / "rs" / "src").glob("*.rs"))]}
    # CR отбрасываем с обеих сторон: queue.rs в рабочей копии и на сервере с CRLF, в git — LF
    r = ssh(f"cd {REMOTE}/rs && for f in Cargo.toml src/*.rs; do echo \"$(tr -d '\r' < $f | md5sum | cut -c1-32)  $f\"; done")
    if r.returncode:
        sys.stderr.write(r.stderr.decode(errors="replace"))
        return False
    remote = {}
    for line in r.stdout.decode().splitlines():
        h, _, name = line.partition("  ")
        remote[name.strip()] = h
    bad = sorted(k for k in local.keys() | remote.keys() if local.get(k) != remote.get(k))
    for k in bad:
        print(f"ГЕЙТ: {k} на сервере ≠ ветке")
    return not bad


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "old"
    if not push_scripts():
        return 2
    if not sources_match():
        return 3
    # вывод удалённого гейта — построчно, код = код ssh
    p = subprocess.Popen(SSH + [f"bash {REMOTE}/srv-gate.sh {base}"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for line in p.stdout:
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()
    return p.wait()


if __name__ == "__main__":
    sys.exit(main())
