"""hostmon OUT SEC [PERIOD] — TK-532: пауза доставки на части соединений — виноват хост (vCPU/очередь virtio) или путь? Раз в PERIOD с (0,1) пишет в OUT (jsonl):
t (epoch), loc (счётчик таймера LOC по vCPU — у остановленного vCPU не растёт), vec (прерывания по векторам eth0 virtio, сумма по vCPU), steal и idle (jiffies по vCPU), softirq_rx (NET_RX по vCPU).
Читает только /proc; нагрузка — один процесс, ~1 % ядра."""
import json
import re
import sys
import time


def read():
    cpu = {}
    for l in open("/proc/stat"):
        m = re.match(r"cpu(\d+) (.*)", l)
        if m:
            v = list(map(int, m.group(2).split()))
            cpu[int(m.group(1))] = {"steal": v[7], "idle": v[3]}
    loc, vec = [], []
    for l in open("/proc/interrupts"):
        p = l.split()
        if p and p[0] == "LOC:":
            loc = list(map(int, p[1:1 + len(cpu)]))
        elif "PCI-MSIX-0000:06:12.0" in l:  # virtio eth0: векторы очередей
            vec.append(sum(int(x) for x in p[1:1 + len(cpu)]))
    rx = []
    for l in open("/proc/softirqs"):
        if l.strip().startswith("NET_RX:"):
            rx = list(map(int, l.split()[1:1 + len(cpu)]))
    return {"t": round(time.time(), 3), "loc": loc, "vec": vec, "steal": [cpu[i]["steal"] for i in sorted(cpu)], "idle": [cpu[i]["idle"] for i in sorted(cpu)], "softirq_rx": rx}


def main():
    out, sec = sys.argv[1], float(sys.argv[2])
    period = float(sys.argv[3]) if len(sys.argv) > 3 else 0.1
    end = time.monotonic() + sec
    with open(out, "w") as f:
        nxt = time.monotonic()
        while time.monotonic() < end:
            f.write(json.dumps(read(), separators=(",", ":")) + "\n")
            nxt += period
            time.sleep(max(0, nxt - time.monotonic()))
            if int(nxt * 10) % 100 == 0:
                f.flush()


main()
