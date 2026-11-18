//! Тесты TK-607: склад суточных сделок (round-trip, устаревание, повреждение, потолок), целочисленный быстрый путь `mul_div`/`first_signal`,
//! ленивые NATR и бары.

use super::data::{
    build_bars, natr_series, Cols, Sym, DAYS_TOTAL, NATR_PERIODS, N_MIN, START_DAY, TF_MINUTES,
};
use super::store::{
    decode, encode, DayTrades, SrcMeta, StoreOpts, TickStore, F_PX_INEXACT, F_QTY_BAD, F_QTY_F64,
};
use super::tick::{
    dec_parts, first_signal, mul_div, parse_trades, parse_trades_full, LoadStat, Tick, TradeSrc,
    ZipSrc, DAY_MS,
};
use std::io::Cursor;
use std::path::Path;
use std::process::Command;
use std::sync::Arc;

struct Lcg(u64);

impl Lcg {
    fn next(&mut self) -> u64 {
        self.0 = self
            .0
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        self.0 >> 11
    }
    fn range(&mut self, n: u64) -> u64 {
        self.next() % n
    }
}

fn ticks(r: &mut Lcg, n: usize, base: i64, step: i64, gap_ms: u64) -> Vec<Tick> {
    let (mut px, mut off) = (base, 0u64);
    (0..n)
        .map(|_| {
            px = (px + (r.range(21) as i64 - 10) * step).max(step);
            off += r.range(gap_ms + 1);
            Tick {
                px,
                off: off.min(DAY_MS as u64 - 1) as u32,
                bm: r.range(2) == 1,
            }
        })
        .collect()
}

#[test]
fn codec_round_trip() {
    let mut r = Lcg(1);
    let mut cases: Vec<Vec<Tick>> = vec![Vec::new()];
    cases.push(vec![Tick {
        px: 123_456_789,
        off: 0,
        bm: true,
    }]);
    cases.push(vec![
        Tick {
            px: 5,
            off: 7,
            bm: false,
        },
        Tick {
            px: 5,
            off: 7,
            bm: true,
        },
        Tick {
            px: 5,
            off: 7,
            bm: false,
        },
    ]);
    for (n, base, step, gap) in [
        (1000, 8_357_680_000_000i64, 10_000i64, 300u64),
        (5000, 91_600, 1, 40),
        (3000, 1_000_000_000_000_000, 100_000_000, 80_000),
        (777, 100_000_000, 1_000, 0),
    ] {
        cases.push(ticks(&mut r, n, base, step, gap));
    }
    for t in &cases {
        let raw = encode(t).expect("кодирование");
        assert_eq!(
            decode(&raw, t.len()).as_deref(),
            Some(t.as_slice()),
            "n={}",
            t.len()
        );
        assert_eq!(decode(&raw, t.len() + 1), None, "лишняя сделка в заголовке");
        if !t.is_empty() {
            assert_eq!(decode(&raw[..raw.len() - 1], t.len()), None, "обрыв");
            assert_eq!(decode(&raw, t.len() - 1), None, "лишние байты");
        }
    }
}

#[test]
fn codec_rejects_unsorted_and_out_of_day() {
    let a = Tick {
        px: 1,
        off: 5,
        bm: false,
    };
    let b = Tick {
        px: 1,
        off: 4,
        bm: false,
    };
    assert!(encode(&[a, b]).is_none());
    let c = Tick {
        px: 1,
        off: DAY_MS as u32,
        bm: false,
    };
    assert!(encode(&[c]).is_none());
}

fn meta(size: u64) -> SrcMeta {
    SrcMeta {
        size,
        mtime_s: 1_700_000_000,
        mtime_ns: 123,
    }
}

fn open(dir: &Path, cap_gb: u64) -> Arc<TickStore> {
    Arc::new(
        TickStore::open(&StoreOpts {
            dir: dir.to_path_buf(),
            cap_gb,
            min_free_gb: 0,
        })
        .expect("склад"),
    )
}

fn day_of(t: &[Tick], ls: &LoadStat, r: &mut Lcg) -> DayTrades {
    DayTrades {
        ticks: t.to_vec(),
        qty: (0..t.len()).map(|_| r.range(100_000) * 10).collect(),
        qty_dec: 3,
        flags: 0,
        stat: ls.clone(),
    }
}

