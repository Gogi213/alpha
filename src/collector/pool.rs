//! Правило пула G-022, одно на все биржи: оборот за сутки не ниже порога, без `top_exclude` крупнейших по обороту.
//!
//! Делает: `rank_pool`, `balanced_chunks` (равные куски не больше `per_conn`), `http_get_json` (REST на момент старта).
//! Не знает биржу: отбор торгуемых контрактов и источник оборота — в адаптере.

use std::collections::BTreeMap;
use std::time::Duration;

use super::exchange::RestError;

/// `qv` — {символ: оборот за сутки, $} уже отобранных контрактов. Топ — по убыванию оборота (при равенстве — по имени);
/// пул — отсортированные имена с оборотом ≥ `min_qv`.
pub fn rank_pool(
    qv: &BTreeMap<String, f64>,
    min_qv: f64,
    top_exclude: usize,
) -> (Vec<String>, Vec<String>) {
    let mut ranked: Vec<(&String, f64)> = qv.iter().map(|(k, v)| (k, *v)).collect();
    ranked.sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(b.0)));
    let top: Vec<String> = ranked
        .iter()
        .take(top_exclude)
        .map(|(k, _)| (*k).clone())
        .collect();
    let mut pool: Vec<String> = ranked
        .iter()
        .skip(top_exclude)
        .filter(|(_, v)| *v >= min_qv)
        .map(|(k, _)| (*k).clone())
        .collect();
    pool.sort();
    (pool, top)
}

/// Куски не длиннее `per_conn` и почти равного размера (хвост из нескольких символов даёт разреженный шард: пауза данных
/// для него естественна — TK-532).
pub fn balanced_chunks<T: Clone>(items: &[T], per_conn: usize) -> Vec<Vec<T>> {
    if items.is_empty() || per_conn == 0 {
        return Vec::new();
    }
    let n = items.len().div_ceil(per_conn);
    let size = items.len().div_ceil(n);
    items.chunks(size).map(<[T]>::to_vec).collect()
}

/// GET → JSON на момент старта (пул): короткая среда выполнения, не горячий путь.
pub fn http_get_json(url: &str) -> Result<serde_json::Value, String> {
    http_get_json_ua(url, None)
}

/// То же со своим `User-Agent`: OKX за Cloudflare отвечает `403 error code: 1010` на запрос без него (замер 12.11).
pub fn http_get_json_ua(url: &str, user_agent: Option<&str>) -> Result<serde_json::Value, String> {
    http_get_json_with(url, user_agent, &[])
}

/// То же с дополнительными заголовками запроса (Gate: `X-Gate-Size-Decimal: 1` — иначе размеры приходят целыми).
pub fn http_get_json_with(
    url: &str,
    user_agent: Option<&str>,
    headers: &[(&str, &str)],
) -> Result<serde_json::Value, String> {
    http_get_rest(url, user_agent, headers).map_err(|e| e.msg)
}

/// GET → JSON для дозапроса: сбой несёт признак «отказ по частоте» (HTTP 429/418 и срок из `Retry-After`) — поток дозапроса по нему встаёт целиком.
pub fn http_get_rest(
    url: &str,
    user_agent: Option<&str>,
    headers: &[(&str, &str)],
) -> Result<serde_json::Value, RestError> {
    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| RestError::from(e.to_string()))?;
    rt.block_on(async {
        let mut builder = reqwest::Client::builder().timeout(Duration::from_secs(30));
        if let Some(ua) = user_agent {
            builder = builder.user_agent(ua);
        }
        let client = builder
            .build()
            .map_err(|e| RestError::from(e.to_string()))?;
        let mut req = client.get(url);
        for (k, v) in headers {
            req = req.header(*k, *v);
        }
        let resp = req
            .send()
            .await
            .map_err(|e| RestError::from(format!("{url}: {e}")))?;
        let status = resp.status();
        if !status.is_success() {
            let msg = format!("{url}: HTTP {status}");
            if is_rate_limit_status(status.as_u16()) {
                let retry_after = resp
                    .headers()
                    .get(reqwest::header::RETRY_AFTER)
                    .and_then(|h| h.to_str().ok())
                    .and_then(|s| s.trim().parse::<u64>().ok())
                    .map(Duration::from_secs);
                return Err(RestError::limited(msg, retry_after));
            }
            return Err(RestError::from(msg));
        }
        resp.json::<serde_json::Value>()
            .await
            .map_err(|e| RestError::from(format!("{url}: {e}")))
    })
}

/// HTTP-статусы «слишком часто»: 429 и 418 (Binance: бан IP после продолжения запросов при 429).
pub fn is_rate_limit_status(code: u16) -> bool {
    code == 429 || code == 418
}
