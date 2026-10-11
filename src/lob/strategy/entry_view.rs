//! Лучшие цены стороны, снимок входа, сдвиг уровня и «цена на уровне».

use super::*;

/// Единственная конверсия «целый тик → цена `f64`» на стороне стратегии (С-11):
/// `тик × шаг`, как собирает цену и сам `MarketDepth`. Остальные места берут
/// цену отсюда, а не вторым умножением.
#[allow(clippy::cast_precision_loss)]
pub(super) fn tick_to_px(tick: i64, tick_px: f64) -> f64 {
    tick as f64 * tick_px
}

/// Лучшие цены стороны целыми тиками `(bid, ask)`, `None` — книга неполна
/// (`best_bid_tick`/`best_ask_tick` ещё на `INVALID_MIN`/`INVALID_MAX`,
/// до первого снапшота) или цена не положительна.
pub(super) fn best_ticks<MD: MarketDepth>(depth: &MD) -> Option<(i64, i64)> {
    let (bid, ask) = (depth.best_bid_tick(), depth.best_ask_tick());
    if bid == INVALID_MIN || ask == INVALID_MAX || bid <= 0 || ask <= 0 {
        return None;
    }
    Some((bid, ask))
}

/// Снимок ног входа по ордерам крейта (F4, В-78): накопленное исполненное
/// (`qty − leaves_qty`; у крейта `exec_qty` — объём **последнего** исполнения,
/// а не сумма), стоимость исполненного (`executed_notional` — по всем
/// исполнениям ноги, R7) и признак
/// «вход ещё решается» — в рынке стоит хоть одна нога или её запрос
/// (постановка/снятие) ещё не подтверждён биржей.
///
/// `Bot::position` крейта для этого не годится: `Local` обновляет позицию
/// только на `status == Filled`, поэтому частично исполненная нога (модель
/// очереди по объёму, F3) остаётся `PartiallyFilled` и позиции не даёт —
/// находка F3. Считается по ордерам (`orders(asset).get(&id)`), без кучи и без
/// `HashMap`: ног у плана единицы, лишних структур на пути события нет.
pub(super) struct EntrySnapshot {
    /// Накопленное исполненное по всем ногам входа.
    pub(super) qty: f64,
    /// Накопленная стоимость исполненного по всем исполнениям ног (R7).
    pub(super) notional: f64,
    /// Вход ещё решается: нога стоит в рынке или её запрос летит.
    pub(super) open: bool,
}

/// Снять снимок ног входа `first_id .. first_id + legs`. Нога, которой в учёте
/// крейта уже нет (биржа её не знает — терминальна и снята с учёта), вход не
/// держит: считать её «решается» значило бы ждать её вечно.
pub(super) fn entry_snapshot<MD, B>(
    bot: &B,
    asset_no: usize,
    first_id: u64,
    legs: u8,
) -> EntrySnapshot
where
    MD: MarketDepth,
    B: Bot<MD>,
{
    let mut snap = EntrySnapshot {
        qty: 0.0,
        notional: 0.0,
        open: false,
    };
    for i in 0..u64::from(legs.max(1)) {
        let Some(order) = bot.orders(asset_no).get(&first_id.saturating_add(i)) else {
            continue;
        };
        if order.req != Status::None
            || matches!(order.status, Status::New | Status::PartiallyFilled)
        {
            snap.open = true;
        }
        let executed = executed_qty(order);
        if executed > 0.0 {
            snap.qty += executed;
            snap.notional += executed_notional(order);
        }
    }
    snap
}

/// Сдвиг уровней стопа и тейка к **средней цене исполненного** входа (F4,
/// В-78). Форма задаёт стоп и тейк расстояниями от цены входа, поэтому, когда
/// реальная средняя другая (частичное исполнение, лестница), оба уровня
/// сдвигаются на ту же разницу — расстояния формы сохраняются.
///
/// Средняя приводится к сетке тиков (цены живут на тиках, так же поступает
/// крейт при постановке), поэтому сдвиг — целое число тиков; при полном
/// исполнении по плановой цене он **ровно ноль** (обе величины — произведение
/// одного и того же целого числа тиков на шаг), и числа прежних прогонов не
/// меняются — на этом стоит гейт «те же круги» (F3/F4).
pub(super) fn level_shift(entry_vwap: Option<f64>, entry_px: f64, tick_px: f64) -> f64 {
    let Some(vwap) = entry_vwap else {
        return 0.0;
    };
    if !vwap.is_finite() || !entry_px.is_finite() || tick_px <= 0.0 {
        return 0.0;
    }
    let snapped = round_half_away(vwap / tick_px) * tick_px;
    if (snapped - entry_px).abs() < tick_px * 0.5 {
        0.0
    } else {
        snapped - entry_px
    }
}

/// Стоит ли лучшая цена стороны **на уровне касания** (B4, В-58 п. 5):
/// «касание не кончилось» — цена не ушла ни в отскок, ни сквозь уровень.
/// Сравнение целыми тиками (С-11): тик стороны равен тику уровня, допуска нет.
/// Уровень берётся со стороны входа: у лонга (бид-уровень) это лучший бид, у
/// шорта — лучший аск. Выключенный или неполный план (`tick_px = 0`) не даёт
/// «прилипания»: считать его на неизвестном шаге — выдумывать признак.
pub(super) fn still_at_level(quotes: Quotes, level_px: f64, tick_px: f64) -> bool {
    if tick_px <= 0.0 || level_px <= 0.0 {
        return false;
    }
    let side_tick = match quotes.entry_side {
        HbtSide::Buy => quotes.bid_tick,
        _ => quotes.ask_tick,
    };
    side_tick == px_tick(level_px, tick_px)
}