#[test]
fn store_write_read_stale_corrupt_and_cap() {
    let tmp = tempfile::tempdir().expect("tmp");
    let st = open(tmp.path(), 1);
    let mut r = Lcg(7);
    let t = ticks(&mut r, 2000, 50_000_000, 100, 500);
    let ls = LoadStat {
        files: 1,
        rows: 2000,
        unsorted_files: 1,
        out_of_day: 3,
        bad_rows: 2,
        errors: 0,
    };
    let d = day_of(&t, &ls, &mut r);
    assert!(st.read("ABCUSDT", "2026-09-15", &meta(10)).is_none());
    st.write("ABCUSDT", "2026-09-15", &meta(10), &d);
    let (v, got) = st
        .read("ABCUSDT", "2026-09-15", &meta(10))
        .expect("попадание");
    assert_eq!(v, t);
    assert_eq!(
        (
            got.files,
            got.rows,
            got.unsorted_files,
            got.out_of_day,
            got.bad_rows
        ),
        (1, 2000, 1, 3, 2)
    );
    let full = st
        .read_day("ABCUSDT", "2026-09-15", &meta(10))
        .expect("со склада целиком");
    assert_eq!(full.ticks, t);
    assert_eq!(full.qty, d.qty, "количество круговое");
    assert_eq!((full.qty_dec, full.flags), (3, 0));
    assert!(st.counters().qty_bytes > 0 && st.counters().rows == 2000);
    assert!(
        st.read("ABCUSDT", "2026-09-15", &meta(11)).is_none(),
        "размер zip изменился"
    );
    let mut m2 = meta(10);
    m2.mtime_ns += 1;
    assert!(
        st.read("ABCUSDT", "2026-09-15", &m2).is_none(),
        "mtime zip изменился"
    );
    let p = st.path("ABCUSDT", "2026-09-15");
    let good = std::fs::read(&p).expect("файл");
    // повреждение блока количества: движок его не читает и работает, признаки видят и читают zip заново
    let mut b = good.clone();
    let k = b.len() - 3;
    b[k] ^= 0x55;
    std::fs::write(&p, &b).expect("запись");
    assert!(st.read("ABCUSDT", "2026-09-15", &meta(10)).is_some());
    assert!(st.read_day("ABCUSDT", "2026-09-15", &meta(10)).is_none());
    assert_eq!(st.counters().bad, 1);
    // повреждение блока сделок: оба пути
    let mut b = good.clone();
    b[96 + 3] ^= 0x55;
    std::fs::write(&p, &b).expect("запись");
    assert!(st.read("ABCUSDT", "2026-09-15", &meta(10)).is_none());
    assert!(st.read_day("ABCUSDT", "2026-09-15", &meta(10)).is_none());
    assert_eq!(st.counters().bad, 3);
    // обрыв файла и прежняя версия формата
    std::fs::write(&p, &good[..good.len() - 1]).expect("запись");
    assert!(st.read("ABCUSDT", "2026-09-15", &meta(10)).is_none());
    assert_eq!(st.counters().bad, 4);
    let mut b = good.clone();
    b[4] = 1;
    std::fs::write(&p, &b).expect("запись");
    assert!(st.read("ABCUSDT", "2026-09-15", &meta(10)).is_none());
    assert_eq!(st.counters().bad, 4, "иная версия — промах, не повреждение");
    // потолок: склад нулевого объёма только читает
    let tmp0 = tempfile::tempdir().expect("tmp");
    let s0 = open(tmp0.path(), 0);
    s0.write("ABCUSDT", "2026-09-15", &meta(10), &d);
    assert!(s0.read("ABCUSDT", "2026-09-15", &meta(10)).is_none());
    assert_eq!(s0.counters().written, 0);
}

#[test]
fn dec_parts_cases() {
    for (s, want) in [
        ("0.00100000", Some((1, 3))),
        ("12.50", Some((125, 1))),
        ("1000", Some((1000, 0))),
        ("1000.000", Some((1000, 0))),
        ("1.0E-5", Some((1, 5))),
        ("9.16E-4", Some((916, 6))),
        ("1.5E+3", Some((1500, 0))),
        ("1.5e3", Some((1500, 0))),
        ("0", Some((0, 0))),
        ("0.000", Some((0, 0))),
        ("0.0E-7", Some((0, 0))),
        ("123456789.12345678", Some((12_345_678_912_345_678, 8))),
        ("", None),
        (".", None),
        ("-1", None),
        ("1e", None),
        ("1E-", None),
        ("abc", None),
        ("1.2.3", None),
        ("18446744073709551616", None),
        ("1E30", None),
    ] {
        assert_eq!(dec_parts(s.as_bytes()), want, "{s}");
    }
}

/// CSV из строк `(цена, количество, мс, bm)`: текст цены и количества как есть.
fn csv_q(day: usize, rows: &[(&str, &str, i64, &str)]) -> String {
    let base = super::data::T0_MS + day as i64 * DAY_MS;
    let mut s = String::from(
        "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n",
    );
    for (i, (p, q, off, bm)) in rows.iter().enumerate() {
        s.push_str(&format!("{i},{p},{q},{i},{i},{},{bm}\n", base + off));
    }
    s
}

