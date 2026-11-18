//! Тесты порта f12 (TK-611): числовые примитивы, разбор входа, одна монета на синтетике. Равенство с Python по реальным данным —
//! ворота `tools/p42/rustfeat_f12_gate.py` (побитовое сравнение с `eps-*.parquet` TK-599).

use super::f12::{
    block_returns, coin_rows, csv_row, ffill_close, header, minute_returns, natr_series,
    parse_px_csv, pearson_pairs, ratio_f64, read_sigsx, CoinCtx, Minutes, PxDay, PxSrc, Refs, Sig,
    DAYS_TOTAL, META, NAMES,
};
use super::pdcompat::parse_date;
use anyhow::Result;
use std::io::Cursor;

const DAY_MS: i64 = 86_400_000;

fn flat_minutes(n: usize, o: i64, h: i64, l: i64, c: i64, qv: i64) -> Minutes {
    Minutes {
        o: vec![o; n],
        h: vec![h; n],
        l: vec![l; n],
        c: vec![c; n],
        qv: vec![qv; n],
    }
}

#[test]
fn ratio_matches_decimal_parse() {
    // правильно округлённое частное `num / 1e8` = разбор десятичной записи (Rust `str::parse` округляет верно)
    let mut z = 0x9E37_79B9_7F4A_7C15u64;
    for _ in 0..2000 {
        z = z.wrapping_mul(6_364_136_223_846_793_005).wrapping_add(1);
        let num = u128::from(z >> 1) % 3_000_000_000_000_000_000u128 + 1;
        let want: f64 = format!("{}.{:08}", num / 100_000_000, num % 100_000_000)
            .parse()
            .unwrap_or(f64::NAN);
        assert_eq!(
            ratio_f64(num, 100_000_000).to_bits(),
            want.to_bits(),
            "{num}"
        );
    }
    assert_eq!(ratio_f64(1, 3).to_bits(), (1.0f64 / 3.0).to_bits());
}

#[test]
fn pearson_skips_nan_and_needs_min_pairs() {
    let x = [1.0, 2.0, f64::NAN, 4.0, 5.0];
    let y = [2.0, 4.0, 9.0, 8.0, f64::NAN];
    // общие позиции 0, 1, 3 — прямая y = 2x
    let r = pearson_pairs(&x, &y, 3);
    assert!((r - 1.0).abs() < 1e-12, "{r}");
    assert!(pearson_pairs(&x, &y, 4).is_nan());
    assert!(pearson_pairs(&[1.0, 1.0, 1.0], &[1.0, 2.0, 3.0], 2).is_nan()); // нулевая дисперсия
}

#[test]
fn returns_and_ffill() {
    let n = 30;
    let mut m = flat_minutes(n, 100, 101, 99, 100, 5);
    m.o[0] = 0; // минутки без данных: 0, 7, 8
    m.o[7] = 0;
    m.o[8] = 0;
    for (i, c) in m.c.iter_mut().enumerate() {
        *c = 100 + i as i64;
    }
    let cf = ffill_close(&m);
    assert!(cf[0].is_nan());
    assert_eq!(cf[1].to_bits(), 101.0f64.to_bits());
    assert_eq!(cf[8].to_bits(), 106.0f64.to_bits()); // последнее закрытие с данными (минута 6)
    let r1 = minute_returns(&m);
    assert!(r1[0].is_nan() && r1[1].is_nan() && r1[7].is_nan() && r1[8].is_nan() && r1[9].is_nan());
    assert_eq!(r1[2].to_bits(), (102.0f64 / 101.0 - 1.0).to_bits());
    let r5 = block_returns(&m, 5);
    // блок 0 — минуты 0…4 (последняя с данными — 4: 104), блок 1 — 5…9 (7, 8 пусты, последняя с данными — 9: 109), блок 2 — 10…14 (114)
    assert!(r5[0].is_nan());
    assert_eq!(r5[1].to_bits(), (109.0f64 / 104.0 - 1.0).to_bits());
    assert_eq!(r5[2].to_bits(), (114.0f64 / 109.0 - 1.0).to_bits());
}

#[test]
fn natr_flat_series() {
    let m = flat_minutes(
        200,
        100_00000000,
        100_20000000,
        99_80000000,
        100_00000000,
        1,
    );
    let nr = natr_series(&m, 30);
    assert_eq!(nr[28], -1);
    assert_eq!(nr[29], 400_000); // TR = 0,4 % от закрытия
    assert_eq!(nr[199], 400_000);
}

#[test]
fn px_csv_rint_and_stable_sort() -> Result<()> {
    let csv =
        "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n\
               1,100.5,1,1,1,2000,true\n\
               2,0.00001234,2,2,2,1000,false\n\
               3,99.99999999,3,3,3,2000,false\n\
               4,7,1,4,4,1000,false\n";
    let d = parse_px_csv(Cursor::new(csv))?;
    assert!(d.unsorted);
    // устойчивая сортировка по времени: 1000: (2), (4); 2000: (1), (3)
    assert_eq!(d.t, vec![1000, 1000, 2000, 2000]);
    assert_eq!(d.p, vec![1234, 700_000_000, 10_050_000_000, 9_999_999_999]);
    assert!(parse_px_csv(Cursor::new("a,b\n1,2\n")).is_err());
    Ok(())
}

