# Critic 2 — collector turbo plan (branch bb/g-022-collector-perf-v1 @ a4c81da3)

**Verdict: ДОРАБОТАТЬ.** The measurements in §1 are solid and the direction of the plan is right: kill the per-frame OS interaction. But the floor in §2.1 has three problems. (1) It mixes two accounting bases. (2) It assumes ACK/recv batching that cannot happen at the measured per-socket rate with W ≤ 1 ms. (3) It assumes compute runs at replay (hot-cache) speed. On top of that, the biggest kernel lever (per-packet IRQ/NAPI cost on virtio) is placed last and under-estimated. Two kinds of rx-path change are not covered by the gates: rx-stamp changes and dedup-order changes.

Basis used below: per-socket rate = 20.4k pkt/s ÷ 146 conns ≈ **140 pkt/s/socket** (≈ 214 frames/s). NET_RX softirqs 27.7k/s against 20.4k pkt/s and 15.6k rx-IRQ/s gives **≈ 0.74 packets per NAPI run**, i.e. zero batching today (`p1/proc_delta.txt`). Machine softirq 0.93 cores ÷ 20.4k pkt/s ≈ **45 µs per packet**. That is far above bare metal, so the per-packet cost is dominated by IRQ/exit overhead.

## A. Floor model (§2.1) and ceiling

1. **The baseline and the floor are on different bases.** The 4.00 cores is process CPU from `/proc`. It excludes the ≈ 0.61 cores of softirq that runs in idle context (0.93 machine − 0.32 in collector threads). The floor row "kernel receive 0.4–0.7" includes it. A design that sleeps more (reactor, W window) moves still more softirq into idle context, where it disappears from process CPU. **Gain measured by process CPU will be inflated.**
   **Change:** baseline = 4.00 + 0.61 ≈ **4.6 cores, machine-wide**. Every gate measures (collector cgroup `cpu.stat`) + (total `/proc/stat` softirq + irq), plus `steal` reported alongside.
   **Effect:** honest ratios. Without this, S2 could show "−1.4" that is partly accounting.

2. **ACK/recv batching in S2 does not happen at W = 0.5–1 ms.** At 140 pkt/s per socket, the expected number of packets per socket per 1 ms window is 0.14. About 93 % of reads stay single-packet, so recv count and ACK count (`tcp_cleanup_rbuf`, ICSK_ACK_PUSHED on drain) barely change. The reactor only removes the sleep/wake part of recvfrom (39 % ≈ 0.29 cores) and thread-timer churn.
   **Change:** re-estimate S2:
   - recvfrom 0.75 → ≈ 0.45
   - other syscalls 0.33 → ≈ 0.10
   - irq/sched 0.22 → ≈ 0.12
   - rx loop + [unknown] part −0.1

   That gives **S2 ≈ −0.6…−0.9, not −1.0…−1.4**. Fewer ACKs need either larger per-socket W (see C) or item A6.
   **Effect:** the plan stops promising a number it cannot deliver.

3. **Compute floor assumes replay speed.** In production the writer chain costs 33–54 µs/frame vs 3.3–7.3 µs in replay. A reactor interleaving about 50 sockets × TLS state × dedup tables will not reach replay speed. Assume ×1.5–2 of replay, so **user floor ≈ 0.6–0.8 cores, not 0.45–0.7**.
   **Change:** before S0, add a "replay interleaved" mode: 16 shards round-robin in one thread, so the cache footprint is realistic. That mode becomes the compute gate.