#[test]
fn parse_full_ticks_equal_plain_and_qty_follows_sort() {
    let day = START_DAY;
    let text = csv_q(
        day,
        &[
            ("10.5", "0.100", 500, "false"),
            ("10.6", "2", 100, "true"),
            ("10.6", "0.0005", 100, "false"),
            ("10.4", "7.25", 900, "true"),
            ("1.0", "9", DAY_MS + 5, "false"),
            ("10.4", "zz", 950, "false"),
        ],
    ) + "x,y\n";
    let mut sp = LoadStat::default();
    let plain = parse_trades(Cursor::new(text.as_bytes()), day, &mut sp).expect("разбор");
    let d = parse_trades_full(Cursor::new(text.as_bytes()), day, LoadStat::default())
        .expect("разбор с количеством");
    assert_eq!(d.ticks, plain);
    assert_eq!(format!("{:?}", d.stat), format!("{sp:?}"));
    assert_eq!(d.qty_dec, 4);
    // порядок после устойчивой сортировки: 100 (2), 100 (0.0005), 500 (0.1), 900 (7.25), 950 (плохое → 0)
    assert_eq!(d.qty, vec![20_000, 5, 1_000, 72_500, 0]);
    assert_eq!(d.flags, F_QTY_BAD);
    assert_eq!(d.qty_f64(0), 2.0);
    assert_eq!(d.qty_f64(3), 7.25);
}

#[test]
fn full_parse_f64_equals_decimal_strings_and_flags() {
    let day = START_DAY;
    let mut r = Lcg(11);
    let mut owned: Vec<(String, String)> = Vec::new();
    for _ in 0..4000 {
        let dp = r.range(9) as usize;
        let dq = r.range(9) as usize;
        // цена < 9e7 и количество в масштабе 1e-8 < 9e15: обе величины точны в f64
        let mp = 1 + r.range(90_000_000 * 10u64.pow(dp as u32));
        let mq = r.range(9_000_000_000_000_000 / 10u64.pow(8 - dq as u32));
        let fmt = |m: u64, d: usize| {
            let s = format!("{m:0>w$}", w = d + 1);
            let (a, b) = s.split_at(s.len() - d);
            if d == 0 {
                a.to_string()
            } else {
                format!("{a}.{b}")
            }
        };
        owned.push((fmt(mp, dp), fmt(mq, dq)));
    }
    owned.push(("9.16E-4".to_string(), "1.5E-5".to_string()));
    owned.push(("0.00100000".to_string(), "10000000".to_string()));
    let rows: Vec<(&str, &str, i64, &str)> = owned
        .iter()
        .enumerate()
        .map(|(i, (p, q))| (p.as_str(), q.as_str(), i as i64, "false"))
        .collect();
    let text = csv_q(day, &rows);
    let d =
        parse_trades_full(Cursor::new(text.as_bytes()), day, LoadStat::default()).expect("разбор");
    assert_eq!(
        d.flags, 0,
        "все цены ≤ 8 знаков и < 2^53, количества < 2^53"
    );
    for (i, (p, q)) in owned.iter().enumerate() {
        assert_eq!(
            d.px_f64(i).to_bits(),
            p.parse::<f64>().expect("цена").to_bits(),
            "цена {p}"
        );
        assert_eq!(
            d.qty_f64(i).to_bits(),
            q.parse::<f64>().expect("количество").to_bits(),
            "количество {q}"
        );
    }
    // флаги: цена с 9 знаками, количество > 2^53, количество с 19 знаками, цена > 2^53 в ×1e8
    for (p, q, flag) in [
        ("1.123456789", "1", F_PX_INEXACT),
        ("1", "90071992547409930", F_QTY_F64),
        ("1", "0.0000000000000000001", F_QTY_BAD),
        ("90071992.54740993", "1", F_PX_INEXACT),
    ] {
        let text = csv_q(day, &[(p, q, 0, "true")]);
        let d = parse_trades_full(Cursor::new(text.as_bytes()), day, LoadStat::default())
            .expect("разбор");
        assert_eq!(d.flags, flag, "{p} {q}");
        assert_eq!(d.ticks.len(), 1, "строка остаётся, как у движка");
    }
}

fn crc32(d: &[u8]) -> u32 {
    let mut c = 0xFFFF_FFFFu32;
    for &b in d {
        c ^= u32::from(b);
        for _ in 0..8 {
            c = if c & 1 == 1 {
                (c >> 1) ^ 0xEDB8_8320
            } else {
                c >> 1
            };
        }
    }
    !c
}

