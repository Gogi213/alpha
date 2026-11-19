//! TK-609 (v6): сутки склада «сверткой» — `(off, цена ×1e8, bm, количество)` на сделку прямо из распакованных блоков, без `Vec<Tick>`,
//! `Vec<u64>` и массивов признаков. Нужна признакам, которым от суток требуется только гистограмма (`flow_coin`, сутки без эпизодов).
//! Разбор — тот же, что у `decode` и `decode_qty` (те же проверки, тот же порядок значений); блоки распаковываются в буферы потока,
//! которые живут между сутками (нет выделения и страничных сбоев на каждые сутки).

use super::{get, parse_head, SrcMeta, TickStore, DAY_MS, HEAD_LEN};
use crate::breakout::tick::LoadStat;
use std::cell::RefCell;
use std::fs;
use std::io::Read;
use std::sync::atomic::Ordering;

/// Заголовок суток: то, что потребитель проверяет до разбора записей.
#[derive(Debug, Clone)]
pub struct DayHead {
    pub n: usize,
    pub flags: u32,
    pub qty_dec: u8,
    pub stat: LoadStat,
    /// Время первой записи, мс от полуночи суток (`None` — записей нет).
    pub first_off: Option<u32>,
}

struct Scratch {
    body: Vec<u8>,
    a: Vec<u8>,
    q: Vec<u8>,
    dec: Option<zstd::bulk::Decompressor<'static>>,
}

thread_local! {
    static SCRATCH: RefCell<Scratch> = const {
        RefCell::new(Scratch { body: Vec::new(), a: Vec::new(), q: Vec::new(), dec: None })
    };
}

/// Читает блок `comp` байт и распаковывает ровно в `raw` байт.
fn unpack(
    f: &mut fs::File,
    comp: usize,
    raw: usize,
    body: &mut Vec<u8>,
    out: &mut Vec<u8>,
    dec: &mut zstd::bulk::Decompressor<'static>,
) -> Option<()> {
    body.resize(comp, 0);
    f.read_exact(body).ok()?;
    out.resize(raw, 0);
    (dec.decompress_to_buffer(&body[..], &mut out[..]).ok()? == raw).then_some(())
}

impl TickStore {
    /// Сутки со склада свёрткой: `start` получает заголовок и возвращает функцию на сделку `(off, px, bm, qty)` либо `None` — суткам
    /// не подходит (флаги точности и т. п.), записи не разбираются. `None` и при промахе, устаревшем, повреждённом файле и обрыве
    /// разбора: счётчики склада не меняются, вызывающий читает сутки обычным путём (`read_day`), который их учтёт. После `None` из
    /// разбора функция на сделку могла уже сработать на части записей — вызывающий отбрасывает её результат.
    pub fn fold_day<F: FnMut(u32, i64, bool, u64)>(
        &self,
        name: &str,
        date: &str,
        meta: &SrcMeta,
        start: impl FnOnce(&DayHead) -> Option<F>,
    ) -> Option<DayHead> {
        let p = self.path(name, date);
        let head = SCRATCH.with(|s| {
            let sc = &mut *s.borrow_mut();
            fold_file(&p, meta, sc, start)
        })?;
        self.hit.fetch_add(1, Ordering::Relaxed);
        Some(head)
    }
}

fn fold_file<F: FnMut(u32, i64, bool, u64)>(
    p: &std::path::Path,
    meta: &SrcMeta,
    sc: &mut Scratch,
    start: impl FnOnce(&DayHead) -> Option<F>,
) -> Option<DayHead> {
    let mut f = fs::File::open(p).ok()?;
    let mut hb = [0u8; HEAD_LEN];
    f.read_exact(&mut hb).ok()?;
    let h = parse_head(&hb, meta).ok()?;
    if f.metadata().ok()?.len() != (HEAD_LEN + h.comp_a + h.comp_q) as u64 {
        return None;
    }
    let mut head = DayHead {
        n: h.n,
        flags: h.flags,
        qty_dec: h.qty_dec,
        stat: h.st,
        first_off: None,
    };
    let mut each = start(&head)?;
    let Scratch { body, a, q, dec } = sc;
    if dec.is_none() {
        *dec = Some(zstd::bulk::Decompressor::new().ok()?);
    }
    let dec = dec.as_mut()?;
    unpack(&mut f, h.comp_a, h.raw_a, body, a, dec)?;
    unpack(&mut f, h.comp_q, h.raw_q, body, q, dec)?;
    let (b, qb) = (&a[..], &q[..]);
    let mut px = i64::from_le_bytes(b.get(0..8)?.try_into().ok()?);
    let g = i64::try_from(u64::from_le_bytes(b.get(8..16)?.try_into().ok()?)).ok()?;
    let (mut off, mut i, mut j) = (0u64, 16usize, 0usize);
    for k in 0..h.n {
        let z = get(b, &mut i)?;
        px = px.wrapping_add(((z >> 1) as i64 ^ -((z & 1) as i64)).wrapping_mul(g));
        let w = get(b, &mut i)?;
        off += w >> 1;
        if off >= DAY_MS as u64 {
            return None;
        }
        let qty = get(qb, &mut j)?.checked_mul(h.qty_step)?;
        if k == 0 {
            head.first_off = Some(off as u32);
        }
        each(off as u32, px, w & 1 == 1, qty);
    }
    (i == b.len() && j == qb.len()).then_some(head)
}
