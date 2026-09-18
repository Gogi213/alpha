//! `lob fee-rate` — ставки комиссий **аккаунта** с биржи (владелец
//! 2026-09-18: «ты уверен, что комиссии правильные у тебя на тейкера? ну и
//! мейкера тоже?»). В коде комиссии — константы `costs::MAKER_FEE_BPS` /
//! `TAKER_FEE_BPS` (В-63: условия счёта владельца 18.09; до того H4 — публичный
//! non-VIP), и они могут разойтись со счётом (смена уровня, скидки). Команда читает `/v5/account/fee-rate`
//! подписанным GET (ключи — только `BYBIT_API_KEY`/`BYBIT_API_SECRET` из
//! окружения, значения не печатаются) и печатает мейкер/тейкер в bps рядом с
//! константами кода; расхождение — повод менять константы решением В-##, не
//! молча. Вне горячего пути, сеть только здесь.

use std::time::Duration;

use clap::Args;

use crate::bybit::probe::{ProbeError, DEFAULT_RECV_WINDOW_MS};
use crate::bybit::sign::Credentials;
use crate::lob::costs::{MAKER_FEE_BPS, TAKER_FEE_BPS};

#[derive(Debug, Args)]
pub struct FeeRateArgs {
    /// Символ (`SOLUSDT`); без него биржа отдаёт ставки по категории.
    #[arg(long)]
    pub symbol: Option<String>,
    /// Категория Bybit v5 (`linear` — USDT-перпы).
    #[arg(long, default_value = "linear")]
    pub category: String,
    /// База REST.
    #[arg(long, default_value = "https://api.bybit.com")]
    pub base_url: String,
}

/// Одна строка ответа биржи: ставки в bps.
#[derive(Debug, Clone, PartialEq)]
pub struct FeeRateRow {
    pub symbol: String,
    pub maker_bps: f64,
    pub taker_bps: f64,
}

/// Разбор тела `/v5/account/fee-rate`: `result.list[].{symbol, takerFeeRate,
/// makerFeeRate}` — доли (`"0.00055"`), в bps × 10⁴. `retCode ≠ 0` — ошибка
/// с текстом биржи.
pub fn parse_fee_rate(body: &str) -> Result<Vec<FeeRateRow>, ProbeError> {
    let v: serde_json::Value =
        serde_json::from_str(body).map_err(|e| ProbeError::Decode(e.to_string()))?;
    let ret_code = v["retCode"].as_i64().unwrap_or(-1);
    if ret_code != 0 {
        return Err(ProbeError::Decode(format!(
            "retCode={ret_code} retMsg={}",
            v["retMsg"].as_str().unwrap_or("")
        )));
    }
    let list = v["result"]["list"]
        .as_array()
        .ok_or_else(|| ProbeError::Decode("нет result.list".to_string()))?;
    let rate = |row: &serde_json::Value, key: &str| -> Result<f64, ProbeError> {
        row[key]
            .as_str()
            .ok_or_else(|| ProbeError::Decode(format!("нет {key}")))?
            .parse::<f64>()
            .map(|r| r * 10_000.0)
            .map_err(|e| ProbeError::Decode(format!("{key}: {e}")))
    };
    list.iter()
        .map(|row| {
            Ok(FeeRateRow {
                symbol: row["symbol"].as_str().unwrap_or("").to_string(),
                maker_bps: rate(row, "makerFeeRate")?,
                taker_bps: rate(row, "takerFeeRate")?,
            })
        })
        .collect()
}

/// Строка запроса: подпись v5 для GET считается по `queryString` на месте
/// тела (`timestamp + api_key + recv_window + query`).
pub fn query_string(category: &str, symbol: Option<&str>) -> String {
    match symbol {
        Some(s) => format!("category={category}&symbol={s}"),
        None => format!("category={category}"),
    }
}

/// Строка отчёта: ставки биржи против констант кода.
pub fn report_lines(rows: &[FeeRateRow]) -> Vec<String> {
    let mut out = Vec::with_capacity(rows.len() + 1);
    out.push(format!(
        "код (В-63): maker={MAKER_FEE_BPS} bps taker={TAKER_FEE_BPS} bps до возврата, возврат {}%, круг(maker+taker) после возврата={} bps",
        crate::lob::costs::FEE_REBATE_SHARE * 100.0,
        crate::lob::costs::ROUNDTRIP_FEES_BPS
    ));
    for r in rows {
        let same = (r.maker_bps - MAKER_FEE_BPS).abs() < 1e-9
            && (r.taker_bps - TAKER_FEE_BPS).abs() < 1e-9;
        out.push(format!(
            "аккаунт {}: maker={} bps taker={} bps круг={} bps — {}",
            if r.symbol.is_empty() {
                "(категория)"
            } else {
                &r.symbol
            },
            r.maker_bps,
            r.taker_bps,
            r.maker_bps + r.taker_bps,
            if same {
                "совпадает с кодом"
            } else {
                "РАСХОДИТСЯ с кодом: менять константы решением В-##"
            }
        ));
    }
    out
}

