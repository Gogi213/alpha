use super::cache::*;
use super::params::Params;
use crate::statarb::panel::{GRID0_MS, MINUTE_MS};
use std::io::Cursor;

const G: i64 = GRID0_MS;
const M: i64 = MINUTE_MS;

fn candles(text: &str) -> std::io::Result<(Option<Contract>, CandleStats)> {
    parse_candles(Cursor::new(text.as_bytes()))
}

fn row(min: i64, c: f64, v: f64, q: f64, n: u32) -> String {
    format!("{},{c},{c},{c},{c},{v},{q},{n}\n", G + min * M)
}

#[test]
fn candles_holes_and_zero_volume_are_nan_with_zero_flows() {
    let text = format!(
        "t,o,h,l,c,v,qv,n\n{}{}{}",
        row(10, 2.0, 5.0, 10.0, 3),
        row(11, 2.0, 0.0, 0.0, 0),
        row(13, 4.0, 1.0, 4.0, 1)
    );
    let (c, st) = candles(&text).unwrap();
    let c = c.unwrap();
    assert_eq!((st.rows, st.zero_volume), (3, 1));
    assert_eq!(c.first_min, 10);
    assert_eq!(c.close.len(), 4);
    assert_eq!(c.close[0], 2.0);
    assert!(c.close[1].is_nan() && c.close[2].is_nan());
    assert_eq!(c.close[3], 4.0);
    assert_eq!(c.vol, vec![5.0, 0.0, 0.0, 1.0]);
    assert_eq!(c.trades, vec![3.0, 0.0, 0.0, 1.0]);
}

#[test]
fn candles_boundary_inputs() {
    assert!(candles("").unwrap().0.is_none());
    assert!(candles("t,o,h,l,c,v,qv,n\n").unwrap().0.is_none());
    // без заголовка и с \r\n и пробелами вокруг полей
    let crlf = row(0, 1.5, 1.0, 1.0, 1).replace(",1.5,", ", 1.5 ,");
    let crlf = crlf.replace('\n', "\r\n");
    assert_eq!(candles(&crlf).unwrap().0.unwrap().close, vec![1.5]);
    // время не на минуте
    let bad_t = format!("{},1,1,1,1,1,1,1\n", G + 1000);
    assert!(candles(&bad_t).is_err());
    // не по возрастанию и повтор
    let rev = format!("{}{}", row(5, 1.0, 1.0, 1.0, 1), row(4, 1.0, 1.0, 1.0, 1));
    assert!(candles(&rev).is_err());
    let dup = format!("{}{}", row(5, 1.0, 1.0, 1.0, 1), row(5, 1.0, 1.0, 1.0, 1));
    assert!(candles(&dup).is_err());
    // отрицательный объём, нечисловая цена, нулевая цена, мало полей
    assert!(candles(&row(1, 1.0, -1.0, 1.0, 1)).is_err());
    assert!(candles(&format!("{},x,1,1,1,1,1,1\n", G)).is_err());
    assert!(candles(&row(1, 0.0, 1.0, 1.0, 1)).is_err());
    assert!(candles(&format!("{},1,1,1\n", G)).is_err());
}

fn oi_text(head: &str, rows: &[(i64, &str)]) -> String {
    let mut s = format!("{head}\n");
    for (slot, vals) in rows {
        s += &format!("{},{vals}\n", G + slot * 5 * M);
    }
    s
}

const HEAD: &str = "t_ms,open_interest,open_interest_value,count_toptrader_long_short_ratio,\
sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio";

#[test]
fn oi_gap_is_nan_and_header_case_ignored() {
    let text = oi_text(
        &HEAD.to_uppercase(),
        &[(100, "1,2,3,4,5,6"), (103, "7,8,9,10,11,12")],
    );
    let mut c = Contract::default();
    assert_eq!(parse_oi(Cursor::new(text.as_bytes()), &mut c).unwrap(), 2);
    assert_eq!(c.oi_first_slot, 100);
    assert_eq!(c.oi[0].len(), 4);
    assert_eq!((c.oi[0][0], c.oi[5][3]), (1.0, 12.0));
    assert!(c.oi[2][1].is_nan() && c.oi[2][2].is_nan());
}

#[test]
fn oi_empty_field_is_nan_and_bad_inputs_rejected() {
    let ok = oi_text(HEAD, &[(0, "1,,3,4,5,6")]);
    let mut c = Contract::default();
    parse_oi(Cursor::new(ok.as_bytes()), &mut c).unwrap();
    assert!(c.oi[1][0].is_nan());
    let run = |text: String| parse_oi(Cursor::new(text.into_bytes()), &mut Contract::default());
    assert!(run(String::new()).is_err());
    assert!(run(oi_text("t_ms,open_interest", &[(0, "1")])).is_err());
    assert!(run(oi_text(HEAD, &[(0, "1,2,3,4,5,-6")])).is_err());
    assert!(run(oi_text(HEAD, &[(0, "1,2,3,4,5,abc")])).is_err());
    assert!(run(oi_text(HEAD, &[(0, "1,2,3")])).is_err());
    assert!(run(oi_text(HEAD, &[(5, "1,2,3,4,5,6"), (5, "1,2,3,4,5,6")])).is_err());
    let off = format!("{HEAD}\n{},1,2,3,4,5,6\n", G + 60_000);
    assert!(run(off).is_err());
}

