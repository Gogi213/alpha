//! Метки прихода пакетов из AF_PACKET (tpacket_v3): точное время прихода каждого пакета биржи, независимо от окна реактора и от склейки очереди приёма ядром.
//!
//! Зачем (collector-perf, G4): метка `SO_TIMESTAMPNS` у `recvmsg` — одна на чтение, у самого нового пакета, а нечитанные пакеты ядро склеивает в один skb
//! (`tcp_try_coalesce`) и метки ранних теряет; при окне пакетирования в миллисекунды ранние кадры чтения получали бы метку позже настоящей на длину окна.
//! Здесь пакеты биржи (IPv4, TCP, порт источника из набора) читаются из кольца mmap без системных вызовов, для каждого потока ведётся список
//! (начало, конец в потоке, время): смещение в потоке = `seq` − (ISN + 1), ISN берётся из SYN-ACK. Запись TLS занимает байты `[a, b)` потока (`RxSock::last_record`),
//! её метка — время последнего из пакетов, дополнивших эти байты (при повторной доставке — самого раннего экземпляра каждого куска).
//! Кольцо читает поток реактора раз в такт; расходы — копирование заголовков (`SNAP` байт) ядром при приёме пакета и разбор в реакторе (замер: ≈ 0,02 ядра на 58 тыс. пак./с).
//! Нет CAP_NET_RAW, нет кольца или пакет потерян — метки нет (`None`): реактор берёт метку чтения (`SO_TIMESTAMPNS`).

use std::collections::{HashMap, VecDeque};
use std::io;
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};

const SNAP: u32 = 96;
const BLOCK: usize = 1 << 20;
const BLOCKS: usize = 8;
const FRAME: usize = 2048;
/// Сколько секунд держать пакеты потока, если по ним не спрашивали (потоки без запросов — чужие или закрытые).
const KEEP_NS: i64 = 15_000_000_000;
const MAX_FLOWS: usize = 4096;

/// Поток биржи: адрес и порт источника, порт у нас.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct Key {
    pub remote_ip: u32,
    pub remote_port: u16,
    pub local_port: u16,
}

struct Pkt {
    start: u64,
    end: u64,
    t: i64,
}

struct Flow {
    /// Номер первого байта данных (ISN + 1).
    base: u32,
    /// Наибольший конец принятых байт в потоке (для развёртки 32-битного `seq`).
    max_end: u64,
    pkts: VecDeque<Pkt>,
    last_t: i64,
}

/// Счётчики кольца.
#[derive(Clone, Copy, Debug, Default)]
pub struct TapStats {
    pub packets: u64,
    pub data_packets: u64,
    pub lookups: u64,
    pub hits: u64,
    pub drops: u64,
}

pub struct Tap {
    fd: OwnedFd,
    ring: *mut u8,
    cur: usize,
    flows: HashMap<Key, Flow>,
    pub stats: TapStats,
}

// SAFETY: кольцо принадлежит одному потоку (реактору), который создаёт и использует `Tap`; указатель — на mmap, живущий, пока жив `Tap`.
unsafe impl Send for Tap {}

#[repr(C)]
struct Req3 {
    block_size: u32,
    block_nr: u32,
    frame_size: u32,
    frame_nr: u32,
    retire_blk_tov: u32,
    sizeof_priv: u32,
    feature_req_word: u32,
}

#[repr(C)]
struct SockFilter {
    code: u16,
    jt: u8,
    jf: u8,
    k: u32,
}

#[repr(C)]
struct SockFprog {
    len: u16,
    filter: *const SockFilter,
}

fn insn(code: u16, jt: u8, jf: u8, k: u32) -> SockFilter {
    SockFilter { code, jt, jf, k }
}