/// Живой запрос: свой рантайм на один вызов, как у `BybitPrivateRest`
/// (`reqwest` без `blocking`).
pub fn fetch_fee_rate(args: &FeeRateArgs) -> Result<Vec<FeeRateRow>, ProbeError> {
    let creds = Credentials::from_env().map_err(ProbeError::Credentials)?;
    let query = query_string(&args.category, args.symbol.as_deref());
    let timestamp_ms = crate::bybit::probe::wall_clock_timestamp_ms()?;
    let signature_hex = creds
        .sign(timestamp_ms, DEFAULT_RECV_WINDOW_MS, &query)
        .map_err(ProbeError::Credentials)?;
    let url = format!("{}/v5/account/fee-rate?{query}", args.base_url);
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| ProbeError::Transport(e.to_string()))?;
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(10))
        .build()
        .map_err(|e| ProbeError::Transport(e.to_string()))?;
    let api_key = creds.api_key().to_string();
    let body = runtime.block_on(async move {
        let resp = client
            .get(url)
            .header("X-BAPI-API-KEY", api_key)
            .header("X-BAPI-TIMESTAMP", timestamp_ms.to_string())
            .header("X-BAPI-RECV-WINDOW", DEFAULT_RECV_WINDOW_MS.to_string())
            .header("X-BAPI-SIGN", signature_hex)
            .send()
            .await
            .map_err(|e| ProbeError::Transport(e.to_string()))?;
        resp.text()
            .await
            .map_err(|e| ProbeError::Transport(e.to_string()))
    })?;
    parse_fee_rate(&body)
}

pub fn run_fee_rate(args: &FeeRateArgs) -> anyhow::Result<Vec<String>> {
    let rows = fetch_fee_rate(args).map_err(|e| anyhow::anyhow!("fee-rate: {e:?}"))?;
    anyhow::ensure!(!rows.is_empty(), "fee-rate: биржа вернула пустой список");
    Ok(report_lines(&rows))
}

#[cfg(test)]
mod tests {
    use super::*;

    const BODY: &str = r#"{"retCode":0,"retMsg":"OK","result":{"list":[{"symbol":"SOLUSDT","takerFeeRate":"0.00035","makerFeeRate":"0.00014"},{"symbol":"ZECUSDT","takerFeeRate":"0.0004","makerFeeRate":"0.0001"}]},"retExtInfo":{},"time":1}"#;

    /// Доли биржи → bps; строка сравнения называет совпадение и расхождение
    /// с константами кода по имени.
    #[test]
    fn parses_rates_into_bps_and_compares_with_the_code_constants() {
        let rows = parse_fee_rate(BODY).unwrap();
        assert_eq!(rows.len(), 2);
        assert!((rows[0].maker_bps - 1.4).abs() < 1e-9 && (rows[0].taker_bps - 3.5).abs() < 1e-9);
        assert!((rows[1].maker_bps - 1.0).abs() < 1e-9 && (rows[1].taker_bps - 4.0).abs() < 1e-9);
        let lines = report_lines(&rows);
        assert!(lines[1].contains("совпадает"), "{}", lines[1]);
        assert!(lines[2].contains("РАСХОДИТСЯ"), "{}", lines[2]);
    }

    #[test]
    fn exchange_error_and_malformed_body_are_errors_not_panics() {
        assert!(parse_fee_rate(r#"{"retCode":10003,"retMsg":"invalid api key"}"#).is_err());
        assert!(parse_fee_rate("not json").is_err());
        assert!(parse_fee_rate(r#"{"retCode":0,"result":{}}"#).is_err());
    }

    #[test]
    fn query_string_is_the_signed_payload_for_get() {
        assert_eq!(
            query_string("linear", Some("SOLUSDT")),
            "category=linear&symbol=SOLUSDT"
        );
        assert_eq!(query_string("linear", None), "category=linear");
    }
}
