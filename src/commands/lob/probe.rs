//! `lob probe` — CLI-обёртка распределения RTT post-only ордера (шаг 6.2).
//! Сам цикл и сводка — `crate::bybit::probe`; здесь аргументы, разбор
//! стороны, фикстура приватного REST для тестов без сети, запись CSV.

use std::path::PathBuf;

use clap::Args;

use crate::bybit::probe::{
    run_cycles, summarize, BybitPrivateRest, OrderSide, PrivateRest, ProbeError, ProbeParams,
    RttSummary, SignedRequest, DEFAULT_RECV_WINDOW_MS, MIN_CYCLES,
};
use crate::bybit::rest::BYBIT_MAINNET_URL;
use crate::bybit::sign::Credentials;

// ---------------------------------------------------------------------------
// `lob probe` (шаг 6.2, Decision 12).
// ---------------------------------------------------------------------------

/// Аргументы `lob probe`: ≥1000 циклов post-only минимального размера далеко
/// от середины, распределение RTT, медиана и p95. Середину читает вызывающий
/// с живого стакана и передаёт готовым числом (модуль зонда тик не читает).
#[derive(Debug, Args)]
pub struct ProbeArgs {
    /// Символ, например `SOLUSDT`.
    #[arg(long)]
    pub symbol: String,
    /// Сторона ордера: `buy` или `sell`.
    #[arg(long, default_value = "buy")]
    pub side: String,
    /// `lotSizeFilter.minOrderQty` инструмента (Decision 22).
    #[arg(long)]
    pub qty_e9: i64,
    /// `priceFilter.tickSize` инструмента.
    #[arg(long)]
    pub tick_e9: i64,
    /// Смещение цены от середины в тиках («далеко» — больше спреда).
    #[arg(long, default_value_t = 100)]
    pub ticks_from_mid: i64,
    /// Окно подписи Bybit по умолчанию — пять секунд (документация биржи).
    #[arg(long, default_value_t = DEFAULT_RECV_WINDOW_MS)]
    pub recv_window_ms: u32,
    /// Число циклов, не меньше `MIN_CYCLES` (done-condition шага 6.2).
    #[arg(long, default_value_t = MIN_CYCLES)]
    pub cycles: usize,
    /// Середина книги в момент решения, в 1e-9.
    #[arg(long)]
    pub mid_e9: i64,
    /// REST-хост Bybit v5 (приватные запросы).
    #[arg(long, default_value = BYBIT_MAINNET_URL)]
    pub base_url: String,
    /// Корень для `probe-<symbol>.csv` по умолчанию.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Куда писать строки циклов (по умолчанию `<root>/probe-<symbol>.csv`).
    #[arg(long)]
    pub out: Option<PathBuf>,
    /// Фикстура без сети и без ключей на бирже: консервные ответы через те же
    /// `run_cycles`/`summarize`. Подпись считается настоящим кодом.
    #[arg(long, default_value_t = false)]
    pub fixture: bool,
}

fn parse_probe_side(s: &str) -> anyhow::Result<OrderSide> {
    match s.to_ascii_lowercase().as_str() {
        "buy" => Ok(OrderSide::Buy),
        "sell" => Ok(OrderSide::Sell),
        other => Err(anyhow::anyhow!(
            "сторона обязана быть buy или sell, получено {other}"
        )),
    }
}

/// Консервный транспорт для `--fixture`: успешное создание и успешная отмена
/// с непустым `orderId` — тот же контракт ответа, что ждёт `run_cycle`.
struct FixturePrivateRest;

impl PrivateRest for FixturePrivateRest {
    fn send(&mut self, req: SignedRequest) -> Result<String, ProbeError> {
        match req.path {
            "/v5/order/create" | "/v5/order/cancel" => Ok(
                "{\"retCode\":0,\"retMsg\":\"OK\",\"result\":{\"orderId\":\"fixture-1\"},\"retExtInfo\":{},\"time\":1}"
                    .to_string(),
            ),
            other => Err(ProbeError::Decode(format!(
                "фикстура не знает путь {other}"
            ))),
        }
    }
}

/// Прогон зонда: `run_cycles` тем же кодом, что живой замер, сводка —
/// `summarize`, строки циклов — в CSV. Меньше `MIN_CYCLES` — отказ тем же
/// вариантом ошибки, что `probe::probe`.
pub fn run_probe(args: &ProbeArgs) -> anyhow::Result<(RttSummary, PathBuf)> {
    if args.cycles < MIN_CYCLES {
        return Err(anyhow::anyhow!(
            "{:?}",
            ProbeError::TooFewCycles {
                requested: args.cycles,
                minimum: MIN_CYCLES,
            }
        ));
    }
    let creds = Credentials::from_env()
        .map_err(|e| anyhow::anyhow!("ключи: {e:?} — выставьте BYBIT_API_KEY/BYBIT_API_SECRET"))?;
    let params = ProbeParams {
        symbol: args.symbol.clone(),
        side: parse_probe_side(&args.side)?,
        qty_e9: args.qty_e9,
        tick_e9: args.tick_e9,
        ticks_from_mid: args.ticks_from_mid,
        recv_window_ms: args.recv_window_ms,
    };
    let mid = args.mid_e9;
    let cycles = if args.fixture {
        let mut fx = FixturePrivateRest;
        run_cycles(&mut fx, &creds, &params, || mid, args.cycles)
            .map_err(|e| anyhow::anyhow!("{e:?}"))?
    } else {
        let mut rest =
            BybitPrivateRest::new(args.base_url.clone()).map_err(|e| anyhow::anyhow!("{e:?}"))?;
        run_cycles(&mut rest, &creds, &params, || mid, args.cycles)
            .map_err(|e| anyhow::anyhow!("{e:?}"))?
    };
    let summary = summarize(&cycles);
    let out = args
        .out
        .clone()
        .unwrap_or_else(|| args.root.join(format!("probe-{}.csv", args.symbol)));
    if let Some(parent) = out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let mut w = csv::Writer::from_path(&out)?;
    w.write_record(["cycle", "rtt_ns"])?;
    for (i, c) in cycles.iter().enumerate() {
        w.write_record([i.to_string(), c.rtt_ns().to_string()])?;
    }
    w.flush()?;
    Ok((summary, out))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn probe_args(root: &std::path::Path, cycles: usize) -> ProbeArgs {
        ProbeArgs {
            symbol: "SOLUSDT".to_string(),
            side: "buy".to_string(),
            qty_e9: 1_000_000,
            tick_e9: super::super::test_support::FIX_TICK_E9,
            ticks_from_mid: 100,
            recv_window_ms: 5000,
            cycles,
            mid_e9: 100_000_000_000,
            base_url: "http://127.0.0.1:1".to_string(),
            root: root.to_path_buf(),
            out: None,
            fixture: true,
        }
    }

    #[test]
    fn probe_fixture_writes_distribution() {
        crate::bybit::sign::with_cleared_env(|| {
            std::env::set_var(crate::bybit::sign::API_KEY_VAR, "fixture");
            std::env::set_var(crate::bybit::sign::API_SECRET_VAR, "fixture");
            let dir = tempfile::tempdir().unwrap();
            let (summary, out) = run_probe(&probe_args(dir.path(), 1000)).unwrap();
            assert_eq!(summary.n, 1000);
            assert!(summary.median_ns > 0);
            assert!(summary.p95_ns >= summary.median_ns);
            assert!(out.exists());
        });
    }

    #[test]
    fn probe_rejects_fewer_than_1000_cycles() {
        let dir = tempfile::tempdir().unwrap();
        assert!(run_probe(&probe_args(dir.path(), 10)).is_err());
    }
}
