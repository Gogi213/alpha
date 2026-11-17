//! Тесты TK-584 «символы выпадают после перезапуска» (`docs/findings/symbols-drop-after-restart-2026-11-17.md`): в подписке всё, что биржа перечисляет как торгуемое
//! (порог оборота по умолчанию 0), контракт без тикера не выпадает, символ со статусом «не торгуется» переживает перезапуск, пока биржа держит его в списке,
//! раскладка по шардам на полном списке (число соединений).

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Value};

use super::exchange::{Exchange, ShardPlan};
use super::livepool::{kind_of, PoolState, SavedState};
use super::{binance, bingx, bybit, gate, okx};

fn strings(v: &[&str]) -> Vec<String> {
    v.iter().map(|s| (*s).to_string()).collect()
}

fn set(v: &[&str]) -> BTreeSet<String> {
    v.iter().map(|s| (*s).to_string()).collect()
}

/// Ожидаемые пулы: порог 0 — все торгуемые (крупный, тонкий и без тикера; расчётный/остановленный — нет); прежний порог 300 000 — только крупный.
fn check_pool(
    ex: &str,
    all: (Vec<String>, Vec<String>),
    old_rule: (Vec<String>, Vec<String>),
    big: &str,
    thin: &str,
    notick: &str,
) {
    let mut want = vec![big.to_string(), thin.to_string(), notick.to_string()];
    want.sort();
    assert_eq!(all.0, want, "{ex}: порог 0 — все торгуемые контракты");
    assert!(all.1.is_empty(), "{ex}: топ не исключается");
    assert_eq!(
        old_rule.0,
        vec![big.to_string()],
        "{ex}: прежний порог 300 000 оставлял только крупный (так выпали тонкие после перезапуска)"
    );
}

#[test]
fn binance_pool_at_zero_threshold_keeps_thin_and_tickerless_contracts() {
    let sym = |n: &str, status: &str| json!({"symbol": n, "status": status, "contractType": "PERPETUAL", "quoteAsset": "USDT"});
    let info = json!({"symbols": [sym("BIGUSDT", "TRADING"), sym("THINUSDT", "TRADING"),
                                  sym("NOTICKUSDT", "TRADING"), sym("SETUSDT", "SETTLING")]});
    let tk = json!([{"symbol": "BIGUSDT", "quoteVolume": "5000000"}, {"symbol": "THINUSDT", "quoteVolume": "1000"},
                    {"symbol": "SETUSDT", "quoteVolume": "9000000"}]);
    check_pool(
        "binance",
        binance::select_pool(&info, &tk, 0.0, 0),
        binance::select_pool(&info, &tk, 300_000.0, 0),
        "BIGUSDT",
        "THINUSDT",
        "NOTICKUSDT",
    );
    assert_eq!(
        binance::listed_of(&info),
        set(&["BIGUSDT", "THINUSDT", "NOTICKUSDT", "SETUSDT"]),
        "список — при любом статусе"
    );
}

#[test]
fn bybit_pool_at_zero_threshold_keeps_thin_and_tickerless_contracts() {
    let ins = |n: &str, status: &str| json!({"symbol": n, "status": status, "contractType": "LinearPerpetual", "quoteCoin": "USDT"});
    let instruments = vec![
        ins("BIGUSDT", "Trading"),
        ins("THINUSDT", "Trading"),
        ins("NOTICKUSDT", "Trading"),
        ins("HALTUSDT", "Settling"),
    ];
    let tk = json!([{"symbol": "BIGUSDT", "turnover24h": "5000000"}, {"symbol": "THINUSDT", "turnover24h": "1000"},
                    {"symbol": "HALTUSDT", "turnover24h": "9000000"}]);
    check_pool(
        "bybit",
        bybit::select_pool(&instruments, &tk, 0.0, 0),
        bybit::select_pool(&instruments, &tk, 300_000.0, 0),
        "BIGUSDT",
        "THINUSDT",
        "NOTICKUSDT",
    );
    assert_eq!(bybit::listed_of(&instruments).len(), 4);
}

