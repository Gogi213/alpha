//! Правило пула G-022, одно на все биржи: оборот за сутки не ниже порога, без `top_exclude` крупнейших по обороту.
//!
//! Делает: `rank_pool`, `balanced_chunks` (равные куски не больше `per_conn`), `http_get_json` (REST на момент старта).
//! Не знает биржу: отбор торгуемых контрактов и источник оборота — в адаптере.

use std::collections::BTreeMap;
use std::time::Duration;

/// `qv` — {символ: оборот за сутки, $} уже отобранных контрактов. Топ — по убыванию оборота (при равенстве — по имени);
/// пул — отсортированные имена с оборотом ≥ `min_qv`.
pub fn rank_pool(qv: &BTreeMap<String, f64>, min_qv: f64, top_exclude: usize) -> (Vec<String>, Vec<String>) {
    let mut ranked: Vec<(&String, f64)> = qv.iter().map(|(k, v)| (k, *v)).collect();
    ranked.sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(b.0)));
    let top: Vec<String> = ranked.iter().take(top_exclude).map(|(k, _)| (*k).clone()).collect();
    let mut pool: Vec<String> = ranked.iter().skip(top_exclude).filter(|(_, v)| *v >= min_qv).map(|(k, _)| (*k).clone()).collect();
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
    let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().map_err(|e| e.to_string())?;
    rt.block_on(async {
        let client = reqwest::Client::builder().timeout(Duration::from_secs(30)).build().map_err(|e| e.to_string())?;
        let resp = client.get(url).send().await.map_err(|e| format!("{url}: {e}"))?;
        if !resp.status().is_success() {
            return Err(format!("{url}: HTTP {}", resp.status()));
        }
        resp.json::<serde_json::Value>().await.map_err(|e| format!("{url}: {e}"))
    })
}