/// Классический BPF: IPv4, TCP, не фрагмент, порт источника из набора → `SNAP` байт; иначе 0.
fn program(ports: &[u16]) -> Vec<SockFilter> {
    const LD_H_ABS: u16 = 0x28;
    const LD_B_ABS: u16 = 0x30;
    const LDX_MSH: u16 = 0xb1;
    const LD_H_IND: u16 = 0x48;
    const JEQ: u16 = 0x15;
    const JSET: u16 = 0x45;
    const RET: u16 = 0x06;
    let n = ports.len();
    // индексы: 0..=7 — разбор заголовков, 8..8+n — сравнения портов, 8+n — отброс, 9+n — оставить; переход — от следующей команды
    let drop_at = 8 + n;
    let to_drop = |from: usize| u8::try_from(drop_at - from - 1).unwrap_or(u8::MAX);
    let mut p = vec![
        insn(LD_H_ABS, 0, 0, 12),
        insn(JEQ, 0, to_drop(1), 0x0800),
        insn(LD_B_ABS, 0, 0, 23),
        insn(JEQ, 0, to_drop(3), 6),
        insn(LD_H_ABS, 0, 0, 20),
        insn(JSET, to_drop(5), 0, 0x1fff),
        insn(LDX_MSH, 0, 0, 14),
        insn(LD_H_IND, 0, 0, 14),
    ];
    for (i, port) in ports.iter().enumerate() {
        p.push(insn(
            JEQ,
            u8::try_from(n - i).unwrap_or(u8::MAX),
            0,
            u32::from(*port),
        ));
    }
    p.push(insn(RET, 0, 0, 0));
    p.push(insn(RET, 0, 0, SNAP));
    p
}

fn be16(b: &[u8], i: usize) -> Option<u16> {
    Some(u16::from_be_bytes([*b.get(i)?, *b.get(i + 1)?]))
}

fn be32(b: &[u8], i: usize) -> Option<u32> {
    Some(u32::from_be_bytes([
        *b.get(i)?,
        *b.get(i + 1)?,
        *b.get(i + 2)?,
        *b.get(i + 3)?,
    ]))
}

impl Tap {
    /// Открыть кольцо; `ports` — порты источника (443 биржи, при проверках — порты локального источника).
    pub fn open(ports: &[u16]) -> io::Result<Self> {
        // SAFETY: socket без указателей; результат проверяется.
        let raw = unsafe {
            libc::socket(
                libc::AF_PACKET,
                libc::SOCK_RAW,
                i32::from((libc::ETH_P_ALL as u16).to_be()),
            )
        };
        if raw < 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: `raw` только что создан и принадлежит нам.
        let fd = unsafe { OwnedFd::from_raw_fd(raw) };
        let set = |level: i32, name: i32, val: *const libc::c_void, len: usize| -> io::Result<()> {
            // SAFETY: fd живой; `val` — указатель на живую структуру длиной `len`.
            let rc = unsafe {
                libc::setsockopt(
                    fd.as_raw_fd(),
                    level,
                    name,
                    val,
                    libc::socklen_t::try_from(len).unwrap_or(0),
                )
            };
            if rc == 0 {
                Ok(())
            } else {
                Err(io::Error::last_os_error())
            }
        };
        let prog = program(ports);
        let fprog = SockFprog {
            len: u16::try_from(prog.len()).unwrap_or(0),
            filter: prog.as_ptr(),
        };
        set(
            libc::SOL_SOCKET,
            libc::SO_ATTACH_FILTER,
            std::ptr::from_ref(&fprog).cast(),
            std::mem::size_of::<SockFprog>(),
        )?;
        let ver: libc::c_int = 2; // TPACKET_V3
        set(
            libc::SOL_PACKET,
            libc::PACKET_VERSION,
            std::ptr::from_ref(&ver).cast(),
            4,
        )?;
        let req = Req3 {
            block_size: BLOCK as u32,
            block_nr: BLOCKS as u32,
            frame_size: FRAME as u32,
            frame_nr: (BLOCK / FRAME * BLOCKS) as u32,
            retire_blk_tov: 1,
            sizeof_priv: 0,
            feature_req_word: 0,
        };
        set(
            libc::SOL_PACKET,
            libc::PACKET_RX_RING,
            std::ptr::from_ref(&req).cast(),
            std::mem::size_of::<Req3>(),
        )?;
        // SAFETY: отображение кольца, размер — block_size × block_nr, как заказано выше; результат проверяется.
        let ring = unsafe {
            libc::mmap(
                std::ptr::null_mut(),
                BLOCK * BLOCKS,
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED,
                fd.as_raw_fd(),
                0,
            )
        };
        if ring == libc::MAP_FAILED {
            return Err(io::Error::last_os_error());
        }
        Ok(Self {
            fd,
            ring: ring.cast(),
            cur: 0,
            flows: HashMap::new(),
            stats: TapStats::default(),
        })
    }

