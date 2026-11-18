//! Тесты TK-607: склад суточных сделок (round-trip, устаревание, повреждение, потолок), целочисленный быстрый путь `mul_div`/`first_signal`,
//! ленивые NATR и бары.

use super::data::{
    build_bars, natr_series, Cols, Sym, DAYS_TOTAL, NATR_PERIODS, N_MIN, START_DAY, TF_MINUTES,
};
use super::store::{decode, encode, SrcMeta, StoreOpts, TickStore};
use super::tick::{first_signal, mul_div, LoadStat, Tick, TradeSrc, ZipSrc, DAY_MS};
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
    assert!(st.read("ABCUSDT", "2026-09-15", &meta(10)).is_none());
    st.write("ABCUSDT", "2026-09-15", &meta(10), &t, &ls);
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
    // повреждение: переворот байта нагрузки
    let p = st.path("ABCUSDT", "2026-09-15");
    let mut b = std::fs::read(&p).expect("файл");
    let k = b.len() - 3;
    b[k] ^= 0x55;
    std::fs::write(&p, &b).expect("запись");
    assert!(st.read("ABCUSDT", "2026-09-15", &meta(10)).is_none());
    assert_eq!(st.counters().bad, 1);
    // потолок: склад нулевого объёма только читает
    let tmp0 = tempfile::tempdir().expect("tmp");
    let s0 = open(tmp0.path(), 0);
    s0.write("ABCUSDT", "2026-09-15", &meta(10), &t, &ls);
    assert!(s0.read("ABCUSDT", "2026-09-15", &meta(10)).is_none());
    assert_eq!(s0.counters().written, 0);
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
    ) + "x,y\n";
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