4. **Kernel lever order and size (K1/K2).** Zero NAPI batching (0.74 pkt per run) at ≈ 45 µs/packet means the IRQ/EOI/vmexit overhead is the largest single item.
   **Change:** move K1 + K2 + K3 **to the front**, right after H0/S6 instrumentation. They need no build, are reversible in seconds and cost about 2 h of windows.
   - Settings: `ethtool -L eth0 combined 2`, `napi_defer_hard_irqs=2`, `gro_flush_timeout=100–200 µs`, IRQs pinned to 2 vCPUs.
   - Expected: about 8–10 pkts per poll, rx-IRQs 15.6k → ≈ 2–3k/s, machine softirq 0.93 → **0.35–0.5 (−0.45…−0.6)**. That is larger than the plan's −0.2…−0.5. It is an assumption; K-window measures it in 3 min.
   - It also takes softirq off the 6 vCPUs used by counting jobs (PSI 27–32 %).
   - Cap `gro_flush_timeout` at ≤ 200 µs: the kernel RX stamp is taken after deferral, so it shifts by up to that value (see C3).

5. **Steering coherence (new K3').** vhost/tun selects the rx queue of a flow by the queue the guest last transmitted that flow on (tun automatic flow steering). Set XPS cpu k → txq k (check `xps_cpus`) and pin reactor k to cpu k. Each reactor's ACKs then go out queue k, so its flows' RX lands on queue k, whose IRQ is on cpu k. Softirq, wakeup and reactor then share one CPU: no IPIs, warm cache, migrations → 0.
   **Change:** in K3, verify with `/proc/interrupts` per queue against the reactor CPU.
   **Effect:** removes most of `try_to_wake_up`/`select_task_rq` (≈ 6.7 % incl.); also a precondition for A7.

6. **ACK experiment (new K5).** `setsockopt(TCP_QUICKACK, 0)` enters pingpong mode. Drain-triggered ACKs are then suppressed except on ACK_PUSHED2, 2×MSS, or the delack timer. The timer exits pingpong, so the option must be re-armed every N reads per socket, never per read.
   - Expected tx 14.6k → ≈ 7–9k pkt/s, ≈ −0.1…−0.2 cores (ACK-in-recv is 0.22 cores ≈ 15 µs/ACK).
   - Risk: the sender's RTT/RTO estimate grows, so TLP/RTO recovery is slower on a path with 19–22 retrans/s. Fast retransmit is unaffected, because OOO data still gets immediate dupacks.
   - Gate: retransmits/s, `gap_end`, and kernel→thread p99 unchanged.

7. **App-driven NAPI (new S3c, experimental).** The "busy-poll rejected" verdict covers only blocking per-socket `SO_BUSY_POLL=3000`. It does not cover epoll + `SO_PREFER_BUSY_POLL` + small `busy_poll_budget` + `napi_defer_hard_irqs` + `gro_flush_timeout` as a safety net.
   - How it works: the reactor's W-tick polls its own NAPI (one NAPI id per reactor thanks to A5). IRQs stay masked while the app keeps polling, and packet processing runs in the reactor's context in batches.
   - Expected: rx-IRQs → near 0, softirq/packet down further. This can only be judged by measurement.
   - Kernel 6.12 lacks `irq_suspend_timeout` (6.13+), so use gro_flush_timeout as the fallback.
   - Do it only after A4/A5 and only if machine kernel cost after S2 is still ≥ 0.6.
   - In §2.4, mark busy-poll as "variant not measured", not "rejected".

8. **Fewer connections, more streams each (do not dismiss).** "Packets are the same" is wrong in two places:
   - Frames per packet rise when the exchange coalesces writes on a busier connection: 1.5 frames/pkt today.
   - Packets per read per W rise with per-socket rate, and that is what drives ACK and recv batching (A2).

   **Change:** one 3-min window per exchange with the shard count halved, measuring frames/pkt, pkts/read, CPU/Mframe and exchange-side drops. Limits: Binance allows 1024 streams/conn; per-connection limits are in the prep reports.
   **Effect:** unknown sign, possibly the cheapest −20…−40 % of kernel cost. Fidelity risk: exchange-side conflation on heavy connections. Gate: G3 REST oracle.

9. **Unresolved 0.29 cores `[unknown]`** (BingX 0.17). Resolve it before committing numbers: build with `-C force-frame-pointers=yes` plus libc debuginfo, or use `perf --call-graph dwarf` on one process.
   **Effect:** 7 % of the budget is currently unplanned.

10. **Guest mitigations.** On Broadwell, MDS VERW on every kernel exit, plus retbleed/IBRS-class mitigations, costs per syscall and per IRQ. At about 80k syscalls/s + 15.6k IRQ/s this may be 0.05–0.2 cores.
    **Change:** read `/sys/devices/system/cpu/vulnerabilities/*` and record it in §1.5. `mitigations=off` on this single-tenant VM is an **owner decision** (security trade-off). Only state the measured cost.

**My ceiling.** Machine-wide basis 4.6 cores, both lanes kept, W ≤ 1 ms, kernel stamps.

| step | gain, cores |
|---|---|
| K1/K2/K3' | −0.45…−0.6 |
| S0b | −0.2 |
| S0 | −0.05…−0.15 |
| batched hand-off | −0.55…−0.65 |
| reactor | −0.6…−0.9 |
| S4 | −0.1…−0.2 |
| K5 | −0.05…−0.2 |

Result: **≈ 1.5–2.1 cores (×2.2–3)**, committed range. The stretch case adds W = 5 ms with kernel stamps (owner accepts C3 coarsening), app-driven NAPI (A7) and fewer connections (A8). That gives **≈ 1.0–1.3 (×3.5–4.6)**. ×10 (≈ 0.46) is not reachable while lane 2 is kept. The plan's "1.1 (×3–4.4)" is the stretch case presented as the base case.

## B. S1/S2 estimates and missing risks

11. **S1 is throw-away work.** S2 puts both lanes in one reactor, which makes S1's cross-thread mutex obsolete.
    **Change:** replace S1 with **S1': SPSC ring of fixed blocks, reader → shard worker, with the worker timer-driven every W (no futex per frame)**. This kills futex 0.57 + park 0.13 (≈ −0.6). It keeps the "no parse in reader" principle, and it is exactly the reactor → worker interface S2 needs anyway.
    **Effect:** same gain, built once, no mutex-collision analysis.

12. **Head-of-line blocking in a single reactor.**
    - Binance `mark00` frames cost **2.1 ms each** (82 KB array, replay table). Parsed inline in a reactor, each one stalls all 50 sockets by 2 ms, 1–3 times per second.
    - Bybit trade bursts are 21.7 µs/frame.
    - PSI 27–32 % means a single reactor thread will sit in the run-queue, and every socket waits.

    **Change:** the reactor does only recv + TLS + WS framing + stamp. Parsing, dedup and encoding go to the worker from item 11. Frames > 16 KB always go to the worker. Collectors run in their own systemd slice with a higher `CPUWeight` (not SCHED_FIFO).
    **Effect:** kernel→thread p99 independent of payload mix.

13. **Non-blocking rustls/tungstenite doubles recv calls if drained to EAGAIN.** A loop that reads until WouldBlock issues one EAGAIN recv per readiness, which can double the 15.7k/s recv rate.
    **Change:** level-triggered epoll, **one recv per ready socket per tick** into a 64 KB buffer fed to `read_tls`. Never drain to EAGAIN; level-triggering re-reports the socket.
    - Writes (ping, pong replies, subscribe) can return WouldBlock with a partial frame. That needs `EPOLLOUT` interest and a `flush` retry. The current design has no such path, so test it explicitly.

14. **Fairness.** With level-triggered epoll and one read per socket per tick, fairness is automatic. Cap `maxevents` at ≥ the socket count so no socket is starved past one tick. With W = 1 ms, a reactor stall of 100 ms (steal/PSI) needs `SO_RCVBUF` ≥ peak per-socket rate × 0.1 s. Autotuning covers this, but assert `recvq_max` in the health page.

15. **Per-frame shared atomics (false sharing).** `m.frames`, `m.bytes`, `last_frame_ms`, `last_frame_mono_ms` cost four atomic RMW/stores per frame. They sit on lines shared between two lane threads and the health reader (`session.rs:427-433`).
    **Change:** in the reactor, use thread-local counters published once per tick.
    **Effect:** small in cycles, but it removes cross-core cache traffic that does not show in perf symbols.

16. **Two clock reads per frame** (`wall_ns()` + `Instant::now()`, `session.rs:267-268`). With kernel stamps (C), use one monotonic read per tick.

17. **Backpressure is not specified.** Today a failed `ring.push` returns false (the session ends?).
    **Change:** state the policy for a full SPSC ring: block-count headroom ≥ 1 s at peak, and a counter that must stay 0. Make it a failure condition of every step.

18. **Latency gate "p99 lag_in worse by 1 ms" will flap.** `lag_in` includes the exchange clock offset and the WAN path; window-to-window p99 noise is likely > 1 ms.
    **Change:** gate on the **kernel→thread** and **thread→block** stages (S5/S6), on paired, interleaved windows. Move S5/S6 instrumentation **before** the first optimisation step, so every later step has a usable latency gate.

## C. Data fidelity and rx semantics

19. **G1 (byte-identical replay) is necessary but not sufficient.** Replay takes rx from the capture, so it cannot see changes in:
    - stamp assignment
    - partial TLS-record / WS-frame handling on a non-blocking socket
    - lane ordering inside dedup
    - reconnect, resubscribe, snapshot

    **Change:** add three gates.
    - **G4, stamp gate on H0:** `replayd` sends with recorded send times. Check the distribution of rx − send, monotonicity per socket, and that a dedup record keeps the **minimum** stamp across lanes.
    - **G5, boundary fuzz on H0:** 1-byte TCP writes, 16 KB TLS records, an 82 KB frame split over ~60 segments, WS continuation frames, a ping in the middle of a large frame, gzip frames split across records.
    - **G6, shadow:** old and new binary on the same 2 shards per exchange concurrently for 1 h. Compare record multisets excluding rx, plus the rx difference distribution. This catches live-only paths. IP limits allow it at the 4-shard subset scale already used for capture.

20. **Dedup must keep the earliest arrival, not the first one processed.** In a batched reactor, both lane copies of a record can arrive within one tick. The order of processing is then epoll order, not arrival order, so with "first wins" the kept record can carry the later lane's rx.
    **Change:** per tick, sort the reads by kernel stamp before handing them to dedup (≤ 150 entries, negligible). Or make dedup keep min(rx) and emit only once the tick is closed. Covered by G4.

21. **Kernel RX stamp semantics are per read, not per frame.**
    - TCP `recvmsg` returns one timestamp: the newest skb consumed. GRO and `tcp_try_coalesce` also propagate the newest skb timestamp.
    - So frames delivered by one read share one stamp: the arrival of the last segment in that read. Earlier frames in the batch get a later stamp, by at most W.
    - With K2, the stamp is also delayed by ≤ `gro_flush_timeout`, because it is taken at `netif_receive_skb`, after deferral.

    This is still strictly better than today's user stamp. Today, frames from one read get increasing stamps 0.1–1 µs apart that reflect parse time, not arrival: false precision. Today's user stamp also includes p50 114 µs / p99.9 1.5 ms / max 16 ms of wake-up latency.
    **Change:**
    - Document the exact semantics.
    - Add a health counter for "frames sharing a stamp".
    - Keep W ≤ 1 ms and `gro_flush_timeout` ≤ 200 µs unless the owner accepts coarser stamps for the stretch gains (A, ceiling).

22. **Semantics switch inside a continuous dataset.** Files before and after the change differ in rx by about 0.1–0.5 ms median.
    **Change:** add a dated switchover entry (A1-style decision plus an entry in the dataset docs). If the file format has a header or manifest field, add a stamp-source tag there. Do not leave it to memory. If adding a field is a format change under В-172, put it in the day manifest instead.

## D. Decisions requested

**(a) `rx` column: kernel RX stamp.** Do not write both stamps to the file: that is a format change, and the user stamp adds no information beyond a latency diagnostic. Put the user − kernel delta into the health histogram (S6) only.
Reasons:
- The kernel stamp is independent of W, of reactor scheduling, of PSI and of the 2 ms mark-frame parse. The user stamp carries all of them, and its p99.9 is 1.2–1.5 ms with a max of 16 ms (probe).
- It is the only stamp under which batching optimisations do not degrade data fidelity.

Conditions:
- C20 (min-stamp dedup) and C21/C22 (documented per-read semantics, dated switchover).
- Check the clock: chrony offset/jitter and clocksource `kvm-clock` stable; add chrony offset to health and alert if > 1 ms. Without it, neither stamp is meaningful at 100 µs scale.

**(b) Option D, lane 2 as warm standby: reject as a blanket switch.** Reasons:
1. Lane 2 is not a pure duplicate. Bybit mark dup is 10 %, BingX book03 23 %, and Bybit lane 2 adds +58 % book states. Dropping it deletes exchange data (В-172 «данные как на бирже»).
2. The 17:43 stall was srv-wide: all four exchanges and both lanes of each shard (shard-level `gap_end`). A standby lane on the same NIC/host protects nothing against that class of event.
3. Against per-connection or edge-server stalls, standby is worse than a live lane. Subscribe + snapshot after detecting degradation is a hole of seconds, while a live lane has none.
4. After the plan, lane 2 costs ≈ 0.4–0.6 cores. That is a small price for a full-redundancy, exchange-faithful record.

Data-driven alternative: add a per-stream metric "records contributed only by lane 2 per day" (counted in the writer's dedup). Revisit per stream, not per exchange, after 7 days of data. Show the owner the numbers; the decision stays the owner's.

## E. HFT checklist (§2.4) — corrections and missing rows

23. Corrections:
    - **Writer zero alloc: "выполнено" → "почти":** 0.002–0.005 per frame is not 0. On the rx path, `Bytes::from(String)` runs per frame (`session.rs:275`) and `Bytes::copy_from_slice` per gzip frame (`session.rs:373`).
    - **Busy-poll: "отклонён" → "blocking variant rejected; epoll prefer-busy-poll variant not measured" (A7).**
    - **Hugepages/THP on dedup tables:** expected gain ≈ 0. Downgrade it and do `mlockall(MCL_CURRENT|MCL_FUTURE)` after warm-up instead (no major faults; cheap).
24. Missing rows:
    - clock discipline (chrony offset/jitter in health, clocksource, step handling)
    - CPU isolation from counting jobs (collector slice + `CPUWeight`, IRQ/reactor vCPU set; steal and PSI per window)
    - NAPI batching (pkts per poll ≥ 5, rx-IRQ/s)
    - ACKs per received packet
    - syscalls per frame target (≤ 0.3)
    - context switches per frame target (≤ 0.05)
    - no shared-line atomics per frame
    - explicit backpressure counter = 0
    - reactor watchdog heartbeat (stall > 50 ms → event)
    - dedup keeps min stamp
    - guest mitigations audited
    - unresolved profile share < 2 %

## Recommended order

H0 (+ G4/G5) → S5/S6 instrumentation (stage latencies, kernel stamps) → K1/K2/K3' windows (no build) → S0b → S0 → S1' (SPSC batched hand-off) → S2 reactor (recv + TLS + WS only, one read per socket per tick, stamp-sorted dedup feed) → S4 → K5 ACK experiment → A8 fewer-connections window → S3c / S2b only if kernel ≥ 0.6 cores remains. G6 shadow before each production rollout.