    /// Прочитать накопленные блоки кольца и обновить списки пакетов потоков.
    pub fn poll(&mut self) {
        loop {
            // SAFETY: `cur` < BLOCKS, блок целиком внутри отображения.
            let bd = unsafe { self.ring.add(self.cur * BLOCK) };
            // SAFETY: слово статуса блока на смещении 8 заголовка блока; ядро пишет его с release, читаем с acquire.
            let status = unsafe {
                (*(bd.add(8).cast::<std::sync::atomic::AtomicU32>()))
                    .load(std::sync::atomic::Ordering::Acquire)
            };
            if status & 1 == 0 {
                break; // TP_STATUS_USER не выставлен
            }
            // SAFETY: поля заголовка блока (числа пакетов и смещение первого) — по смещениям ABI tpacket_block_desc.
            let (n, first) = unsafe {
                (
                    bd.add(12).cast::<u32>().read_unaligned(),
                    bd.add(16).cast::<u32>().read_unaligned(),
                )
            };
            let mut off = first as usize;
            for _ in 0..n {
                if off + 28 > BLOCK {
                    break;
                }
                // SAFETY: заголовок пакета tpacket3_hdr внутри блока; поля по смещениям ABI.
                let (next, sec, nsec, snaplen, mac) = unsafe {
                    let h = bd.add(off);
                    (
                        h.cast::<u32>().read_unaligned() as usize,
                        h.add(4).cast::<u32>().read_unaligned(),
                        h.add(8).cast::<u32>().read_unaligned(),
                        h.add(12).cast::<u32>().read_unaligned() as usize,
                        h.add(24).cast::<u16>().read_unaligned() as usize,
                    )
                };
                if off + mac + snaplen <= BLOCK {
                    // SAFETY: данные пакета — `snaplen` байт со смещения `mac` от заголовка, внутри блока.
                    let data = unsafe { std::slice::from_raw_parts(bd.add(off + mac), snaplen) };
                    let t = i64::from(sec) * 1_000_000_000 + i64::from(nsec);
                    self.ingest(data, t);
                }
                if next == 0 {
                    break;
                }
                off += next;
            }
            // SAFETY: блок возвращается ядру: статус TP_STATUS_KERNEL (0) с release.
            unsafe {
                (*(bd.add(8).cast::<std::sync::atomic::AtomicU32>()))
                    .store(0, std::sync::atomic::Ordering::Release);
            }
            self.cur = (self.cur + 1) % BLOCKS;
        }
    }

    /// Разобрать один пакет (Ethernet → IPv4 → TCP) со временем прихода `t` (нс UTC).
    pub fn ingest(&mut self, p: &[u8], t: i64) {
        self.stats.packets += 1;
        let (Some(&vihl), Some(&proto)) = (p.get(14), p.get(23)) else {
            return;
        };
        if be16(p, 12) != Some(0x0800) || proto != 6 {
            return;
        }
        let ihl = usize::from(vihl & 15) * 4;
        let tcp = 14 + ihl;
        let (
            Some(total),
            Some(sport),
            Some(dport),
            Some(seq),
            Some(&doff),
            Some(&flags),
            Some(rip),
        ) = (
            be16(p, 16),
            be16(p, tcp),
            be16(p, tcp + 2),
            be32(p, tcp + 4),
            p.get(tcp + 12),
            p.get(tcp + 13),
            be32(p, 26),
        )
        else {
            return;
        };
        let key = Key {
            remote_ip: rip,
            remote_port: sport,
            local_port: dport,
        };
        const SYN: u8 = 0x02;
        const ACK: u8 = 0x10;
        if flags & (SYN | ACK) == (SYN | ACK) {
            if self.flows.len() >= MAX_FLOWS {
                self.flows.retain(|_, f| t - f.last_t < KEEP_NS);
            }
            self.flows.insert(
                key,
                Flow {
                    base: seq.wrapping_add(1),
                    max_end: 0,
                    pkts: VecDeque::new(),
                    last_t: t,
                },
            );
            return;
        }
        let len = usize::from(total).saturating_sub(ihl + usize::from(doff >> 4) * 4);
        if len == 0 {
            return;
        }
        let Some(f) = self.flows.get_mut(&key) else {
            return;
        };
        self.stats.data_packets += 1;
        // развёртка 32-битного смещения относительно наибольшего конца
        let rel = u64::from(seq.wrapping_sub(f.base));
        let mut start = (f.max_end & !0xffff_ffff) | rel;
        if start + (1 << 31) < f.max_end {
            start += 1 << 32;
        } else if start > f.max_end + (1 << 31) && start >= 1 << 32 {
            start -= 1 << 32;
        }
        let end = start + len as u64;
        f.max_end = f.max_end.max(end);
        f.last_t = t;
        f.pkts.push_back(Pkt { start, end, t });
        while f.pkts.front().is_some_and(|x| t - x.t > KEEP_NS) {
            f.pkts.pop_front();
        }
    }

