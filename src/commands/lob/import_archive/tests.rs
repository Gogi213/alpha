use super::*;
use crate::binlog::Reader;

const DAY: &str = "2026-09-21";
const START: i64 = 1_789_948_800_000; // 2026-09-21 00:00:00 UTC, мс

fn msg(kind: &str, ts: i64, cts: i64, u: u64, b: &str, a: &str) -> String {
    format!(
        r#"{{"topic":"orderbook.200.TESTUSDT","type":"{kind}","ts":{ts},"data":{{"s":"TESTUSDT","b":[{b}],"a":[{a}],"u":{u},"seq":{u}}},"cts":{cts}}}"#
    )
}

fn fixture(dir: &Path) -> ImportArchiveArgs {
    std::fs::write(
        dir.join("instruments.csv"),
        "symbol,tick_size,min_order_qty,qty_step,min_notional_value\nTESTUSDT,0.01,0.1,0.1,5\n",
    )
    .unwrap();
    let ob = [
        msg(
            "snapshot",
            START + 500,
            START + 480,
            1,
            r#"["1.00","5"],["0.99","2.5"]"#,
            r#"["1.01","3"]"#,
        ),
        msg(
            "delta",
            START + 1500,
            START + 1490,
            2,
            r#"["1.00","4.2"]"#,
            "",
        ),
        // разрыв номера: 2 → 4
        msg(
            "delta",
            START + 2500,
            START + 2490,
            4,
            "",
            r#"["1.01","0"],["1.02","1"]"#,
        ),
        // снимок следующих суток — не пишется
        msg(
            "snapshot",
            START + 86_400_500,
            START + 86_400_480,
            1,
            r#"["1.00","5"]"#,
            r#"["1.01","3"]"#,
        ),
    ]
    .join("\n");
    std::fs::write(dir.join("ob.data"), ob).unwrap();
    let s = |ms: i64| format!("{}.{:03}0", ms.div_euclid(1000), ms.rem_euclid(1000));
    std::fs::write(
        dir.join("trades.csv"),
        format!(
            "timestamp,symbol,side,size,price,tickDirection,trdMatchID,grossValue,homeNotional,foreignNotional,RPI\n\
             {},TESTUSDT,Buy,1,1.01,PlusTick,a,0,1,1,0\n\
             {},TESTUSDT,Sell,0.8,1.00,MinusTick,b,0,0.8,0.8,0\n\
             {},TESTUSDT,Buy,0.3,1.01,PlusTick,c,0,0.3,0.3,1\n\
             {},TESTUSDT,Sell,1,1.00,MinusTick,d,0,1,1,0\n",
            s(START + 100),
            s(START + 1000),
            s(START + 2000),
            s(START + 86_400_100)
        ),
    )
    .unwrap();
    ImportArchiveArgs {
        symbol: "TESTUSDT".to_string(),
        day: DAY.to_string(),
        ob: dir.join("ob.data"),
        trades: dir.join("trades.csv"),
        instruments: dir.join("instruments.csv"),
        root: dir.join("root"),
    }
}

/// Сутки архива → бинлог: снимок первым кадром, сделки вперемежку со стаканом по времени
/// (стакан — по `ts`, сделка — по `T`), сделка до снимка и сообщения следующих суток отброшены,
/// разрыв `u` посчитан; поля записей — как у коллектора (тики, шаги, `exch_ts` = `cts`).
#[test]
fn archive_day_becomes_a_binlog_in_arrival_order() {
    let dir = tempfile::tempdir().unwrap();
    let args = fixture(dir.path());
    let sum = run_import_archive(&args).unwrap();
    assert_eq!(sum.snapshots, 1);
    assert_eq!(sum.messages, 3);
    assert_eq!(sum.u_gaps, 1);
    assert_eq!(sum.trades, 2);
    assert_eq!(sum.trades_before_snapshot, 1);
    assert_eq!(sum.dropped_after_day, 1);

    let data = std::fs::read(&sum.out).unwrap();
    let mut r = Reader::open(&data[..]).unwrap();
    assert_eq!(r.header().tick_e9, 10_000_000);
    assert_eq!(r.header().step_e9, 100_000_000);
    let mut frames = Vec::new();
    while let Some(f) = r.read_frame().unwrap() {
        frames.push(f);
    }
    // Первый кадр — снимок: три уровня, флаги снимка, цена в тиках, размер в шагах.
    let snap = &frames[0];
    assert_eq!(snap.len(), 3);
    assert!(snap
        .iter()
        .all(|x| x.ev == LOCAL_BID_DEPTH_SNAPSHOT_EVENT || x.ev == LOCAL_ASK_DEPTH_SNAPSHOT_EVENT));
    assert_eq!((snap[0].price_ticks, snap[0].qty_lots), (100, 50));
    assert_eq!((snap[1].price_ticks, snap[1].qty_lots), (99, 25));
    assert_eq!(snap[0].exch_ts_ns, (START + 480) * 1_000_000);
    assert_eq!(snap[0].local_ts_ns, (START + 500) * 1_000_000);
    let rest: Vec<Record> = frames[1..].iter().flatten().copied().collect();
    let kinds: Vec<u64> = rest.iter().map(|x| x.ev).collect();
    assert_eq!(
        kinds,
        vec![
            LOCAL_SELL_TRADE_EVENT, // T = +1000, перед дельтой ts = +1500
            LOCAL_BID_DEPTH_EVENT,  // +1500
            LOCAL_BUY_TRADE_EVENT,  // T = +2000, RPI
            LOCAL_ASK_DEPTH_EVENT,  // +2500: 1.01 → 0
            LOCAL_ASK_DEPTH_EVENT,  // +2500: 1.02 → 1
        ]
    );
    assert_eq!((rest[0].price_ticks, rest[0].qty_lots), (100, 8));
    assert_eq!(rest[0].local_ts_ns, (START + 1000) * 1_000_000);
    assert!(rest[2].rpi && !rest[0].rpi);
    assert_eq!((rest[3].price_ticks, rest[3].qty_lots), (101, 0));

    // Повтор — отказ: импорт не перезаписывает сутки.
    assert!(run_import_archive(&args).is_err());
}

#[test]
fn trade_time_keeps_milliseconds_and_drops_the_rest() {
    assert_eq!(trade_ms("1789948800.4366"), Some(1_789_948_800_436));
    assert_eq!(trade_ms("1789948800.5"), Some(1_789_948_800_500));
    assert_eq!(trade_ms("1789948800"), Some(1_789_948_800_000));
    assert_eq!(trade_ms("x.1"), None);
}

/// Выгрузка сделок пишет величины и в экспоненте (`1.1283e+06` — AKE 01.09): разбор точный, без `f64`.
#[test]
fn trade_numbers_in_exponent_form_parse_exactly() {
    assert_eq!(parse_decimal_e9("1.1283e+06"), Some(1_128_300_000_000_000));
    assert_eq!(parse_decimal_e9("1900"), Some(1_900_000_000_000));
    assert_eq!(parse_decimal_e9("5e-05"), Some(50_000));
    assert_eq!(parse_decimal_e9("0.0082300"), Some(8_230_000));
    assert_eq!(parse_decimal_e9("1e-10"), None, "точнее 1e-9 — отказ");
}
