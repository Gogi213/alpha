#!/usr/bin/env python3
"""TK-463: проверка приёмки ядра tk463-sim — читает логи гейтов на calc по ssh, rc != 0 при несовпадении любого счётчика.
Использование: python tools/compute/tk463-check.py   (ключи/хосты — ~/.ssh/config, алиас calc)
rc: 0 — все сошлись, 1 — счётчик не сошёлся, 2 — ssh calc недоступен."""
import subprocess
import sys

D = "/data/tk0463"
REMOTE = f"""
echo md5 $(md5sum {D}/bin/tk463-sim | cut -c1-8)
echo d2a $(grep -c 'cmp: IDENTICAL 252 из 252' {D}/d2a_full/full.log)
echo gate_done $(grep -c 'jobs 21 ok 42' {D}/out/GATE.DONE)
echo gate_ident $(grep -c IDENTICAL {D}/out/gate.log)
echo gate_bad $(grep -c -v IDENTICAL {D}/out/gate.log)
echo twt1440ctrl $(grep -c '^ctrl TWTUSDT 1440 .*IDENTICAL' {D}/out/gate.log)
echo d2b_bytes $(grep -c BYTES_EQ {D}/d2b/gate2.log)
echo d2b_ident $(grep -c IDENTICAL {D}/d2b/gate2.log)
echo synth $(grep -c IDENTICAL {D}/synth/gate.log)
"""
EXPECT = [("md5", "fa014fb0"), ("d2a", "1"), ("gate_done", "1"), ("gate_ident", "42"), ("gate_bad", "0"),
          ("twt1440ctrl", "1"), ("d2b_bytes", "9"), ("d2b_ident", "9"), ("synth", "6")]


def main():
    try:
        p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=30", "calc", REMOTE],
                           capture_output=True, text=True, encoding="utf-8", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"FAIL: ssh calc недоступен ({e})", file=sys.stderr)
        return 2
    if p.returncode != 0 and not p.stdout.strip():
        print(f"FAIL: ssh calc недоступен: {p.stderr.strip()}", file=sys.stderr)
        return 2
    print(p.stdout.strip())
    got = {}
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            got[parts[0]] = parts[1]
    rc = 0
    for k, v in EXPECT:
        if got.get(k) != v:
            print(f"FAIL: {k} = {got.get(k)} != {v}", file=sys.stderr)
            rc = 1
    if rc == 0:
        print("OK: все счётчики сошлись")
    return rc


if __name__ == "__main__":
    sys.exit(main())