    /// Метка записи TLS, занимающей байты `[a, b)` потока: время последнего из пакетов, дополнивших эти байты (при повторной доставке куска — самого раннего его
    /// экземпляра). `None` — пакетов потока нет или они не покрывают запись целиком.
    pub fn stamp_for(&mut self, key: &Key, a: u64, b: u64) -> Option<i64> {
        self.stats.lookups += 1;
        let f = self.flows.get_mut(key)?;
        let mut best: Option<i64> = None;
        let mut covered = a;
        let mut pieces: Vec<(u64, u64, i64)> = Vec::new();
        for x in f.pkts.iter().filter(|x| x.end > a && x.start < b) {
            pieces.push((x.start, x.end, x.t));
        }
        pieces.sort_unstable_by_key(|x| (x.0, x.2));
        for (s, e, t) in pieces {
            if s > covered {
                return None; // дыра: часть записи пришла пакетом, которого нет в кольце
            }
            if e > covered {
                covered = e;
                best = Some(best.map_or(t, |m| m.max(t)));
            }
        }
        if covered < b {
            return None;
        }
        // пакеты до начала записи больше не нужны
        while f.pkts.front().is_some_and(|x| x.end <= a) {
            f.pkts.pop_front();
        }
        self.stats.hits += 1;
        best
    }

    /// Сколько пакетов ядро не смогло положить в кольцо (кольцо переполнено): метки по ним не будет.
    pub fn kernel_drops(&self) -> u64 {
        let mut st = [0u32; 3]; // tpacket_stats_v3: tp_packets, tp_drops, tp_freeze_q_cnt
        let mut len = std::mem::size_of_val(&st) as libc::socklen_t;
        // SAFETY: fd живой; буфер — три u32, длина передана; ядро пишет не больше `len`.
        let rc = unsafe {
            libc::getsockopt(
                self.fd.as_raw_fd(),
                libc::SOL_PACKET,
                libc::PACKET_STATISTICS,
                st.as_mut_ptr().cast(),
                &mut len,
            )
        };
        if rc == 0 {
            u64::from(st[1])
        } else {
            0
        }
    }

    /// Забыть поток (соединение закрыто).
    pub fn forget(&mut self, key: &Key) {
        self.flows.remove(key);
    }
}

