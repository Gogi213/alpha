//! Проекция цены стены со спота на фьючерс (П-22 §3.2, TK-328): вход B —
//! «стена найдена в стакане спота, ордер ставится в стакан фьючерса».
//! Расстояние от стены до mid сохраняется в bps (правило проекта): цена
//! фьючерса `P_f = F_mid · P_wall / S_mid`, затем округление до шага цены
//! фьючерса в **пассивную** сторону — bid вниз, ask вверх, чтобы ордер не
//! пересёк рынок. Чистая функция, без ввода-вывода; на события не вызывается.

use crate::book::Side;

/// Тик стены на споте → тик фьючерса. `spot_e9`/`fut_e9` — шаг цены в 1e-9
/// валюты котировки (`tick_e9` заголовка бинлога), `s_mid`/`f_mid` — mid
/// спота и фьючерса в один момент (цена, не тики). `None` — mid не положителен
/// или не конечен, либо результат не лёг в `i64`.
///
/// При `spot_e9 == fut_e9` и `s_mid == f_mid` возвращает `price_tick` без
/// округления: множители равны ровно 1,0 (это и есть гейт «байт в байт» —
/// фьюч-сигнал, поданный фьючерсом через проекцию, не меняется).
#[must_use]
#[allow(clippy::cast_precision_loss, clippy::cast_possible_truncation)]
pub fn project_tick(
    price_tick: i64,
    side: Side,
    spot_e9: i64,
    fut_e9: i64,
    s_mid: f64,
    f_mid: f64,
) -> Option<i64> {
    if spot_e9 <= 0 || fut_e9 <= 0 || !(s_mid.is_finite() && f_mid.is_finite()) {
        return None;
    }
    if s_mid <= 0.0 || f_mid <= 0.0 {
        return None;
    }
    let x = price_tick as f64 * (spot_e9 as f64 / fut_e9 as f64) * (f_mid / s_mid);
    if !x.is_finite() {
        return None;
    }
    let r = match side {
        Side::Bid => x.floor(),
        Side::Ask => x.ceil(),
    };
    (r.abs() < 9.0e15).then_some(r as i64)
}

#[cfg(test)]
mod tests;