#[test]
fn okx_pool_at_zero_threshold_keeps_thin_and_tickerless_contracts() {
    let ins = |n: &str, state: &str| json!({"instId": n, "ctType": "linear", "settleCcy": "USDT", "state": state});
    let instruments = vec![
        ins("BIG-USDT-SWAP", "live"),
        ins("THIN-USDT-SWAP", "live"),
        ins("NOTICK-USDT-SWAP", "live"),
        ins("HALT-USDT-SWAP", "suspend"),
    ];
    let tk = vec![
        json!({"instId": "BIG-USDT-SWAP", "volCcy24h": "100", "last": "50000"}),
        json!({"instId": "THIN-USDT-SWAP", "volCcy24h": "10", "last": "100"}),
        json!({"instId": "HALT-USDT-SWAP", "volCcy24h": "9000", "last": "50000"}),
    ];
    let (all, top, _) = okx::select_pool(&instruments, &tk, 0.0, 0);
    let (old, _, _) = okx::select_pool(&instruments, &tk, 300_000.0, 0);
    check_pool(
        "okx",
        (all, top),
        (old, Vec::new()),
        "BIG-USDT-SWAP",
        "THIN-USDT-SWAP",
        "NOTICK-USDT-SWAP",
    );
    assert!(okx::listed_of(&instruments).contains("HALT-USDT-SWAP"));
}

#[test]
fn bingx_pool_at_zero_threshold_keeps_thin_and_tickerless_contracts() {
    let c = |n: &str, status: i64| json!({"symbol": n, "currency": "USDT", "status": status});
    let contracts = vec![
        c("BIG-USDT", 1),
        c("THIN-USDT", 1),
        c("NOTICK-USDT", 1),
        c("NCFXHALT-USDT", 25),
    ];
    let tk = vec![
        json!({"symbol": "BIG-USDT", "quoteVolume": "5000000"}),
        json!({"symbol": "THIN-USDT", "quoteVolume": "1000"}),
    ];
    let (all, top, _) = bingx::select_pool(&contracts, &tk, 0.0, 0);
    let (old, _, _) = bingx::select_pool(&contracts, &tk, 300_000.0, 0);
    check_pool(
        "bingx",
        (all, top),
        (old, Vec::new()),
        "BIG-USDT",
        "THIN-USDT",
        "NOTICK-USDT",
    );
    assert!(
        bingx::listed_of(&contracts).contains("NCFXHALT-USDT"),
        "status=25 — в списке"
    );
}

#[test]
fn gate_pool_at_zero_threshold_keeps_thin_and_tickerless_contracts() {
    let c = |n: &str, status: &str| json!({"name": n, "status": status});
    let contracts = vec![
        c("BIG_USDT", "trading"),
        c("THIN_USDT", "trading"),
        c("NOTICK_USDT", "trading"),
        c("HALT_USDT", "delisting"),
    ];
    let tk = vec![
        json!({"contract": "BIG_USDT", "volume_24h_quote": "5000000"}),
        json!({"contract": "THIN_USDT", "volume_24h_quote": "1000"}),
    ];
    let (all, top, _) = gate::select_pool(&contracts, &tk, 0.0, 0);
    let (old, _, _) = gate::select_pool(&contracts, &tk, 300_000.0, 0);
    check_pool(
        "gate",
        (all, top),
        (old, Vec::new()),
        "BIG_USDT",
        "THIN_USDT",
        "NOTICK_USDT",
    );
    assert!(gate::listed_of(&contracts).contains("HALT_USDT"));
}

// ---- перезапуск ----

fn saved_marks(dir: &std::path::Path, marks: &[(&str, i64)]) -> SavedState {
    let marks: BTreeMap<String, i64> = marks.iter().map(|(s, t)| ((*s).to_string(), *t)).collect();
    SavedState::save(dir, 0, 86_400, &marks, &Default::default()).unwrap();
    SavedState::load(dir).unwrap()
}

