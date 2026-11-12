#!/usr/bin/env python3
"""G-022 турбо: монитор окна на сервере — CPU юнитов (cgroup v2), очередь приёма сокетов (Recv-Q, ss) и число соединений по юнитам.
Запуск: winmon.py --seconds 180 --unit bnws-prod.service --unit turbo-bn.service --out /data/turbo-probe/winmon.json
Читает только /sys/fs/cgroup и `ss`; ничего не пишет, кроме --out. Юнит = группа процессов (у Python — родитель + процесс на шард, у Rust — один процесс).
"""
import argparse
import json
import re
import subprocess
import time

CG = "/sys/fs/cgroup/system.slice"


def cpu_usec(unit):
    try:
        for ln in open(f"{CG}/{unit}/cpu.stat"):
            if ln.startswith("usage_usec"):
                return int(ln.split()[1])
    except OSError:
        pass
    return None


def psi_some_usec(unit):
    """Время, когда хотя бы одна задача юнита ждала CPU в очереди (cgroup v2 cpu.pressure, some total, мкс)."""
    try:
        for ln in open(f"{CG}/{unit}/cpu.pressure"):
            if ln.startswith("some"):
                return int(re.search(r"total=(\d+)", ln).group(1))
    except (OSError, AttributeError):
        pass
    return None


def pids(unit):
    try:
        return {int(x) for x in open(f"{CG}/{unit}/cgroup.procs").read().split()}
    except OSError:
        return set()


def ss_rows():
    out = subprocess.run(["ss", "-tnpH", "state", "established"], capture_output=True, text=True).stdout
    for ln in out.splitlines():
        f = ln.split()
        m = re.search(r"pid=(\d+)", ln)
        if len(f) >= 4 and m:
            yield int(f[0]), int(m.group(1)), f[3]  # Recv-Q, pid, peer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=180)
    ap.add_argument("--every", type=float, default=1.0)
    ap.add_argument("--unit", action="append", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    st = {u: {"cpu": [], "psi": [], "recvq_max": 0, "recvq_samples": 0, "recvq_nonzero": 0, "recvq_bytes_sum": 0, "conns_max": 0} for u in a.unit}
    prev = {u: (cpu_usec(u), psi_some_usec(u), time.monotonic()) for u in a.unit}
    t_end = time.monotonic() + a.seconds
    started = time.time()
    while time.monotonic() < t_end:
        time.sleep(a.every)
        now = time.monotonic()
        pset = {u: pids(u) for u in a.unit}
        conns = {u: 0 for u in a.unit}
        for q, pid, peer in ss_rows():
            for u, ps in pset.items():
                if pid in ps and not peer.startswith("127."):
                    conns[u] += 1
                    s = st[u]
                    s["recvq_samples"] += 1
                    s["recvq_bytes_sum"] += q
                    s["recvq_max"] = max(s["recvq_max"], q)
                    s["recvq_nonzero"] += q > 0
        for u in a.unit:
            c, p0, t0 = prev[u]
            c1, p1 = cpu_usec(u), psi_some_usec(u)
            if c is not None and c1 is not None:
                st[u]["cpu"].append((c1 - c) / 1e6 / (now - t0))
            if p0 is not None and p1 is not None:
                st[u]["psi"].append((p1 - p0) / 1e6 / (now - t0))
            prev[u] = (c1, p1, now)
            st[u]["conns_max"] = max(st[u]["conns_max"], conns[u])
    out = {"started": started, "seconds": a.seconds, "units": {}}
    for u, s in st.items():
        cpu = sorted(s["cpu"])
        out["units"][u] = {
            "cpu_cores_mean": round(sum(cpu) / len(cpu), 3) if cpu else None, "cpu_cores_p95": round(cpu[int(len(cpu) * 0.95)], 3) if cpu else None, "cpu_cores_max": round(cpu[-1], 3) if cpu else None,
            "cpu_wait_some_frac_mean": round(sum(s["psi"]) / len(s["psi"]), 4) if s["psi"] else None, "cpu_wait_some_frac_max": round(max(s["psi"]), 4) if s["psi"] else None,
            "recvq_max_bytes": s["recvq_max"], "recvq_samples": s["recvq_samples"], "recvq_nonzero_samples": s["recvq_nonzero"],
            "recvq_nonzero_pct": round(100 * s["recvq_nonzero"] / s["recvq_samples"], 3) if s["recvq_samples"] else None,
            "recvq_mean_bytes": round(s["recvq_bytes_sum"] / s["recvq_samples"], 1) if s["recvq_samples"] else None, "conns_max": s["conns_max"],
        }
    json.dump(out, open(a.out, "w"), indent=1)
    print(json.dumps(out["units"]))


if __name__ == "__main__":
    main()
