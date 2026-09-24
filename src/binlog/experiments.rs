//! Кодеки только для замеров — не часть формата (ремонт W3, ревью 23.09).
//!
//! `super::encode_frame_payload_v3` (то, что реально пишет `Writer::
//! write_frame`) — единственная кодировка формата: `PriceMode::Delta`,
//! `EvMode::Inline`. Всё остальное здесь — варианты `PriceMode::
//! IndexSimulated` (M1i, тикет 43) и `EvMode::Table` (M1e, тикет 44) плюс
//! перекодировщик v2 для замера A/B (M4б, тикет 43) — их единственный
//! вызывающий — `lob binlog-stats` (в т.ч. `--reencode`).
//!
//! Дочерний модуль `binlog`, поэтому видит `encode_frame_payload`/
//! `PriceMode`/`EvMode`/`EV_TABLE_MAX`/`Record`/`FieldBytes`/`DeltaState`/
//! кодек варинтов и `BinlogError` без переэкспорта — приватность Rust
//! пропускает потомка к приватным элементам родителя. `pub(crate)`, не
//! `pub`: эти функции не входят в pub API писателя/читателя (`binlog::mod`
//! реэкспортирует их себе как `pub(crate)`, чтобы `binlog_stats.rs` не
//! менял пути импорта этим переносом).

use super::*;

/// Симуляция «уровень индексом» — **только замер** M1i тикета 43, в формат не
/// входит (см. `PriceMode::IndexSimulated`). `indexed[i]` — кодировать ли цену
/// записи `i` индексом вместо дельты.
pub(crate) fn encode_frame_payload_v3_index_simulated(
    records: &[Record],
    indexed: &[bool],
    out: &mut Vec<u8>,
) -> FieldBytes {
    encode_frame_payload(
        records,
        PriceMode::IndexSimulated(indexed),
        EvMode::Inline,
        out,
    )
}

/// Вариант «`ev` таблицей на кадр» — **только замер** M1e тикета 44, в формат
/// не входит (см. `EvMode::Table`); таблица строится по самим записям кадра.
pub(crate) fn encode_frame_payload_v3_ev_table(
    records: &[Record],
    out: &mut Vec<u8>,
) -> FieldBytes {
    let mut table = [0u64; EV_TABLE_MAX];
    let mut len = 0usize;
    for r in records {
        if len < EV_TABLE_MAX && !table[..len].contains(&r.ev) {
            table[len] = r.ev;
            len += 1;
        }
    }
    encode_frame_payload(records, PriceMode::Delta, EvMode::Table(&table[..len]), out)
}

/// Тело кадра v2 — форма, которую пишет живой коллектор до перезапуска на
/// новый бинарник. Кодировщик существует ради замера A/B и фикстур
/// совместимости: `order_id`/`fval` в `Record` больше нет, и на их месте
/// пишутся нули — ровно те значения, что несут живые данные, поэтому
/// перекодировка обязана совпасть с файлом на диске до байта (M4б тикета 43).
pub(crate) fn encode_frame_payload_v2(records: &[Record], out: &mut Vec<u8>) -> FieldBytes {
    let mut fb = FieldBytes::default();
    let Some(first) = records.first() else {
        return fb;
    };
    let epoch_ns = first.exch_ts_ns;
    out.extend_from_slice(&epoch_ns.to_le_bytes());
    fb.epoch += FRAME_EPOCH_LEN;

    let mut st = DeltaState::new();
    for r in records {
        let before = out.len();
        write_uvarint(out, r.ev);
        fb.ev += took(out, before);
        let before = out.len();
        write_zigzag(out, r.exch_ts_ns.wrapping_sub(epoch_ns));
        fb.exch_ts += took(out, before);
        let before = out.len();
        write_zigzag(out, r.local_ts_ns.wrapping_sub(epoch_ns));
        fb.local_ts += took(out, before);
        let before = out.len();
        write_zigzag(out, r.price_ticks.wrapping_sub(st.prev_price_ticks));
        fb.price += took(out, before);
        let before = out.len();
        write_zigzag(out, r.qty_lots.wrapping_sub(st.prev_qty_lots));
        fb.qty += took(out, before);
        // Три мёртвых поля v2 — как их писала живая запись: `order_id` = 0
        // (у публичного L2-потока числового id нет), `ival` — из блочности,
        // `fval` — бит-паттерн 0.0. RPI в v2 не представим: там нет второго
        // бита у `ival`, а ненулевой `ival` старый читатель понимает как
        // блочность, поэтому кодировщик v2 его не пишет (v2 — только чтение
        // и замер, живая запись идёт в v3).
        let before = out.len();
        write_uvarint(out, 0);
        write_zigzag(out, i64::from(r.block));
        write_uvarint(out, 0);
        fb.dead_fields += took(out, before);
        st.prev_price_ticks = r.price_ticks;
        st.prev_qty_lots = r.qty_lots;
    }
    fb
}