/// zip без сжатия с одним файлом — достаточно для `unzip -p`.
fn write_zip(path: &Path, entry: &str, data: &[u8]) {
    let crc = crc32(data);
    let n = entry.len() as u16;
    let mut z = Vec::new();
    let le32 = |z: &mut Vec<u8>, v: u32| z.extend_from_slice(&v.to_le_bytes());
    let le16 = |z: &mut Vec<u8>, v: u16| z.extend_from_slice(&v.to_le_bytes());
    le32(&mut z, 0x0403_4b50);
    for v in [10u16, 0, 0, 0, 0x21] {
        le16(&mut z, v);
    }
    le32(&mut z, crc);
    le32(&mut z, data.len() as u32);
    le32(&mut z, data.len() as u32);
    le16(&mut z, n);
    le16(&mut z, 0);
    z.extend_from_slice(entry.as_bytes());
    z.extend_from_slice(data);
    let cd = z.len() as u32;
    le32(&mut z, 0x0201_4b50);
    for v in [20u16, 10, 0, 0, 0, 0x21] {
        le16(&mut z, v);
    }
    le32(&mut z, crc);
    le32(&mut z, data.len() as u32);
    le32(&mut z, data.len() as u32);
    for v in [n, 0, 0, 0, 0] {
        le16(&mut z, v);
    }
    le32(&mut z, 0);
    le32(&mut z, 0);
    z.extend_from_slice(entry.as_bytes());
    let cd_len = z.len() as u32 - cd;
    le32(&mut z, 0x0605_4b50);
    for v in [0u16, 0, 1, 1] {
        le16(&mut z, v);
    }
    le32(&mut z, cd_len);
    le32(&mut z, cd);
    le16(&mut z, 0);
    std::fs::write(path, z).expect("zip");
}

fn csv(day: usize, rows: &[(f64, i64, &str)]) -> String {
    let base = super::data::T0_MS + day as i64 * DAY_MS;
    let mut s = String::from(
        "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n",
    );
    for (i, (p, off, bm)) in rows.iter().enumerate() {
        s.push_str(&format!("{i},{p},1,{i},{i},{},{bm}\n", base + off));
    }
    s
}

#[test]
fn zipsrc_store_hit_miss_stale_missing_and_unsorted() {
    if Command::new("unzip").arg("-v").output().is_err() {
        return;
    }
    let tmp = tempfile::tempdir().expect("tmp");
    let day = START_DAY;
    let date = super::data::day_string(day);
    let dir = tmp.path().join("trades").join("TESTUSDT");
    std::fs::create_dir_all(&dir).expect("dir");
    let zip = dir.join(format!("TESTUSDT-aggTrades-{date}.zip"));
    // не по порядку, равные времена, вне суток, плохая строка
    let text = csv(
        day,
        &[
            (10.5, 500, "false"),
            (10.6, 100, "true"),
            (10.6, 100, "false"),
            (10.4, 900, "true"),
            (1.0, DAY_MS + 5, "false"),
        ],
    ) + "7,y\n";
    write_zip(&zip, "TESTUSDT.csv", text.as_bytes());
    let root = tmp.path().join("trades");
    let store = open(&tmp.path().join("store"), 1);
    let plain = ZipSrc::new(root.clone(), 1 << 20);
    let want = plain.day("TESTUSDT", day).expect("без склада");
    let want_stat = plain.stat.lock().expect("stat").clone();
    assert_eq!(
        want.iter().map(|t| t.off).collect::<Vec<_>>(),
        vec![100, 100, 500, 900]
    );
    assert_eq!(
        (
            want_stat.unsorted_files,
            want_stat.out_of_day,
            want_stat.bad_rows
        ),
        (1, 1, 1)
    );

    let a = ZipSrc::new(root.clone(), 1 << 20).with_store(Arc::clone(&store));
    assert_eq!(
        a.day("TESTUSDT", day).as_deref(),
        Some(&*want),
        "холодный склад"
    );
    assert_eq!((store.counters().miss, store.counters().written), (1, 1));
    let b = ZipSrc::new(root.clone(), 1 << 20).with_store(Arc::clone(&store));
    assert_eq!(
        b.day("TESTUSDT", day).as_deref(),
        Some(&*want),
        "тёплый склад"
    );
    assert_eq!(store.counters().hit, 1);
    let (sa, sb) = (
        a.stat.lock().expect("a").clone(),
        b.stat.lock().expect("b").clone(),
    );
    assert_eq!(
        format!("{sa:?}"),
        format!("{sb:?}"),
        "счётчики загрузки со склада те же"
    );
    assert_eq!(format!("{sb:?}"), format!("{want_stat:?}"));
    assert!(
        b.day("TESTUSDT", day + 1).is_none(),
        "нет файла — как прежде"
    );
    assert!(b.day("NOPEUSDT", day).is_none());
    assert_eq!(b.stat.lock().expect("b").errors, 0);

    // zip заменён (другой размер) — склад устарел, читается новое содержимое
    let text2 = csv(day, &[(11.0, 5, "true"), (11.5, 6, "false")]);
    write_zip(&zip, "TESTUSDT.csv", text2.as_bytes());
    let c = ZipSrc::new(root, 1 << 20).with_store(Arc::clone(&store));
    let got = c.day("TESTUSDT", day).expect("новое содержимое");
    assert_eq!(got.len(), 2);
    assert_eq!(store.counters().written, 2);
}