#[test]
fn cache_roundtrip_and_staleness() {
    let (c, _) = candles(&format!(
        "{}{}",
        row(3, 1.25, 2.0, 3.0, 4),
        row(5, 1.5, 1.0, 2.0, 1)
    ))
    .unwrap();
    let mut c = c.unwrap();
    parse_oi(
        Cursor::new(oi_text(HEAD, &[(1, "1,2,3,4,5,6")]).into_bytes()),
        &mut c,
    )
    .unwrap();
    c.funding = vec![(480, 0.0001), (960, -0.0002)];
    let mut buf = Vec::new();
    write_contract(&mut buf, &c).unwrap();
    let back = read_contract(&mut Cursor::new(&buf)).unwrap();
    assert_eq!(back.close[0].to_bits(), c.close[0].to_bits());
    assert_eq!(back.close.len(), c.close.len());
    assert!(back.close[1].is_nan());
    assert_eq!((back.vol, back.trades), (c.vol.clone(), c.trades.clone()));
    assert_eq!(back.oi_first_slot, 1);
    assert_eq!(back.oi[3], vec![4.0]);
    assert_eq!(back.funding, c.funding);
    assert!(read_contract(&mut Cursor::new(b"XXXX".to_vec())).is_err());

    let dir = std::env::temp_dir().join(format!("pmc-stale-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let (src, cache) = (dir.join("a.csv.gz"), dir.join("a.pmc"));
    std::fs::write(&src, b"x").unwrap();
    assert!(is_stale(&cache, &[&src]), "кэша нет");
    std::thread::sleep(std::time::Duration::from_millis(30));
    std::fs::write(&cache, b"y").unwrap();
    assert!(!is_stale(&cache, &[&src]));
    std::thread::sleep(std::time::Duration::from_millis(30));
    std::fs::write(&src, b"z").unwrap();
    assert!(is_stale(&cache, &[&src]), "источник новее");
    std::fs::remove_dir_all(&dir).unwrap();
}

// Усечённая копия секций docs/research/P-34-params.json (ключи и вложенность как в файле протокола;
// `_src`, `verdict`, `costs` ядро не читает).
const PARAMS: &str = r#"{
 "protocol":"P-34 r4","verdict":{"seed":34},
 "grid_min":{"value":5},
 "episodes":{"_src":"2","D_min":[90,180,360,720,1440],"quantile_X_D":0.99,
  "E4_wick":{"return_share":0.5,"within_min":15},"outlier_tail_candles_lt":3,
  "market_stratum":{"ref_symbol":"BTCUSDT","same_sign":true,"same_D":true,"min_overlap_share":0.5}},
 "features":{"windows_min":[5,15,30,60],"norm":{"history_days":7,"mad_factor":1.4826},
  "funding_z_history_days":30,"theta":[2,3,4],"ids":["F1"]},
 "onset":{"L_pre_min":360,"L_post":"t_peak"},
 "controls":{"C2a":{"moments_per_first_trigger":10},
  "C2b":{"moments_per_episode":10,"exclude_same_sign_episode_window_min":[-360,1440]},
  "sigma_1m_window_days":7},
 "label_forward":{"D_min":[90,180],"H_min":[90,360,1440]},
 "split":{"fit":["2025-01","2025-12"],"test":["2026-01","2026-10"]}
}"#;

#[test]
fn params_read_protocol_file_shape_and_reject_bad_values() {
    let p = Params::from_json(PARAMS).unwrap();
    assert_eq!(p.features.windows_min, vec![5, 15, 30, 60]);
    assert_eq!(p.episodes.d_min.len(), 5);
    assert_eq!(
        p.controls.c2b.exclude_same_sign_episode_window_min,
        [-360, 1440]
    );
    assert_eq!(p.split.test[1], "2026-10");
    let bad = |from: &str, to: &str| Params::from_json(&PARAMS.replace(from, to)).is_err();
    assert!(bad("\"grid_min\":{\"value\":5},", ""), "нет секции");
    assert!(bad("\"theta\":[2,3,4]", "\"theta\":[]"));
    assert!(bad("\"theta\":[2,3,4]", "\"theta\":[-1]"));
    assert!(bad("[5,15,30,60]", "[0,15]"));
    assert!(bad("[5,15,30,60]", "[7]"));
    assert!(bad("\"history_days\":7", "\"history_days\":0"));
    assert!(bad("\"quantile_X_D\":0.99", "\"quantile_X_D\":1.5"));
    assert!(bad("\"quantile_X_D\":0.99", "\"quantile_X_D\":\"a\""));
    assert!(bad("\"min_overlap_share\":0.5", "\"min_overlap_share\":0"));
    assert!(bad("\"min_overlap_share\":0.5", "\"min_overlap_share\":1.5"));
    assert!(bad("[-360,1440]", "[1440,-360]"));
    assert!(bad("\"L_pre_min\":360", "\"L_pre_min\":0"));
    assert!(Params::from_json("").is_err());
}