impl Drop for Tap {
    fn drop(&mut self) {
        // SAFETY: отображение создано в `open` с тем же размером.
        unsafe {
            libc::munmap(self.ring.cast(), BLOCK * BLOCKS);
        }
        let _ = &self.fd;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Пакет Ethernet/IPv4/TCP: источник `rip:sport` → `dport`, номер `seq`, флаги, данные длиной `len` (в буфере только заголовки).
    fn pkt(rip: [u8; 4], sport: u16, dport: u16, seq: u32, flags: u8, len: usize) -> Vec<u8> {
        let mut p = vec![0u8; 14 + 20 + 20];
        p[12] = 0x08;
        p[13] = 0x00;
        p[14] = 0x45;
        let total = u16::try_from(20 + 20 + len).unwrap();
        p[16..18].copy_from_slice(&total.to_be_bytes());
        p[23] = 6;
        p[26..30].copy_from_slice(&rip);
        p[34..36].copy_from_slice(&sport.to_be_bytes());
        p[36..38].copy_from_slice(&dport.to_be_bytes());
        p[38..42].copy_from_slice(&seq.to_be_bytes());
        p[46] = 5 << 4;
        p[47] = flags;
        p
    }

    fn key() -> Key {
        Key {
            remote_ip: u32::from_be_bytes([1, 2, 3, 4]),
            remote_port: 443,
            local_port: 50_000,
        }
    }

    fn open_flow(t: &mut Tap, isn: u32) {
        t.ingest(&pkt([1, 2, 3, 4], 443, 50_000, isn, 0x12, 0), 1_000);
    }

    #[test]
    fn stream_offsets_come_from_isn_and_a_record_takes_the_stamp_of_its_last_packet() {
        let mut t = Tap::for_tests();
        open_flow(&mut t, 1000);
        // три пакета по 100 байт: [0,100) в 10, [100,200) в 20, [200,300) в 35
        for (i, ts) in [10i64, 20, 35].into_iter().enumerate() {
            t.ingest(
                &pkt([1, 2, 3, 4], 443, 50_000, 1001 + 100 * i as u32, 0x18, 100),
                ts * 1_000_000,
            );
        }
        // запись [0,100) — метка первого пакета; [100,300) покрыта вторым и третьим — метка третьего
        assert_eq!(t.stamp_for(&key(), 0, 100), Some(10_000_000));
        assert_eq!(t.stamp_for(&key(), 100, 300), Some(35_000_000));
        // запись выходит за пакеты — метки нет
        assert_eq!(t.stamp_for(&key(), 300, 350), None);
    }

    #[test]
    fn retransmitted_piece_keeps_the_earliest_copy_and_a_gap_gives_no_stamp() {
        let mut t = Tap::for_tests();
        open_flow(&mut t, 5);
        t.ingest(&pkt([1, 2, 3, 4], 443, 50_000, 6, 0x18, 50), 1_000_000);
        t.ingest(&pkt([1, 2, 3, 4], 443, 50_000, 6, 0x18, 50), 9_000_000); // повтор того же куска
        assert_eq!(t.stamp_for(&key(), 0, 50), Some(1_000_000));
        // дыра: пакет [100,150) без [50,100)
        t.ingest(&pkt([1, 2, 3, 4], 443, 50_000, 106, 0x18, 50), 2_000_000);
        assert_eq!(t.stamp_for(&key(), 50, 150), None);
        t.ingest(&pkt([1, 2, 3, 4], 443, 50_000, 56, 0x18, 50), 7_000_000);
        assert_eq!(
            t.stamp_for(&key(), 50, 150),
            Some(7_000_000),
            "запись завершилась, когда пришёл недостающий кусок"
        );
    }

    #[test]
    fn stream_offsets_survive_the_32_bit_sequence_wrap() {
        let mut t = Tap::for_tests();
        open_flow(&mut t, 1000); // первый байт данных — seq 1001
        let base = 1001u32;
        // пакет в конце первых 4 ГиБ потока и следующий за ним — за границей 2^32 (seq возвращается к `base`)
        t.ingest(
            &pkt(
                [1, 2, 3, 4],
                443,
                50_000,
                base.wrapping_add(0xFFFF_FF9C),
                0x18,
                100,
            ),
            5_000_000,
        );
        t.ingest(&pkt([1, 2, 3, 4], 443, 50_000, base, 0x18, 100), 6_000_000);
        assert_eq!(t.stamp_for(&key(), 0xFFFF_FF9C, 1 << 32), Some(5_000_000));
        assert_eq!(
            t.stamp_for(&key(), 1 << 32, (1 << 32) + 100),
            Some(6_000_000)
        );
    }

    #[test]
    fn classic_bpf_program_has_consistent_jumps() {
        let p = program(&[443, 19_100]);
        // каждый переход попадает внутрь программы, последняя команда — возврат
        for (i, x) in p.iter().enumerate() {
            if x.code == 0x15 || x.code == 0x45 {
                assert!(i + 1 + usize::from(x.jt) < p.len(), "jt команды {i}");
                assert!(i + 1 + usize::from(x.jf) < p.len(), "jf команды {i}");
            }
        }
        assert_eq!(p.last().map(|x| x.code), Some(0x06));
    }

    impl Tap {
        /// Без кольца: разбор пакетов и поиск меток (проверки).
        fn for_tests() -> Self {
            // SAFETY: eventfd — любой живой дескриптор подойдёт как заглушка; кольцо не используется, `Drop` отображение не снимает при нулевом указателе.
            let raw = unsafe { libc::eventfd(0, 0) };
            Self {
                // SAFETY: `raw` создан здесь.
                fd: unsafe { OwnedFd::from_raw_fd(raw) },
                ring: std::ptr::null_mut(),
                cur: 0,
                flows: HashMap::new(),
                stats: TapStats::default(),
            }
        }
    }
}