#[test]
fn mul_div_equals_i128() {
    let mut r = Lcg(3);
    let edge = [
        0i64,
        1,
        -1,
        2,
        i64::MAX,
        i64::MIN,
        i64::MAX / 3,
        1_000_000_000_000_000,
        100_000_000,
        99_999,
    ];
    let mut vals: Vec<i64> = edge.to_vec();
    for _ in 0..400 {
        let k = r.range(63) as u32;
        let v = (r.next() >> (64 - 11 - k.min(52))) as i64;
        vals.push(if r.range(2) == 0 { v } else { -v });
    }
    let mut n = 0;
    for &a in &vals {
        for &b in &edge {
            for &d in vals.iter().step_by(7) {
                if d == 0 {
                    continue;
                }
                let want = i128::from(a) * i128::from(b) / i128::from(d);
                assert_eq!(mul_div(a, b, d), want, "{a}·{b}/{d}");
                n += 1;
            }
        }
    }
    assert!(n > 10_000);
}

/// Прежняя реализация (деление в `i128` на каждой сделке).
fn first_signal_ref(
    ticks: &[Tick],
    from: usize,
    to: usize,
    s: i64,
    lp: i64,
    dmax: i64,
    color: bool,
) -> Option<(usize, u32)> {
    let mut hi = i64::MIN;
    let mut op = 0i64;
    for (k, tk) in ticks.iter().enumerate().take(to).skip(from) {
        let p = s * tk.px;
        if k == from {
            op = p;
        }
        hi = hi.max(p);
        if hi >= lp {
            return None;
        }
        let dist =
            (i128::from(lp - p) * i128::from(super::data::SCALE) / i128::from(lp.abs())) as i64;
        if dist > dmax || (color && p <= op) {
            continue;
        }
        return Some((k, dist as u32));
    }
    None
}

#[test]
fn first_signal_equals_reference() {
    let mut r = Lcg(11);
    let mut found = 0;
    for case in 0..4000 {
        let big = case % 4 == 3;
        let (base, step) = if big {
            (4_000_000_000_000_000i64, 1_000_000_000_000i64)
        } else {
            (8_000_000_000i64, 10_000i64)
        };
        let n = 1 + r.range(40) as usize;
        let tks = ticks(&mut r, n, base, step, 5);
        let s = if r.range(2) == 0 { 1 } else { -1 };
        let lp = s * (base + (r.range(31) as i64 - 8) * step);
        let dmax = (r.range(4) as i64 + 1) * 100_000 * [1, 10, 100][r.range(3) as usize];
        let color = r.range(2) == 0;
        let (from, to) = (0, n);
        let a = first_signal(&tks, from, to, s, lp, dmax, color);
        let b = first_signal_ref(&tks, from, to, s, lp, dmax, color);
        assert_eq!(a, b, "case {case}");
        found += usize::from(a.is_some());
    }
    assert!(found > 100, "тест должен ловить и сигналы: {found}");
}

/// Прежняя реализация NATR (деление в `i128`).
fn natr_ref(cols: &Cols, p: usize) -> Vec<i32> {
    let n = N_MIN;
    let mut trp = vec![0i64; n + 1];
    let mut cnt = vec![0u32; n + 1];
    let mut prevc = 0i64;
    for t in 0..n {
        let (mut tr, mut c) = (0, 0);
        if cols.o[t] != 0 {
            tr = cols.h[t] - cols.l[t];
            if prevc != 0 {
                tr = tr
                    .max((cols.h[t] - prevc).abs())
                    .max((cols.l[t] - prevc).abs());
            }
            prevc = cols.c[t];
            c = 1;
        }
        trp[t + 1] = trp[t] + tr;
        cnt[t + 1] = cnt[t] + c;
    }
    let mut out = vec![-1i32; n];
    for t in p - 1..n {
        if cols.o[t] == 0 {
            continue;
        }
        let k = cnt[t + 1] - cnt[t + 1 - p];
        if (k as usize) * 2 < p {
            continue;
        }
        let sum = i128::from(trp[t + 1] - trp[t + 1 - p]);
        let v = sum * i128::from(super::data::SCALE) / (i128::from(k) * i128::from(cols.c[t]));
        out[t] = v.min(i128::from(i32::MAX)) as i32;
    }
    out
}

fn walk_cols(r: &mut Lcg, price: i64) -> Cols {
    let mut c = Cols::new();
    let mut p = price;
    for m in 0..N_MIN {
        if r.range(50) == 0 {
            continue; // дыра
        }
        let o = p;
        p = (p + (r.range(2001) as i64 - 1000) * (price / 100_000).max(1)).max(price / 2);
        let (h, l) = (
            o.max(p) + r.range(500) as i64,
            o.min(p) - r.range(500) as i64,
        );
        c.o[m] = o;
        c.h[m] = h;
        c.l[m] = l.max(1);
        c.c[m] = p;
        c.qv[m] = r.range(1_000_000) as i64;
    }
    c
}

