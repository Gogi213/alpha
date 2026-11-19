# Critic 1: collector turbo plan (branch bb/g-022-collector-perf-v1 @ a4c81da3, docs/findings/collector-perf-2026-11-13.md)

**Verdict: ДОРАБОТАТЬ.** The measurements (§1) are solid and correctly scaled. The direction (no per-frame hand-off, one reactor, batched kernel crossings) is right. But the plan's central lever is mis-sized. Its batching window W = 0.5–1 ms limits reactor wakeups, while recv calls, ACKs and kernel crossings scale with **(socket, read) pairs**. At 31.3k frames/s over 146 sockets (≈ 214 frames/s/socket), a 1 ms window holds ≈ 0.2 frames per socket. S2 as written therefore cuts wakeups and leaves most recv/ACK cost in place. The plan also has no gate at all on the `rx` column and on which lane counts as the first arrival. In addition, S1 and S2 duplicate each other, and the checklist omits the clock and kernel-entry facts that explain this box's costs.

New facts checked on srv (read-only, 13.11 ≈ 18:30 GMT+4):
- **Clocksource and clock sync:** clocksource is `kvm-clock`, so vDSO works. chrony is stratum 2 via `ntp1.lwlcom.net`, offset ≈ 70–90 µs. There is no `/dev/ptp*`.
- **CPU mitigations:** active mitigations are `meltdown: PTI`, `mds/mmio/taa: Clear CPU buffers` (VERW on every kernel exit) and `spectre_v2: Retpolines`. On this Broadwell guest **every user↔kernel crossing is expensive**, so the number of crossings is the main cost driver, more than the work done inside them. This explains recvfrom ≈ 48 µs/call and futex ≈ 17 µs.
- **Buffers and busy-poll:** `tcp_rmem` max is 128 MB (autotuning on). busy_read/busy_poll are 0.

## Numbered items

### Floor model and ceiling (attack 1)

