//! Пул сессии: `instruments.csv` последнего `lob pick` (`--pool-instruments`)
//! или все linear USDT-перпетуалы биржи (`--all-instruments`, таск 28).
//! Отдельно от цикла записи — единственное место, где сессия читает CSV и
//! REST `instruments-info`; в горячий путь ничего из этого не попадает.

use std::path::Path;

use crate::feed::live::PoolMember;

use super::SessionArgs;

/// Одна строка `symbol,tick_size,...` из `instruments.csv` — читаем только
/// то, что нужно сессии, тик и шаг размера, тем же способом, что
/// `commands::record::load_steps_for_symbol` читает для одного символа.
#[derive(Debug, serde::Deserialize)]
struct InstrumentRow {
    symbol: String,
    tick_size: String,
    qty_step: String,
}

pub(super) fn load_pool(path: &Path) -> anyhow::Result<Vec<PoolMember>> {
    // Единственный читатель `instruments.csv` (дозапрос по ревью таска 08,
    // ось Craft) — терпит метку `debug` первой строкой, голый
    // `csv::Reader::from_path` читал бы её как заголовок и падал здесь же.
    let mut r = crate::commands::lob::pick::instruments_csv_reader(path)
        .map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?;
    let mut pool = Vec::new();
    for row in r.deserialize::<InstrumentRow>() {
        let row = row.map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?;
        let tick_e9 = crate::bybit::ws::parse_e9(row.tick_size.trim())
            .ok_or_else(|| anyhow::anyhow!("{}: tick_size не разобрался", row.symbol))?;
        let step_e9 = crate::bybit::ws::parse_e9(row.qty_step.trim())
            .ok_or_else(|| anyhow::anyhow!("{}: qty_step не разобрался", row.symbol))?;
        pool.push(PoolMember {
            symbol: row.symbol,
            tick_e9,
            step_e9,
        });
    }
    if pool.is_empty() {
        anyhow::bail!("{}: пуст — сначала `lob pick`", path.display());
    }
    Ok(pool)
}

/// Пул сессии: либо готовый `instruments.csv` (`--pool-instruments`), либо
/// весь список биржи (`--all-instruments`, таск 28). Ровно один — вторую
/// половину условия `clap conflicts_with` не выражает, как и у
/// `--minutes`/`--pilot-minutes`/`--always-on`.
pub(super) fn resolve_pool(args: &SessionArgs) -> anyhow::Result<Vec<PoolMember>> {
    match (&args.pool_instruments, args.all_instruments) {
        (Some(path), false) => load_pool(path),
        (None, true) => load_pool_all(&args.base_url),
        (None, false) => anyhow::bail!(
            "нужен ровно один флаг пула: --pool-instruments <instruments.csv> (пул `lob pick`) \
             или --all-instruments (все linear USDT-перпетуалы биржи, R86)"
        ),
        // Не `unreachable!`: `run_session` зовут и программно
        // (`pilot.rs`), минуя разбор `clap`, и тогда `conflicts_with`
        // ничего не гарантирует — паника вместо ошибки уронила бы пилот.
        (Some(_), true) => anyhow::bail!(
            "--pool-instruments и --all-instruments взаимоисключающие: задан ровно один"
        ),
    }
}

/// Признаки linear USDT-перпетуала в торгах — те же три поля
/// `instruments-info`, по которым отбирает `pick::pool` (`quote_coin`,
/// `contract_type`, `status`). Здесь они стоят отдельно нарочно: пул
/// анализа применяет сверх них исключения §2 (не-крипто база, молодые
/// контракты, дедуп), запись — нет.
const LINEAR_QUOTE_COIN: &str = "USDT";
const LINEAR_CONTRACT_TYPE: &str = "LinearPerpetual";
const TRADING_STATUS: &str = "Trading";

fn load_pool_all(base_url: &str) -> anyhow::Result<Vec<PoolMember>> {
    let mut client = crate::bybit::rest::BybitPublicRest::new(base_url)
        .map_err(|e| anyhow::anyhow!("instruments-info: {e:?}"))?;
    let all = crate::bybit::rest::fetch_all_linear_instruments(&mut client)
        .map_err(|e| anyhow::anyhow!("instruments-info: {e:?}"))?;
    let considered = all.len();
    let pool: Vec<PoolMember> = all
        .into_iter()
        .filter(|i| {
            i.quote_coin == LINEAR_QUOTE_COIN
                && i.contract_type == LINEAR_CONTRACT_TYPE
                && i.status == TRADING_STATUS
        })
        .map(|i| PoolMember {
            symbol: i.symbol,
            tick_e9: i.tick_e9,
            step_e9: i.qty_step_e9,
        })
        .collect();
    if pool.is_empty() {
        anyhow::bail!("instruments-info вернул {considered} инструментов, ни один не linear USDT-перпетуал в торгах");
    }
    eprintln!(
        "session: --all-instruments — {} из {considered} записей instruments-info \
         (linear/{LINEAR_QUOTE_COIN}/{LINEAR_CONTRACT_TYPE}/{TRADING_STATUS}, без исключений §2)",
        pool.len()
    );
    Ok(pool)
}