#[test]
fn sigsx_reader_sorts_by_level_then_time() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let p = dir.path().join("long-sigsx.csv");
    // заголовок бинаря: 9 имён при 10 полях строк
    std::fs::write(
        &p,
        "sym,level,sig_ms,dist_e8,level_px_e8,born,end,first_bar,natr_e8\n\
         BBBUSDT,3,5000,10,200,1,9,7,400000,5:200|6:199\n\
         AAAUSDT,2,9000,11,300,1,9,7,400001,5:300\n\
         AAAUSDT,2,4000,12,300,1,9,7,400002,5:300\n\
         AAAUSDT,1,9500,13,100,1,9,7,400003,5:100|9:101\n",
    )?;
    let by = read_sigsx(&p, None)?;
    let a = &by["AAAUSDT"];
    assert_eq!(
        a.iter().map(|x| (x.level, x.sig_ms)).collect::<Vec<_>>(),
        vec![(1, 9500), (2, 4000), (2, 9000)]
    );
    assert_eq!(a[0].members, vec![(5, 100), (9, 101)]);
    assert_eq!(a[0].natr_e8, 400_003);
    let only = read_sigsx(&p, Some(&["BBBUSDT".to_string()]))?;
    assert_eq!(only.len(), 1);
    Ok(())
}

struct MemSrc {
    day: i64,
    t: Vec<i64>,
    p: Vec<i64>,
}

impl PxSrc for MemSrc {
    fn day(&self, _sym: &str, abs_day: i64) -> Result<Option<PxDay>> {
        Ok((abs_day == self.day).then(|| PxDay {
            t: self.t.clone(),
            p: self.p.clone(),
            unsorted: false,
        }))
    }
}

fn refs() -> Refs {
    let n = (DAYS_TOTAL * 1440) as usize;
    let mk = || {
        CoinCtx::new(flat_minutes(
            n,
            50_00000000,
            50_10000000,
            49_90000000,
            50_00000000,
            7,
        ))
    };
    Refs {
        refs: vec![("BTCUSDT", mk()), ("ETHUSDT", mk())],
    }
}

#[test]
fn coin_rows_flat_synthetic() -> Result<()> {
    let n = (DAYS_TOTAL * 1440) as usize;
    let ctx = CoinCtx::new(flat_minutes(
        n,
        100_00000000,
        100_20000000,
        99_80000000,
        100_00000000,
        1_000_000,
    ));
    let base_ms = parse_date("2026-09-01")? * DAY_MS;
    let day = 20;
    let sig_ms = day * DAY_MS + 10 * 3_600_000; // 10:00 UTC, от базовой даты
    let sig_abs = base_ms + sig_ms;
    let r = 100_50000000i64;
    let sig = Sig {
        level: 7,
        sig_ms,
        dist_e8: 99_502, // (R − 100,4)/R·1e8, усечение
        level_px_e8: r,
        born: 20 * 1440 + 300,
        first_bar: 20 * 24 + 1,
        natr_e8: 400_000,
        members: vec![(20 * 24 + 1, r), (20 * 24 + 4, 100_40000000)],
    };
    let src = MemSrc {
        day: base_ms.div_euclid(DAY_MS) + day,
        t: vec![
            sig_abs - 50 * 60_000,
            sig_abs - 10 * 60_000,
            sig_abs,
            sig_abs + 5000,
        ],
        p: vec![100_00000000, 100_10000000, 100_40000000, 100_45000000],
    };
    let (rows, st) = coin_rows(
        &src,
        &refs(),
        &ctx,
        "ZZZUSDT",
        1,
        base_ms,
        DAYS_TOTAL,
        std::slice::from_ref(&sig),
    )?;
    assert_eq!(st.locate_fail, 0);
    assert_eq!(st.natr_mismatch, 0);
    assert_eq!(rows.len(), 1);
    let f = rows[0].feats.expect("признаки");
    let at = |name: &str| f[NAMES.iter().position(|&x| x == name).unwrap_or(usize::MAX)];
    assert_eq!(at("n_piv").to_bits(), 2.0f64.to_bits());
    assert_eq!(at("pierce_ep").to_bits(), 0.0f64.to_bits());
    assert_eq!(at("touch_ep").to_bits(), 0.0f64.to_bits());
    assert_eq!(at("tpm60").to_bits(), (2.0f64 / 60.0).to_bits());
    assert_eq!(at("tpm15").to_bits(), (1.0f64 / 15.0).to_bits());
    assert_eq!(at("gap60_s").to_bits(), 2400.0f64.to_bits()); // 40 минут между сделками
    assert_eq!(at("natr_bp").to_bits(), 40.0f64.to_bits());
    assert_eq!(at("turn1h").to_bits(), 600.0f64.to_bits()); // 60 мин × 1e6 / 1e5
    assert!(at("c5_btc_7d").is_nan()); // у плоских рядов нулевая дисперсия
    assert_eq!(at("mv5").to_bits(), 0.0f64.to_bits());
    // строка CSV: столько же полей, сколько в заголовке
    let line = csv_row("ZZZUSDT", "long", &rows[0]);
    assert_eq!(line.split(',').count(), header().split(',').count());
    assert!(line.starts_with("U0|long|ZZZUSDT|7|0,U0,long,ZZZUSDT,7,0,"));

    // сигнальная сделка не найдена (другая цена) → строка-заглушка, `locate_fail`
    let src2 = MemSrc {
        p: vec![100_00000000, 100_10000000, 100_30000000, 100_45000000],
        ..src
    };
    let (rows, st) = coin_rows(
        &src2,
        &refs(),
        &ctx,
        "ZZZUSDT",
        1,
        base_ms,
        DAYS_TOTAL,
        &[sig],
    )?;
    assert_eq!(st.locate_fail, 1);
    assert!(rows[0].feats.is_none());
    let line = csv_row("ZZZUSDT", "long", &rows[0]);
    assert_eq!(line.split(',').count(), header().split(',').count());
    assert!(line.contains(",False,"));
    assert_eq!(META.len() + NAMES.len() + 1, header().split(',').count());
    Ok(())
}