/// Разбирает тело кадра варианта «`ev` таблицей» — **только замер** M1e
/// тикета 44 (в формат не входит, см. `EvMode::Table`). Нужен замеру и тестам
/// варианта: без обратного чтения «экономия» проверялась бы на слово, а не
/// round-trip'ом.
pub(crate) fn decode_frame_payload_v3_ev_table(payload: &[u8]) -> Result<Vec<Record>, BinlogError> {
    let epoch_ns = read_frame_epoch(payload)?;
    let mut pos = FRAME_EPOCH_LEN;
    let table_len = read_uvarint(payload, &mut pos)?;
    if table_len > EV_TABLE_MAX as u64 {
        return Err(BinlogError::Corrupt(format!(
            "таблица ev объявила {table_len} значений при потолке {EV_TABLE_MAX}"
        )));
    }
    let mut table = [0u64; EV_TABLE_MAX];
    for i in 0..table_len as usize {
        let ev = read_uvarint(payload, &mut pos)?;
        if let Some(slot) = table.get_mut(i) {
            *slot = ev;
        }
    }
    let mut st = DeltaState::new();
    let mut out = Vec::new();
    while pos < payload.len() {
        let code = read_uvarint(payload, &mut pos)?;
        let ev = if code < table_len {
            table.get(code as usize).copied().ok_or_else(|| {
                BinlogError::Corrupt(format!("код ev {code} вне таблицы {table_len}"))
            })?
        } else if code == table_len {
            read_uvarint(payload, &mut pos)?
        } else {
            return Err(BinlogError::Corrupt(format!(
                "код ev {code} больше длины таблицы {table_len}"
            )));
        };
        let exch_delta = read_zigzag(payload, &mut pos)?;
        let local_delta = read_zigzag(payload, &mut pos)?;
        let attrs = read_uvarint(payload, &mut pos)?;
        if attrs & !ATTRS_KNOWN != 0 {
            return Err(BinlogError::Corrupt(format!(
                "неизвестный бит attrs группы: {attrs:#x}"
            )));
        }
        let count = read_uvarint(payload, &mut pos)?;
        let remaining = (payload.len() - pos) as u64;
        if count > remaining {
            return Err(BinlogError::Corrupt(format!(
                "группа объявила {count} записей, а в кадре осталось {remaining} байт"
            )));
        }
        let exch_ts_ns = epoch_ns.wrapping_add(exch_delta);
        let local_ts_ns = epoch_ns.wrapping_add(local_delta);
        let block = attrs & ATTRS_BLOCK != 0;
        let rpi = attrs & ATTRS_RPI != 0;
        for _ in 0..count {
            let price_delta = read_zigzag(payload, &mut pos)?;
            let qty_delta = read_zigzag(payload, &mut pos)?;
            let price_ticks = st.prev_price_ticks.wrapping_add(price_delta);
            let qty_lots = st.prev_qty_lots.wrapping_add(qty_delta);
            st.prev_price_ticks = price_ticks;
            st.prev_qty_lots = qty_lots;
            out.push(Record {
                ev,
                exch_ts_ns,
                local_ts_ns,
                price_ticks,
                qty_lots,
                block,
                rpi,
            });
        }
    }
    Ok(out)
}