#[test]
fn lazy_natr_and_bars_equal_eager() {
    let mut r = Lcg(5);
    // 100_000_000 (≈ 1), цены в 1e13 (дорогая монета: деление идёт по i128-пути)
    for price in [100_000_000i64, 3_000_000_000_000, 9_000_000_000_000_000] {
        let cols = walk_cols(&mut r, price);
        let s = Sym::from_cols("T", cols.clone(), Vec::new());
        for p in NATR_PERIODS {
            let got = s.natr_for(p).expect("период");
            assert_eq!(got, natr_ref(&cols, p).as_slice(), "natr {p} price {price}");
            assert_eq!(got, natr_series(&cols, p).as_slice());
            assert!(got.iter().any(|&v| v >= 0));
        }
        for tf in TF_MINUTES {
            let (a, b) = (s.bars_for(tf).expect("тф"), build_bars(&cols, tf));
            assert_eq!(
                (&a.o, &a.h, &a.l, &a.c, &a.ok),
                (&b.o, &b.h, &b.l, &b.c, &b.ok),
                "bars {tf}"
            );
        }
        assert!(s.natr_for(7).is_none() && s.bars_for(7).is_none());
    }
    assert_eq!(DAYS_TOTAL * 1440, N_MIN);
}

/// Склад и подмена источника (критик TK-607): один и тот же `(монета, дата)` из двух деревьев zip (одинаковый размер, разный mtime и содержимое;
/// так `rt-fz` и TK-599 читают разные zip одной даты) — запись склада переписывается, устаревшее чтение невозможно, ошибки нет; повреждённый файл
/// склада — чтение zip заново и перезапись; сбой записи склада — предупреждение, счёт идёт.
#[test]
fn zipsrc_alternate_source_corrupt_and_write_failure_never_stale_never_error() {
    use std::fs::OpenOptions;
    use std::time::{Duration, UNIX_EPOCH};
    if Command::new("unzip").arg("-v").output().is_err() {
        return;
    }
    let tmp = tempfile::tempdir().expect("tmp");
    let day = START_DAY;
    let date = super::data::day_string(day);
    let mk = |tree: &str, rows: &[(f64, i64, &str)], mtime_s: u64| -> std::path::PathBuf {
        let root = tmp.path().join(tree);
        let dir = root.join("TESTUSDT");
        std::fs::create_dir_all(&dir).expect("dir");
        let zip = dir.join(format!("TESTUSDT-aggTrades-{date}.zip"));
        write_zip(&zip, "TESTUSDT.csv", csv(day, rows).as_bytes());
        OpenOptions::new()
            .write(true)
            .open(&zip)
            .expect("open")
            .set_modified(UNIX_EPOCH + Duration::from_secs(mtime_s))
            .expect("mtime");
        root
    };
    let a = mk("a", &[(10.1, 5, "false"), (10.2, 9, "true")], 1_700_000_000);
    let b = mk("b", &[(10.3, 5, "false"), (10.4, 9, "true")], 1_700_000_100);
    let zip_len = |r: &Path| {
        std::fs::metadata(
            r.join("TESTUSDT")
                .join(format!("TESTUSDT-aggTrades-{date}.zip")),
        )
        .expect("zip")
        .len()
    };
    assert_eq!(
        zip_len(&a),
        zip_len(&b),
        "одинаковый размер, различие — mtime"
    );
    let store = open(&tmp.path().join("store"), 1);
    let src = |root: &Path| ZipSrc::new(root.to_path_buf(), 1 << 20).with_store(Arc::clone(&store));
    let px = |s: &ZipSrc| -> Vec<i64> {
        s.read_day("TESTUSDT", day)
            .expect("без ошибки")
            .expect("файл есть")
            .ticks
            .iter()
            .map(|t| t.px)
            .collect()
    };
    let (pa, pb) = (
        vec![1_010_000_000, 1_020_000_000],
        vec![1_030_000_000, 1_040_000_000],
    );
    let cnt = |s: &TickStore| {
        let c = s.counters();
        (c.hit, c.miss, c.written, c.bad)
    };
    assert_eq!(px(&src(&a)), pa);
    assert_eq!(cnt(&store), (0, 1, 1, 0));
    assert_eq!(px(&src(&a)), pa);
    assert_eq!(cnt(&store), (1, 1, 1, 0), "то же дерево — попадание");
    assert_eq!(
        px(&src(&b)),
        pb,
        "другое дерево той же даты — не устаревшее чтение"
    );
    assert_eq!(
        cnt(&store),
        (1, 2, 2, 0),
        "несовпадение заголовка — промах и перезапись, не повреждение"
    );
    assert_eq!(px(&src(&a)), pa);
    assert_eq!(
        cnt(&store),
        (1, 3, 3, 0),
        "обратно на первое дерево — снова перезапись"
    );
    assert_eq!(px(&src(&a)), pa);
    assert_eq!(cnt(&store), (2, 3, 3, 0));
    // повреждённый файл склада: zip читается заново, файл переписывается
    let p = store.path("TESTUSDT", &date);
    let mut raw = std::fs::read(&p).expect("файл склада");
    raw[96 + 3] ^= 0x55;
    std::fs::write(&p, &raw).expect("запись");
    assert_eq!(px(&src(&a)), pa);
    assert_eq!(
        cnt(&store),
        (2, 4, 4, 1),
        "повреждение — промах, пересчёт и перезапись"
    );
    assert_eq!(px(&src(&a)), pa);
    assert_eq!(cnt(&store), (3, 4, 4, 1));
    // сбой записи (на месте каталога монеты — файл): счёт идёт, ошибка загрузки не засчитана
    let store2 = open(&tmp.path().join("store2"), 1);
    std::fs::write(tmp.path().join("store2").join("TESTUSDT"), b"x").expect("файл вместо каталога");
    let s2 = ZipSrc::new(a.clone(), 1 << 20).with_store(Arc::clone(&store2));
    assert_eq!(px(&s2), pa);
    assert_eq!(store2.counters().write_err, 1);
    assert_eq!(s2.stat.lock().expect("stat").errors, 0);
}

