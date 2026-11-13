#!/usr/bin/env python3
"""G-022 collector-perf: one machine-wide measurement window.
`kwin.py snap <dir>` writes a snapshot; `kwin.py report <dir0> <dir1> [label]` prints the window report.
Machine-wide basis (coordinator 13.11): sum of cgroup cpu.stat of the 4 collector units + whole-machine softirq+irq, steal reported alongside."""
import glob
import json
import os
import re
import subprocess
import sys
import time

UNITS = {"binance": "collect-binance", "bybit": "soak-bybit", "okx": "collect-okx", "bingx": "collect-bingx"}
ROOTS = {
    "binance": "/data/collect/binance/data",
    "bybit": "/data/turbo-probe/soak-bybit/data",
    "okx": "/data/collect/okx/data",
    "bingx": "/data/collect/bingx/data",
}
HEALTH = {
    "binance": "/data/turbo-probe/soak-binance/health.json",
    "bybit": "/data/turbo-probe/soak-bybit/health.json",
    "okx": "/data/collect/okx/health.json",
    "bingx": "/data/collect/bingx/health.json",
}


def cg(unit):
    p = subprocess.run(["systemctl", "show", "-p", "ControlGroup", "--value", unit], capture_output=True, text=True).stdout.strip()
    d = {}
    for line in open(f"/sys/fs/cgroup{p}/cpu.stat"):
        k, v = line.split()
        d[k] = int(v)
    return d


def snap(out):
    os.makedirs(out, exist_ok=True)
    json.dump({"t": time.time(), "cg": {e: cg(u) for e, u in UNITS.items()}}, open(f"{out}/cg.json", "w"))
    for src, name in (
        ("/proc/stat", "stat"),
        ("/proc/softirqs", "softirqs"),
        ("/proc/interrupts", "interrupts"),
        ("/proc/net/dev", "netdev"),
        ("/proc/net/snmp", "snmp"),
        ("/proc/net/netstat", "netstat"),
        ("/proc/net/softnet_stat", "softnet"),
        ("/proc/pressure/cpu", "psi"),
    ):
        open(f"{out}/{name}", "w").write(open(src).read())
    for e, h in HEALTH.items():
        try:
            open(f"{out}/health-{e}.json", "w").write(open(h).read())
        except OSError:
            pass


def kv(path, tag):
    h = None
    for line in open(path):
        if line.startswith(tag + ":"):
            f = line.split()[1:]
            if h is None:
                h = f
            else:
                return dict(zip(h, map(int, f)))
    return {}