1. **Size W per socket, not per reactor. Decouple the timestamp from W.** Replace "W = 0.5–1 ms" with W = 5–10 ms, aligned to the exchange push cadence (depth@100ms, Bybit 10–100 ms, OKX books 100 ms). The `rx` column then takes the kernel stamp (item 9). With kernel stamps, W no longer affects data latency fidelity: the writer already flushes every 100 ms and `lag_out` p50 is 81–101 ms. So W can be large and buy real per-socket batching.
   - **Effect (estimate):** reads drop from ≈ 15.7k/s to ≈ 146 sockets × 10–30 bursts/s ≈ 1.5–4.5k/s. ACKs drop with them, because `tcp_cleanup_rbuf` ACKs once per read once more than rcv_mss is unacked: tx 14.6k pps → ≈ 2–5k pps.
   - **S2 at W = 1 ms:** −0.6…−0.9 cores (overstated in the plan). **At W = 5–10 ms:** −1.0…−1.4 (the plan's number becomes reachable).
   - **Verify:** on H0 and in a live window, measure reads/s, tx pps and `TcpExt.DelayedACKs` at W = 1, 5 and 10 ms.

2. **Make io_uring the main design, not S2b ("only if ≥ 0.15").** With PTI + VERW + retpolines, crossing count is the cost driver.
   - **Design:** multishot recv + provided-buffer ring + `IORING_SETUP_DEFER_TASKRUN | SINGLE_ISSUER` + `COOP_TASKRUN` gives **one `io_uring_enter` per W for all 30–50 sockets of a process**. epoll needs epoll_wait plus one recv per ready socket, plus a second recv to see EAGAIN. That is 2× the crossings unless you read with a large buffer and trust a short read.
   - **Effect (estimate):** ≈ 1.5–4.5k crossings/s → ≈ 400–800/s (4 procs × 100–200 wakes). Saves a further ≈ 0.1–0.25 cores beyond epoll.
   - **Stamps:** the kernel stamp travels with the recvmsg cmsg (`IORING_OP_RECVMSG` multishot supports cmsg). Check that `SO_TIMESTAMPNS` cmsg is delivered on multishot recvmsg on 6.12. One probe, 30 min.
   - **Fallback:** if the probe fails, epoll + `recvmsg` with a 64 KB buffer, and skip the EAGAIN-confirming second read when the read was short.

3. **Re-attribute softirq before claiming −0.2…−0.5 from S3.** Machine softirq 0.93 cores / 20.4k pps ≈ 45 µs/packet. That is ~10× normal virtio rx cost, so it includes:
   - the wakeup IPIs from `sock_def_readable → try_to_wake_up` (each IPI is a VM exit);
   - ACK tx kicks;
   - non-collector traffic (downloads, rsync).

   Once the reactor sleeps on one fd per process, most data arrivals find no waiter, so wakeups in softirq disappear. Expect softirq −40…−60 % from S2 alone, **before** any K1/K2 knob. Order S3 after S2 and re-measure the base; otherwise S2 and S3 double-count the same savings.
   - **Verify:** `/proc/softirqs` NET_RX and `/proc/stat` softirq split by CPU, plus IRQ/s, before and after S2, with the same collector set.

4. **Measure epoll busy-poll with IRQ deferral before rejecting it (K5).** The 87 %/core rejection was measured on **per-socket blocking recv with `SO_BUSY_POLL=3000 µs`**. A reactor that already wakes every W can do something different: set `EPIOCSPARAMS` (kernel ≥ 6.9, present on 6.12) with `busy_poll_usecs` 20–50, `prefer_busy_poll=1`, together with `napi_defer_hard_irqs=2..10` and `gro_flush_timeout` ≈ W. NAPI is then polled from the reactor's own wake instead of by IRQ, and IRQs (VM exits) mostly vanish.
   - **Caveats:** epoll busy-poll only covers one napi_id per epoll context, and 8 queues spread the sockets. Combine with **K1 (queues 8 → 1–2)** so all collector flows share 1–2 NAPI contexts.
   - **Effect (estimate):** IRQ 15.6k/s → < 2k/s; softirq/IRQ −0.1…−0.3 on top of S2.
   - **Verify:** one live window per setting.
   - **Do not mark busy-poll "rejected"** in §2.4. Mark it "per-socket blocking variant rejected; reactor variant K5 pending".

5. **Test fewer connections with more topics each, instead of dismissing it.** The §2.3 reason ("same frames/packets") is an assumption. Exchanges write a tick burst into one socket as consecutive WS frames, and the TCP sender coalesces them into ≥ 1 MSS segments. Today frames/packet ≈ 1.5 (31.3k frames over 20.4k pps). With 2–4× topics per connection, packets per frame (and IRQ, ACK, per-socket reads) should fall.
   - **Measure:** one 3-min window per exchange on a subset (e.g. Binance book shards merged 2→1), recording frames/packet and pps.
   - **Check per-exchange limits** (Binance 1024 streams/conn and its per-connection outgoing message limit). Lane symmetry is kept.
   - **Effect (estimate):** pps −20…−40 % if it holds. That is the only lever besides lanes on the "irreducible" 20.4k pps of §2.1.

6. **Add frame pointers / DWARF before Phase 3 to resolve the 0.29-core `[unknown]` user bucket.** Build with `-C force-frame-pointers=yes` (cost < 1 %) or record with `--call-graph dwarf` on one process. This 7 % bucket is currently unassignable, and the S2 estimate claims "part" of it without evidence.

7. **Revised ceiling (my estimate, measured shares).**

   | component | cores | basis |
   |---|---|---|
   | softirq + IRQ for 20.4k pps, with no wake IPIs, deferred IRQs and fewer ACKs | 0.15–0.30 | ≈ 8–15 µs/pkt incl. amortized ACK |
   | crossings: io_uring at ≤ 1k enters/s, reads/ACK amortized | 0.05–0.10 | |
   | user: TLS + WS | 0.08–0.12 | |
   | user: parse/dedup/encode with payload-hash prefilter (item 14) | 0.08–0.12 | |
   | user: zstd | 0.08 | |
   | user: gunzip | 0.02 | |
   | user: misc/timers/health | 0.05 | |
   | **total** | **0.55–0.8 (×5–7)** | without option D |

   With per-shard lane reduction (item 11 only where measured), ≈ 0.4. The plan's 1.1 is conservative **only if** items 1–3 are adopted. With W = 1 ms and epoll, its own 1.1 is optimistic (my estimate 1.3–1.6).

### S1/S2 estimates and missing risks (attack 2)

8. **Drop S1 as a production step and fold it into S2.**
   - **Duplicated work:** S1 builds a per-shard mutex shared by two rx threads, a design S2 deletes immediately. That is ≈ 3.5 h of code, gates and windows thrown away. One-change-at-a-time is still respected: S2 lands per exchange, one process at a time, with A/B windows.
   - **If S1 is kept** (fallback if S2 slips), its estimate is right: −0.55…−0.70. The writer's cold-cache cost (live 33–54 µs vs replay 3.3–7.3) **moves** into the rx threads in S1. It shrinks only when 519 threads collapse to ≈ 8–12 (S2), which is an additional S2 gain the plan does not count: ≈ 0.2–0.4 cores of the 1.19-core writer role.

9. **Add the reactor risks that are missing from §2.5:**
   - **(a) Head-of-line blocking.** Binance `mark00` is an 82 KB frame costing 2.1 ms (replay) per lane per second. In a single reactor it blocks all 50 sockets for 2 ms, twice a second. p99 `queue_lag` takes it, and so do BingX gunzip bursts.
     - **Fix:** payload-hash dedup before parse (item 14) halves this. Process frames > 16 KB on the zstd/aux thread via SPSC, or chunk the array parse.
     - **Gate:** add p99.9 reactor loop time ≤ 3 ms.
   - **(b) Fairness.** Use level-triggered readiness, or a per-socket read budget per turn (≤ 64 KB, then requeue). An edge-triggered drain loop lets a hot socket (OKX books, 10k frames/s per process) starve the others.
   - **(c) Lost-wakeup stalls** (the classic non-blocking rustls/tungstenite bug). Plaintext already decrypted inside rustls, or complete frames inside tungstenite's `ReadBuffer`, generate no new readiness event. Every turn must call `ws.read()` until `WouldBlock`, not just once per event.
     - **Test:** synthetic source that sends 50 WS frames in one TLS record, then goes silent for 10 s. All 50 frames must be emitted immediately.
     - Also a case where a TLS record is split across 3 TCP segments with 5 ms gaps.
   - **(d) Backpressure.** Today a bounded `sync_channel` blocks the rx thread, and TCP pushes back. In a reactor, a slow disk/zstd must **never** block the loop, and a drop is forbidden (В-172). Specify:
     - a preallocated ring of blocks (e.g. 256 × 1 MB);
     - when full, an extra allocated overflow block plus event `writer_backlog`;
     - a health counter;
     - a test with the disk thread paused for 30 s and zero loss.
   - **(e) Writes from the reactor.** Pong replies to exchange pings (BingX/OKX text pings) and WS pings go through rustls writes that may hit `WouldBlock`. These need write-interest handling and a test with a stalled peer receive window.
   - **(f) Reactor watchdog.** A heartbeat from the loop, read by a separate thread; a missed heartbeat for > 1 s emits an event. This turns "single point of failure" into a measured thing.

### Data fidelity (attack 3)

10. **"Byte-identical output" (G1) is necessary but not sufficient.**
    - **What G1 cannot see:** it runs on replay, which bypasses socket, TLS, WS framing, real gzip, lane interleaving and `rx`. G2 excludes `rx` explicitly. So **no gate covers the `rx` column or which lane counts as the first arrival**, and the reactor changes both.
    - **First-seen bias.** Within one W batch the reactor processes sockets in ready-list order. If lane B's frame arrived earlier but is processed after lane A's duplicate, "first seen" keeps A's later stamp. Dedup must keep the **minimum kernel stamp** across lanes, not the first processed. With W = 5–10 ms this matters.
    - **Add gate G4 (rx semantics), live shadow window.** For 30 min, on 2 shards per exchange, run old and new binaries side by side on separate connections. Compare:
      - (i) record multisets excluding `rx`: must be identical, modulo records only one side saw (count them);
      - (ii) distribution of `rx_new − rx_old`: expected p50 ≈ −100 µs with kernel stamps, and no positive tail beyond 1 ms;
      - (iii) per-lane `rx` monotonic non-decreasing.

      **Effect:** catches stamp-semantics regressions and dedup-winner bias that G1/G2/G3 cannot.
    - **Batch stamp semantics.** On TCP, `SO_TIMESTAMPNS` returns **one stamp per recvmsg, of the last skb read**. Frames earlier in the same read get a stamp up to W later than their true arrival, but still earlier than the user stamp. Either:
      - document `rx` = "guest-kernel arrival of the last segment of the read containing the frame (error ≤ W, one-sided late)"; or
      - cap per-socket read size so a read rarely spans two exchange bursts.

      Record per-frame `rx_batch_span` in health, not in the file.

### Decisions (attack 4)

11. **(a) `rx` column.** Use the **kernel stamp in the file** (it removes 114 µs p50 / 0.5 ms p99 / 1.5 ms p99.9 / 9–16 ms max of scheduler noise, and it is W-independent). Put the user stamp and the kernel→thread histogram in health only. Writing both stamps to the file is a format change that buys nothing once G4 exists. Caveats:
    - **Absolute accuracy is bounded by the clock:** chrony offset ≈ 70–90 µs, stratum 2, no PHC. Without a better clock, the 50–500 µs gain is real for **relative/jitter** analysis (cross-exchange lead-lag inside srv), not for absolute exchange-to-srv latency.
    - **Add to the checklist:** log chrony offset and root dispersion into health every 10 s. Try `modprobe ptp_kvm` and a chrony PHC refclock, if the host exposes it, for µs-level guest sync.

    **(b) Option D, lane 2 as warm standby: reject as a blanket policy; decide per shard on a measured "unique contribution" metric.** Reasons:
    - **The 17:43 stall does not argue for lane 2.** It was srv-wide (all 4 exchanges, same minute, before the guest NIC), and lane 2 shares the host and NIC, so it stalled too. Lane 2 protects only against **per-connection/per-server** degradation at the exchange (slow_pong, single-path stalls) and gives min-of-two arrival latency.
    - **Lanes diverge in content:** Bybit mark ~10 % duplicates, BingX book03 23 %, Bybit +58 % book states (turbo report §3). A standby loses that data, which violates В-172.
    - **Failover is lossy:** subscribe on degradation costs ≥ 1 RTT + snapshot + the 3 s detection threshold, i.e. seconds of loss per event.

    **Concrete step:** from the existing 24 h files, compute per shard:
    - (i) the share of records lane 2 delivered that lane 1 never did;
    - (ii) the share where lane 2 was first by > 1 ms;
    - (iii) seconds where lane 1 was silent > 1 s and lane 2 was not.

    Standby only where all three ≈ 0 (likely Binance/OKX book shards with exactly 50 % duplicates). Expected saving ≈ 0.1–0.2 cores after items 1–3, not the ×2 the plan implies. Lane cost shrinks once per-frame cost shrinks.

### HFT checklist (attack 5)

12. **Missing rows to add:**
    - (i) **clock quality:** chrony offset/dispersion in health, PHC attempt (item 11a);
    - (ii) **kernel-crossing budget:** crossings per frame, target ≤ 0.05 (now ≈ 2.9: recv 0.5 + futex 1.07 + switches 1.24 + other). This is the metric that matters on a PTI/VERW guest;
    - (iii) **reactor loop time p99/p99.9** and a reactor heartbeat;
    - (iv) **TCP drop counters per process/host** in health: `TCPRcvQDrop`, `TCPBacklogDrop`, `PruneCalled`, `TCPOFODrop`. Larger W keeps more bytes in socket buffers; autotuning up to 128 MB is fine, but prove it;
    - (v) **dedup winner = minimum kernel stamp** (item 10);
    - (vi) **backpressure without loss** (item 9d);
    - (vii) **symbols for the whole hot path** (item 6).

13. **Rows wrongly marked or over-weighted:**
    - "**busy-poll rejected**": only the per-socket variant (item 4).
    - "**lock-free SPSC**": after S2, the only cross-thread hand-off is one 100 ms block to zstd. A mutex there costs nothing measurable. Mark the requirement as "no per-frame cross-thread hand-off", which is the real HFT property, and avoid building a lock-free ring for ~10 handoffs/s.
    - "**hugepages/prefault**": the working set is a few MB; expect no measurable gain. Downgrade to "MAP_POPULATE on the block ring only; no THP work". Do not spend hours on it.
    - "**pinned threads**": inside a KVM guest, vCPUs float on host pCPUs. Pinning cuts guest migrations (13.8k/s now, mostly from 519 threads) but not host-level jitter. Thread count reduction (S2) does most of it; keep K3 as a cheap follow-up.
    - **Spectre/PTI mitigations** belong in the checklist as a *known cost* (documented, not changed; `mitigations=off` is a security decision on a shared box, out of scope).

### Smaller plan items

14. **S4 dedup prefilter: use a hash of the raw payload bytes (xxh3-128 + length, ≈ 20–40 ns for a 200 B frame) against a small per-shard ring of recent hashes from the other lane, instead of a byte-search key extractor.**
    - **Fallback:** a hash miss falls through to full parse, so it is safe whenever lanes differ byte-wise.
    - **Why:** simpler, and it covers the 82 KB mark arrays (−2.1 ms per duplicate).
    - **Effect:** the plan's −0.13 from halving parse, plus the HOL fix in 9a.
    - **Gate:** G1 on 16 captures, plus a counter of hash-hit records whose parsed identity would differ (must be 0, checked in replay with both paths).

15. **S0b: first try reusing a `miniz_oxide` `InflateState` per session (`reset`) before adding libdeflate.** `init_tree` is 34 % of BingX rx-user, which suggests per-frame state allocation and initialization. A no-dependency change may yield ≈ ×1.5–2. Then compare with libdeflate on the same microreplay.

16. **S0 accounting:** PGO/LTO's 10–20 % applies to user compute that S2/S4 shrink from 1.8 to ≈ 0.4. Its absolute value after the other steps is ≈ 0.04–0.08, not 0.11–0.22. Keep it first (cheap), but report its gain re-measured at the end.

17. **Gate threshold "p99 worse by 1 ms" breaks under W = 5–10 ms** if it is evaluated on user-stamp `lag_in`. Evaluate latency gates on kernel stamps, plus a separate budget: `queue_lag` p99 ≤ W + 3 ms.

18. **Measurement hygiene:** srv runs at PSI cpu 27–32 % and load ≈ 10. Freeing ≈ 3 cores lowers contention, so later steps will look better than their isolated effect (nonlinear). Attribute per step with alternating old/new windows (already planned), and also record srv load/PSI per window to flag confounded windows.

## Recommended ceiling

≈ **0.6–0.8 cores for all 4 collectors (×5–7) without option D**, from:
- kernel-stamped `rx`, which allows a burst-aligned W of 5–10 ms;
- an io_uring reactor at ≤ 1k kernel entries/s per host, with no wake IPIs;
- reads and ACKs per (socket, burst), not per packet;
- K1 + K5 IRQ deferral;
- the payload-hash dedup prefilter.

Possibly ≈ 0.5 if fewer-connections (item 5) confirms −20…−40 % pps. ≈ 0.4 with lane 2 removed only on shards measured as non-contributing. The irreducible part is ≈ 0.15–0.3 cores of guest packet processing for ≈ 20k pps, and it falls only with fewer packets (item 5, lanes).