/// Ручные ворота TK-607 (склад v2, количество): по ВСЕМ файлам окна количество и цена со склада (тёплое чтение) равны разбору CSV
/// стандартной библиотекой (`str::parse::<f64>`, независимый от `dec_parts`/`parse_px`), порядок, `off`, `bm` — те же, холодное чтение равно тёплому.
/// Окно — файл `/data/p42-tk607/qtyrt.conf`: строки `T0 (YYYY-MM-DD)`, каталог zip (`<монета>/<монета>-aggTrades-<дата>.zip`), каталог для временного склада, [список `<МОНЕТА> <дата>`].
/// Итог пишется в `/data/p42-tk607/qtyrt-<T0>.out`. Запуск: `tools/p42/build-srv.sh <x> test "breakout::store_tests::window_quantity -- --ignored --nocapture"`.
#[test]
#[ignore = "ворота на данных srv: /data/p42-tk607/qtyrt.conf"]
fn window_quantity_matches_csv() {
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Mutex;
    let Ok(conf) = std::fs::read_to_string("/data/p42-tk607/qtyrt.conf") else {
        eprintln!("нет /data/p42-tk607/qtyrt.conf");
        return;
    };
    let l: Vec<&str> = conf
        .lines()
        .map(str::trim)
        .filter(|x| !x.is_empty())
        .collect();
    let (t0, trades, parent) = (l[0], Path::new(l[1]), Path::new(l[2]));
    std::env::set_var("P42_T0", t0);
    assert_eq!(super::data::day_string(0), t0, "P42_T0 не применился");
    let t0d = chrono::NaiveDate::parse_from_str(t0, "%Y-%m-%d").expect("T0");
    let mut files: Vec<(String, usize)> = Vec::new();
    let mut before_t0 = 0usize;
    // четвёртая строка конфигурации — список `<МОНЕТА> <дата>` (файлы, которые окно реально прочитало); без неё — все zip каталога
    if let Some(list) = l.get(3) {
        for row in std::fs::read_to_string(list)
            .expect("список файлов")
            .lines()
        {
            let mut it = row.split_whitespace();
            let (Some(name), Some(date)) = (it.next(), it.next()) else {
                continue;
            };
            let d = chrono::NaiveDate::parse_from_str(date, "%Y-%m-%d").expect("дата списка");
            files.push((
                name.to_string(),
                usize::try_from((d - t0d).num_days()).expect("дата раньше T0"),
            ));
        }
    }
    for coin in std::fs::read_dir(trades)
        .expect("каталог zip")
        .flatten()
        .filter(|_| l.get(3).is_none())
    {
        let name = coin.file_name().to_string_lossy().into_owned();
        let Ok(rd) = std::fs::read_dir(coin.path()) else {
            continue;
        };
        for f in rd.flatten() {
            let fname = f.file_name().to_string_lossy().into_owned();
            let Some(date) = fname
                .strip_prefix(&format!("{name}-aggTrades-"))
                .and_then(|r| r.strip_suffix(".zip"))
            else {
                continue;
            };
            let Ok(d) = chrono::NaiveDate::parse_from_str(date, "%Y-%m-%d") else {
                continue;
            };
            match usize::try_from((d - t0d).num_days()) {
                Ok(day) => files.push((name.clone(), day)),
                Err(_) => before_t0 += 1,
            }
        }
    }
    files.sort();
    let tmp = tempfile::Builder::new()
        .prefix("qtyrt-")
        .tempdir_in(parent)
        .expect("временный склад");
    let store = open(tmp.path(), 20);
    let cold = ZipSrc::new(trades.to_path_buf(), 1 << 20).with_store(Arc::clone(&store));
    let warm = ZipSrc::new(trades.to_path_buf(), 1 << 20).with_store(Arc::clone(&store));
    let next = AtomicUsize::new(0);
    let rows = AtomicUsize::new(0);
    let flagged = Mutex::new((0usize, 0usize));
    let errs: Mutex<Vec<String>> = Mutex::new(Vec::new());
    std::thread::scope(|sc| {
        for _ in 0..2 {
            sc.spawn(|| loop {
                let i = next.fetch_add(1, Ordering::Relaxed);
                let Some((sym, day)) = files.get(i) else {
                    break;
                };
                let r = qty_check(&cold, &warm, trades, sym, *day);
                match r {
                    Ok((n, fpx, fq)) => {
                        rows.fetch_add(n, Ordering::Relaxed);
                        if let Ok(mut g) = flagged.lock() {
                            g.0 += usize::from(fpx);
                            g.1 += usize::from(fq);
                        }
                    }
                    Err(e) => {
                        if let Ok(mut g) = errs.lock() {
                            g.push(format!("{sym} день {day}: {e}"));
                        }
                    }
                }
            });
        }
    });
    let errs = errs.into_inner().expect("errs");
    let (fpx, fq) = flagged.into_inner().expect("flagged");
    let c = store.counters();
    let line = format!(
        "QTYRT T0={t0} файлов={} (до T0 пропущено {before_t0}) строк={} расхождений={} флаг цены={fpx} флаг количества={fq} | склад: hit {} miss {} written {} bad {} объём {} Б, блок количества {} Б",
        files.len(),
        rows.load(Ordering::Relaxed),
        errs.len(),
        c.hit,
        c.miss,
        c.written,
        c.bad,
        c.used_bytes,
        c.qty_bytes
    );
    let mut out = line.clone();
    for e in errs.iter().take(20) {
        out.push_str(&format!("\n  {e}"));
    }
    std::fs::write(format!("/data/p42-tk607/qtyrt-{t0}.out"), &out).expect("итог");
    eprintln!("{out}");
    assert!(errs.is_empty(), "{line}");
    assert_eq!(
        c.hit as usize,
        files.len(),
        "тёплое чтение — только попадания"
    );
}