/// BingX 17.11 04:12 UTC: 59 контрактов `status=25` выпали при перезапуске (REST их не отдавал торгуемыми), хотя внутри запуска такой символ держится сутки, а биржа
/// продолжала слать по ним книгу (замер: тот же темп, цена не меняется).
#[test]
fn restart_keeps_a_symbol_the_exchange_lists_but_does_not_trade_and_drops_a_delisted_one() {
    let dir = tempfile::tempdir().unwrap();
    let st = saved_marks(
        dir.path(),
        &[
            ("NCFXHALT-USDT", 1_000),
            ("DELISTED-USDT", 1_000),
            ("OLD-USDT", 1_000),
            ("BIG-USDT", 1_000),
        ],
    );
    let contracts = vec![
        json!({"symbol": "BIG-USDT", "currency": "USDT", "status": 1}),
        json!({"symbol": "NCFXHALT-USDT", "currency": "USDT", "status": 25}),
        json!({"symbol": "OLD-USDT", "currency": "USDT", "status": 25}),
    ];
    let tk = vec![json!({"symbol": "BIG-USDT", "quoteVolume": "5000000"})];
    let (mut pool, _, _) = bingx::select_pool(&contracts, &tk, 0.0, 0);
    assert_eq!(
        pool,
        strings(&["BIG-USDT"]),
        "status=25 в пул заново не входит"
    );
    let tradable: BTreeMap<String, f64> = [("BIG-USDT".to_string(), 5e6)].into();
    let listed = bingx::listed_of(&contracts);
    let held = st.held(&pool, 3_600_000, 86_400, &tradable, &listed);
    assert_eq!(
        held.keys().cloned().collect::<Vec<_>>(),
        strings(&["NCFXHALT-USDT", "OLD-USDT"]),
        "в списке, внутри суток — держим; убран из списка — нет"
    );
    pool.extend(held.keys().cloned());
    pool.sort();
    assert_eq!(pool, strings(&["BIG-USDT", "NCFXHALT-USDT", "OLD-USDT"]));
    // сутки без торговли прошли: держать больше нельзя (как внутри запуска)
    assert!(st
        .held(
            &strings(&["BIG-USDT"]),
            90_000_000,
            86_400,
            &tradable,
            &listed
        )
        .is_empty());
}

#[test]
fn zero_threshold_does_not_make_a_symbol_stale_while_the_exchange_lists_it_as_trading() {
    let pool = strings(&["BIGUSDT", "THINUSDT"]);
    let mut st = PoolState::new(&pool, 0, 0.0, 0, 3600);
    // тонкий символ с нулевым оборотом (контракт без тикера) — торгуемый, не снимается
    let qv: BTreeMap<String, f64> = [("BIGUSDT", 5e6), ("THINUSDT", 0.0), ("NEWUSDT", 10.0)]
        .map(|(s, v)| (s.to_string(), v))
        .into();
    let r = st.review(&qv, 10 * 3_600_000).unwrap();
    assert!(r.stale.is_empty());
    assert_eq!(r.add, vec![("NEWUSDT".to_string(), 10.0)]);
    // пропал из ответа REST — снимается лишь после выдержки (здесь час)
    let qv: BTreeMap<String, f64> = [("BIGUSDT".to_string(), 5e6)].into();
    assert!(st
        .review(&qv, 10 * 3_600_000 + 1_800_000)
        .unwrap()
        .stale
        .is_empty());
    assert_eq!(st.review(&qv, 12 * 3_600_000).unwrap().stale.len(), 1);
}

// ---- раскладка полного списка ----

fn shards_by_kind(plans: &[ShardPlan]) -> BTreeMap<String, usize> {
    let mut m = BTreeMap::new();
    for p in plans {
        *m.entry(kind_of(&p.name).to_string()).or_insert(0) += 1;
    }
    m
}

fn names(n: usize) -> Vec<String> {
    (0..n).map(|i| format!("S{i:04}USDT")).collect()
}

/// Число шардов на всём списке торгуемых (REST 17.11 11:40 UTC: Binance 522, Bybit 821, OKX 529, BingX 946, Gate 1 034): каждый шард — два соединения (две линии).
#[test]
fn shard_counts_for_the_whole_trading_list() {
    let kinds = ["book", "trade", "mark"].map(String::from).to_vec();
    let cnt = |e: &dyn Exchange, n: usize| shards_by_kind(&e.plan(&names(n), &kinds, 0));
    let want = |b: usize, t: usize, m: usize| {
        BTreeMap::from([
            ("book".to_string(), b),
            ("trade".to_string(), t),
            ("mark".to_string(), m),
        ])
    };
    assert_eq!(cnt(&binance::Binance, 522), want(17, 9, 1));
    assert_eq!(cnt(&bybit::Bybit, 821), want(11, 11, 11));
    assert_eq!(
        cnt(&okx::Okx::default(), 529),
        want(6, 6, 6),
        "OKX mark — три канала на символ, 100 символов на шард"
    );
    assert_eq!(cnt(&bingx::Bingx::default(), 946), want(5, 5, 5));
    assert_eq!(cnt(&gate::Gate::default(), 1_034), want(11, 11, 11));
}

#[test]
fn listed_of_ignores_other_quote_assets_and_contract_types() {
    let info: Value = json!({"symbols": [
        {"symbol": "AUSDT", "status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDT"},
        {"symbol": "AUSD", "status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USD"},
        {"symbol": "AUSDT_250101", "status": "TRADING", "contractType": "CURRENT_QUARTER", "quoteAsset": "USDT"}]});
    assert_eq!(binance::listed_of(&info), set(&["AUSDT"]));
}