def report(a, b, label=""):
    ja, jb = json.load(open(f"{a}/cg.json")), json.load(open(f"{b}/cg.json"))
    dt = jb["t"] - ja["t"]
    coll_user = sum(jb["cg"][e]["user_usec"] - ja["cg"][e]["user_usec"] for e in UNITS) / 1e6 / dt
    coll_sys = sum(jb["cg"][e]["system_usec"] - ja["cg"][e]["system_usec"] for e in UNITS) / 1e6 / dt

    def stat(p):
        for line in open(p):
            if line.startswith("cpu "):
                return list(map(int, line.split()[1:]))

    s0, s1 = stat(f"{a}/stat"), stat(f"{b}/stat")
    d = [y - x for x, y in zip(s0, s1)]
    tot = sum(d[:8])
    ncpu = 8

    def cores(i):
        return d[i] / tot * ncpu

    names = ["user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal"]
    soft_irq = cores(5) + cores(6)
    busy = cores(0) + cores(1) + cores(2) + cores(5) + cores(6)

    def ctxt(p):
        return int([line for line in open(p) if line.startswith("ctxt")][0].split()[1])

    def softirq(p, tag):
        for line in open(p):
            if line.strip().startswith(tag + ":"):
                return sum(map(int, line.split()[1:9]))
        return 0

    nrx = (softirq(f"{b}/softirqs", "NET_RX") - softirq(f"{a}/softirqs", "NET_RX")) / dt

    def irqs(p):
        n = 0
        for line in open(p):
            if "virtio3-input" in line:
                n += sum(map(int, line.split()[1:9]))
        return n

    irq_rx = (irqs(f"{b}/interrupts") - irqs(f"{a}/interrupts")) / dt

    def nd(p):
        for line in open(p):
            if line.strip().startswith("eth0:"):
                f = line.split(":", 1)[1].split()
                return int(f[0]), int(f[1]), int(f[8]), int(f[9])
        return 0, 0, 0, 0

    na, nb = nd(f"{a}/netdev"), nd(f"{b}/netdev")
    rxp, txp = (nb[1] - na[1]) / dt, (nb[3] - na[3]) / dt
    sa, sb = kv(f"{a}/snmp", "Tcp"), kv(f"{b}/snmp", "Tcp")
    ea, eb = kv(f"{a}/netstat", "TcpExt"), kv(f"{b}/netstat", "TcpExt")

    def dd(x, y, k):
        return (y.get(k, 0) - x.get(k, 0)) / dt

    def psi(p):
        return int(re.search(r"some .*total=(\d+)", open(p).read()).group(1))

    psi_pct = (psi(f"{b}/psi") - psi(f"{a}/psi")) / 1e6 / dt * 100
    t0ms, t1ms = int(ja["t"] * 1000), int(jb["t"] * 1000)
    frames = 0
    for root in ROOTS.values():
        for f in glob.glob(f"{root}/stats-*.jsonl"):
            try:
                lines = open(f).read().splitlines()[-int(dt / 10) - 4:]
            except OSError:
                continue
            for ln in lines:
                try:
                    j = json.loads(ln)
                except Exception:
                    continue
                if t0ms <= j["ts_ms"] <= t1ms:
                    frames += j["msgs"]
    fps = frames / dt
    basis = coll_user + coll_sys + soft_irq
    print(f"=== {label} window {dt:.0f} s")
    print(f"collectors (cgroup cpu.stat): {coll_user + coll_sys:.2f} cores = user {coll_user:.2f} + sys {coll_sys:.2f}; frames {fps:,.0f}/s")
    print("machine (cores of 8): " + ", ".join(f"{n} {cores(i):.2f}" for i, n in enumerate(names)))
    print(f"machine softirq+irq {soft_irq:.2f} cores; busy (user+nice+sys+irq+softirq, no steal) {busy:.2f}; steal {cores(7):.2f}; PSI cpu some {psi_pct:.1f} %")
    print(f"BASIS collectors + machine softirq+irq = {basis:.2f} cores (≈0.32 softirq counted twice, constant); {basis / max(fps, 1) * 1e6:.1f} core-us/frame")
    print(
        f"net: rx {rxp:,.0f} pkt/s, tx {txp:,.0f} pkt/s (tx/rx {txp / max(rxp, 1):.2f}); rx IRQ {irq_rx:,.0f}/s; NET_RX softirq {nrx:,.0f}/s => {rxp / max(nrx, 1):.2f} pkt per NAPI run; "
        f"ctx switches {(ctxt(f'{b}/stat') - ctxt(f'{a}/stat')) / dt:,.0f}/s"
    )
    print(
        f"tcp: DelayedACKs {dd(ea, eb, 'DelayedACKs'):.1f}/s, TCPRcvCoalesce {dd(ea, eb, 'TCPRcvCoalesce'):.0f}/s, RetransSegs {dd(sa, sb, 'RetransSegs'):.1f}/s, "
        f"TCPTimeouts {dd(ea, eb, 'TCPTimeouts'):.2f}/s, TCPRcvQDrop {dd(ea, eb, 'TCPRcvQDrop'):.2f}/s, TCPBacklogDrop {dd(ea, eb, 'TCPBacklogDrop'):.2f}/s, TCPOFODrop {dd(ea, eb, 'TCPOFODrop'):.2f}/s"
    )
    for e in UNITS:
        try:
            h = json.load(open(f"{b}/health-{e}.json"))
            bp = h["backpressure"]
            print(
                f"  {e:8s} lag_in p50/p99 {h['lag_in_ms']['p50']}/{h['lag_in_ms']['p99']} ms; queue_lag p50/p99 {bp['queue_lag_ms']['p50']}/{bp['queue_lag_ms']['p99']} ms; "
                f"lag_out p50/p99 {h['lag_out_ms']['p50']}/{h['lag_out_ms']['p99']} ms; recvq behind {bp['recv_q']['behind_pct']} %, max {bp['recv_q']['max_bytes']} B; "
                f"ring full {bp['ring']['full_events_window']}; cpu {h['process']['cpu_cores']}, sched_wait {h['process']['sched_wait_cores']}"
            )
        except Exception as ex:
            print(f"  {e}: health? {ex}")


if __name__ == "__main__":
    if sys.argv[1] == "snap":
        snap(sys.argv[2])
    else:
        report(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "")