/// Один файл: холодное чтение (zip → склад), тёплое (склад) и независимый разбор CSV. `Ok((строк, флаг цены, флаг количества))`.
fn qty_check(
    cold: &ZipSrc,
    warm: &ZipSrc,
    trades: &Path,
    sym: &str,
    day: usize,
) -> Result<(usize, bool, bool), String> {
    let date = super::data::day_string(day);
    let c = cold
        .read_day(sym, day)
        .map_err(|e| format!("холодное: {e:#}"))?
        .ok_or("холодное: нет файла")?;
    let w = warm
        .read_day(sym, day)
        .map_err(|e| format!("тёплое: {e:#}"))?
        .ok_or("тёплое: нет файла")?;
    if c.ticks != w.ticks || c.qty != w.qty || (c.qty_dec, c.flags) != (w.qty_dec, w.flags) {
        return Err("холодное чтение != тёплому".into());
    }
    let base = super::data::t0_ms() + day as i64 * DAY_MS;
    let zip = trades.join(sym).join(format!("{sym}-aggTrades-{date}.zip"));
    let out = Command::new("unzip")
        .arg("-p")
        .arg(&zip)
        .output()
        .map_err(|e| format!("unzip: {e}"))?;
    let mut want: Vec<(u32, bool, f64, f64)> = Vec::new();
    let (mut prev, mut unsorted) = (0u32, false);
    for line in out.stdout.split(|&b| b == b'\n') {
        if !line.first().is_some_and(u8::is_ascii_digit) {
            continue;
        }
        let f: Vec<&[u8]> = line.split(|&b| b == b',').collect();
        if f.len() < 7 {
            continue;
        }
        let num = |b: &[u8]| {
            std::str::from_utf8(b)
                .ok()
                .and_then(|s| s.parse::<f64>().ok())
        };
        let (Some(p), Some(q), Some(ts)) = (
            num(f[1]),
            num(f[2]),
            std::str::from_utf8(f[5])
                .ok()
                .and_then(|s| s.parse::<i64>().ok()),
        ) else {
            continue;
        };
        let off = ts - base;
        if !(0..DAY_MS).contains(&off) {
            continue;
        }
        let off = off as u32;
        unsorted |= off < prev;
        prev = off;
        want.push((off, f[6].first() == Some(&b't'), p, q));
    }
    if unsorted {
        want.sort_by_key(|x| x.0);
    }
    if w.ticks.len() != want.len() || w.qty.len() != want.len() {
        return Err(format!(
            "строк {} (склад) против {} (CSV)",
            w.ticks.len(),
            want.len()
        ));
    }
    let (fpx, fq) = (
        w.flags & F_PX_INEXACT != 0,
        w.flags & (F_QTY_BAD | F_QTY_F64) != 0,
    );
    for (i, &(off, bm, p, q)) in want.iter().enumerate() {
        if w.ticks[i].off != off || w.ticks[i].bm != bm {
            return Err(format!("строка {i}: off/bm"));
        }
        if !fq && w.qty_f64(i).to_bits() != q.to_bits() {
            return Err(format!("строка {i}: количество {} != {q}", w.qty_f64(i)));
        }
        if !fpx && w.px_f64(i).to_bits() != p.to_bits() {
            return Err(format!("строка {i}: цена {} != {p}", w.px_f64(i)));
        }
    }
    Ok((want.len(), fpx, fq))
}
