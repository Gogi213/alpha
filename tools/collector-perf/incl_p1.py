#!/usr/bin/env python3
"""Phase 1: inclusive kernel/user function shares of collector samples (callchain contains the function), by thread role."""
import collections, re, subprocess, sys
D = sys.argv[1]
pids = {}
for e in ("binance", "bybit", "okx", "bingx"):
    pids[int(open(f"{D}/{e}.pid").read())] = e
def role(c):
    for p in ("rx-", "wr-", "sk-"):
        if c.startswith(p):
            return p[:-1]
    return "other"
GROUPS = {
 "recvfrom syscall": ["__x64_sys_recvfrom"], "futex syscall": ["__x64_sys_futex"],
 "  futex_wake": ["futex_wake"], "  futex_wait": ["futex_wait"],
 "schedule (blocking/ctx switch)": ["__schedule"],
 "try_to_wake_up": ["try_to_wake_up"],
 "  IPI/remote wake": ["send_call_function_single_ipi", "native_send_call_func_single_ipi", "x2apic_send_IPI", "smp_call_function", "ttwu_queue_wakelist", "__smp_call_single_queue", "kvm_send_ipi", "native_apic_msr_write", "native_write_msr"],
 "tcp_recvmsg": ["tcp_recvmsg"], "  tcp_cleanup_rbuf": ["tcp_cleanup_rbuf"], "  tcp_send_ack/ack_snd": ["__tcp_send_ack", "tcp_send_ack", "__tcp_ack_snd_check"],
 "  copy to user": ["copy_to_user", "_copy_to_iter", "rep_movs_alternative", "copyout"],
 "tx path (xmit)": ["tcp_write_xmit", "__tcp_transmit_skb", "ip_queue_xmit", "dev_queue_xmit"],
 "  virtio kick": ["virtqueue_notify", "vp_notify", "virtqueue_kick", "iowrite16", "vp_modern_notify"],
 "softirq/net_rx_action": ["net_rx_action"], "  virtnet_poll": ["virtnet_poll"], "  tcp_v4_rcv": ["tcp_v4_rcv"], "  sock_def_readable (wake rx thr)": ["sock_def_readable", "sk_data_ready", "tcp_data_ready"],
 "  netif_receive_skb": ["__netif_receive_skb_core"], "  gro": ["napi_gro_receive", "dev_gro_receive", "gro_normal"],
 "idle exit / hrtimer": ["hrtimer_interrupt", "__sysvec_apic_timer_interrupt", "sysvec_apic_timer_interrupt"],
 "ioctl FIONREAD": ["__x64_sys_ioctl"], "write syscall": ["__x64_sys_write"], "clock_nanosleep": ["__x64_sys_clock_nanosleep"],
 "perf tracing overhead": ["perf_tp_event", "perf_trace_run_bpf_submit", "perf_swevent_hrtimer"],
}
p = subprocess.Popen(["perf", "script", "-i", f"{D}/all.data", "-F", "comm,pid,tid,cpu,ip,sym,dso", "--no-inline"], stdout=subprocess.PIPE, text=True, errors="replace", bufsize=1 << 20)
hre = re.compile(r"^\s*(\S.*?)\s+(\d+)/(\d+)\s+\[(\d+)\]")
cnt = collections.defaultdict(collections.Counter)  # role -> group -> n
tot = collections.Counter()
hdr = None; syms = []
def flush():
    if not hdr or hdr[1] not in pids:
        return
    r = role(hdr[0]); tot[r] += 1; tot["all"] += 1
    s = set(syms)
    for g, names in GROUPS.items():
        if any(n in s for n in names):
            cnt[r][g] += 1; cnt["all"][g] += 1
for line in p.stdout:
    if not line.strip():
        flush(); hdr = None; syms = []; continue
    if line[0] not in " \t":
        m = hre.match(line); hdr = (m.group(1), int(m.group(2))) if m else None; syms = []
    else:
        f = line.split()
        if len(f) >= 2:
            syms.append(f[1].split("+0x")[0])
flush()
cap = 499 * 60.0
print("share of collector CPU samples whose callchain contains the function (rx / wr / all collectors); cores of all collectors in last column")
print(f"samples: rx {tot['rx']} wr {tot['wr']} sk {tot['sk']} other {tot['other']} all {tot['all']}  (all = {tot['all']/cap:.2f} cores)")
for g in GROUPS:
    a, b, c = cnt["rx"][g], cnt["wr"][g], cnt["all"][g]
    print(f"{g:36s} rx {100*a/max(tot['rx'],1):5.1f}%  wr {100*b/max(tot['wr'],1):5.1f}%  all {100*c/max(tot['all'],1):5.1f}%  = {c/cap:5.2f} cores")
