//! Разжатие мелких gzip-кадров без библиотеки (TK-580): быстрый путь `Gunzip` для кадров BingX.
//!
//! Зачем. Кадры BingX крошечные — разжато 134–240 Б, сжато 130–180 Б (замер 17.11, 135 тыс. настоящих кадров, `docs/findings/bingx-load-sensitivity-2026-11-17.md`),
//! и у 82 % кадров своё динамическое дерево Хаффмана (79 920 разных деревьев на 111 тыс. динамических кадров: кэш деревьев не помогает, топ-4096 покрывает 20 %).
//! Цена разжатия — не декодирование, а построение таблиц: libdeflate на каждый кадр заполняет таблицу литералов/длин на 2^11 слов по 4 Б и смещений на 2^8 слов
//! (`build_decode_table` — 19 % циклов процесса BingX, perf 16.11), хотя коды в кадрах короткие (максимум 7–8 бит у литералов/длин, ≤ 3 у смещений). Здесь таблица
//! одноуровневая размером 2^(самая длинная длина кода) слов по 2 Б: 256 слов вместо 2048 — и всё состояние в нескольких сотнях байт, не в ≈ 10 КБ.
//! Контракт безопасности: результат отдаётся только после проверки CRC32 и длины хвоста gzip; любая необычность (длинный код, неполный код, лишние байты, большой кадр,
//! заголовок с CRC) — `None`, и `Gunzip` разжимает кадр прежним путём (libdeflate), поэтому поведение на плохих и редких кадрах не меняется.

// Приведения `as` ниже — к значениям, уже обрезанным маской или таблицей (< 2^16), или к индексам в массивах до 320 элементов.
#![allow(clippy::cast_possible_truncation)]

/// Самая длинная длина кода, для которой строится одноуровневая таблица (литералы/длины и смещения).
const MAX_BITS: u32 = 10;
/// Кадр длиннее — прежний путь (быстрый путь рассчитан на кадры в сотни байт).
pub const MAX_BODY: usize = 16 << 10;
pub const MAX_OUT: usize = 64 << 10;

const LEN_BASE: [u16; 29] = [
    3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131,
    163, 195, 227, 258,
];
const LEN_EXTRA: [u8; 29] = [
    0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0,
];
const DIST_BASE: [u16; 30] = [
    1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537,
    2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577,
];
const DIST_EXTRA: [u8; 30] = [
    0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13,
    13,
];
/// Порядок длин кодов в заголовке динамического блока (RFC 1951, 3.2.7).
const PRECODE_ORDER: [usize; 19] = [
    16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15,
];

/// Состояние декодера: таблицы одного кадра и таблицы фиксированного кода (строятся один раз). Одно на поток.
pub struct Tiny {
    lit: [u16; 1 << MAX_BITS],
    dist: [u16; 1 << MAX_BITS],
    pre: [u16; 1 << 7],
    fixed_lit: [u16; 1 << 9],
    fixed_dist: [u16; 1 << 5],
    /// Используемые символы и счёт длин кодов: литералы/длины, смещения, коды длин (собираются при разборе заголовка).
    lu: Used,
    du: Used,
    pu: Used,
}

/// Используемые символы одного алфавита в порядке номеров (слово = символ << 4 | длина кода) и счёт кодов по длинам: таблице не нужен обход всех 286 длин, из которых у кадра BingX
/// ненулевых ≈ 47 (замер 17.11).
struct Used {
    n: usize,
    w: [u16; 288],
    count: [u16; 16],
}

impl Used {
    fn new() -> Self {
        Self {
            n: 0,
            w: [0; 288],
            count: [0; 16],
        }
    }

    fn clear(&mut self) {
        self.n = 0;
        self.count = [0; 16];
    }

    /// Символ `sym` с кодом длины `len` (1..=15).
    #[inline(always)]
    fn add(&mut self, sym: usize, len: u8) -> Option<()> {
        *self.w.get_mut(self.n)? = (sym << 4) as u16 | u16::from(len);
        self.n += 1;
        *self.count.get_mut(usize::from(len & 15))? += 1;
        Some(())
    }

    /// Все длины алфавита подряд (коды длин, фиксированные коды).
    fn set_lens(&mut self, lens: &[u8]) -> Option<()> {
        self.clear();
        for (sym, &l) in lens.iter().enumerate() {
            if l != 0 {
                self.add(sym, l)?;
            }
        }
        Some(())
    }
}

/// Таблица декодирования одного кода.
#[derive(Clone, Copy)]
struct Table<'a> {
    t: &'a [u16],
    mask: u64,
}

/// Одноуровневая таблица: слово = символ << 4 | длина кода (0 — нет такого кода); индекс — следующие `bits` бит потока (первый бит кода — младший).
/// Возвращает `bits` — длину самого длинного кода (0 — кодов нет). `None`: код переполнен, неполон или длиннее `MAX_BITS` — кадр уйдёт прежним путём.
fn build(u: &Used, table: &mut [u16]) -> Option<u32> {
    let count = &u.count;
    let max = (1..16u32)
        .rev()
        .find(|&l| count.get(l as usize) != Some(&0));
    let Some(max) = max else {
        *table.get_mut(0)? = 0;
        return Some(0);
    };
    if max > MAX_BITS {
        return None;
    }
    let mut left = 1i32;
    let mut next = [0u32; 17];
    let mut code = 0u32;
    for l in 1..=max as usize {
        left = (left << 1) - i32::from(*count.get(l)?);
        if left < 0 {
            return None;
        }
        // `count[0]` не ведётся: нулевых длин в `Used` нет
        code = (code
            + if l == 1 {
                0
            } else {
                u32::from(*count.get(l - 1)?)
            })
            << 1;
        *next.get_mut(l)? = code;
    }
    let size = 1usize << max;
    let t = table.get_mut(..size)?;
    if left != 0 {
        // неполный код допустим только как единственный код длины 1 (одно смещение у кадров с одним видом повтора): второй слот таблицы — «нет кода»
        if u.n != 1 || max != 1 {
            return None;
        }
        t.fill(0);
    }
    for &e in u.w.get(..u.n)? {
        let l = e & 15;
        let c = next.get_mut(usize::from(l))?;
        let rev = (c.reverse_bits() >> (32 - u32::from(l))) as usize;
        *c += 1;
        for slot in t.iter_mut().skip(rev).step_by(1usize << l) {
            *slot = e;
        }
    }
    Some(max)
}

/// Битовый поток: 64-битный накопитель, младшие биты — первые. За концом данных читаются нули; `consumed` позволяет проверить, что вышли ровно на конец тела.
struct Bits<'a> {
    src: &'a [u8],
    pos: usize,
    buf: u64,
    n: u32,
}

impl<'a> Bits<'a> {
    fn new(src: &'a [u8]) -> Self {
        Self {
            src,
            pos: 0,
            buf: 0,
            n: 0,
        }
    }

    #[inline(always)]
    fn refill(&mut self) {
        if let Some(w) = self.src.get(self.pos..self.pos + 8) {
            let mut b = [0u8; 8];
            b.copy_from_slice(w);
            self.buf |= u64::from_le_bytes(b) << self.n;
            let take = (63 - self.n) >> 3;
            self.pos += take as usize;
            self.n += take << 3;
        } else {
            while self.n <= 56 {
                self.buf |= u64::from(self.src.get(self.pos).copied().unwrap_or(0)) << self.n;
                self.pos += 1;
                self.n += 8;
            }
        }
    }

    #[inline(always)]
    fn take(&mut self, bits: u32) -> u64 {
        let v = self.buf & ((1u64 << bits) - 1);
        self.buf >>= bits;
        self.n -= bits;
        v
    }

    /// Прочитанных бит (с учётом нулей за концом).
    fn consumed(&self) -> usize {
        self.pos * 8 - self.n as usize
    }
}

impl Tiny {
    pub fn new() -> Box<Self> {
        let mut t = Box::new(Self {
            lit: [0; 1 << MAX_BITS],
            dist: [0; 1 << MAX_BITS],
            pre: [0; 1 << 7],
            fixed_lit: [0; 1 << 9],
            fixed_dist: [0; 1 << 5],
            lu: Used::new(),
            du: Used::new(),
            pu: Used::new(),
        });
        let mut l = [8u8; 288];
        l.iter_mut().take(256).skip(144).for_each(|x| *x = 9);
        l.iter_mut().take(280).skip(256).for_each(|x| *x = 7);
        let mut u = Used::new();
        let ok = u.set_lens(&l).is_some()
            && build(&u, &mut t.fixed_lit).is_some()
            && u.set_lens(&[5u8; 32]).is_some()
            && build(&u, &mut t.fixed_dist).is_some();
        debug_assert!(ok);
        t
    }

    /// gzip-кадр → `out[..size]` (размер из хвоста кадра). `Some(size)` — разжато, CRC32 и длина сошлись; `None` — кадр не для быстрого пути или повреждён.
    pub fn unpack(&mut self, frame: &[u8], out: &mut Vec<u8>) -> Option<usize> {
        let (body, crc, size) = gzip_parts(frame)?;
        if body.len() > MAX_BODY || size > MAX_OUT {
            return None;
        }
        // буфер только растёт: обнулять каждый раз не нужно, декодер пишет в `out[..size]` целиком (это проверяется по `o == size`)
        if out.len() < size {
            out.resize(size, 0);
        }
        let dst = out.get_mut(..size)?;
        self.inflate(body, dst)?;
        (crc32fast::hash(dst) == crc).then_some(size)
    }

    fn inflate(&mut self, body: &[u8], out: &mut [u8]) -> Option<()> {
        let mut br = Bits::new(body);
        let mut o = 0usize;
        loop {
            br.refill();
            let last = br.take(1) == 1;
            match br.take(2) {
                0 => o = stored(&mut br, out, o)?,
                1 => {
                    let lit = Table {
                        t: &self.fixed_lit,
                        mask: (1 << 9) - 1,
                    };
                    let dist = Table {
                        t: &self.fixed_dist,
                        mask: (1 << 5) - 1,
                    };
                    o = block(&mut br, out, o, lit, dist)?;
                }
                2 => {
                    let (lb, db) = self.dynamic_header(&mut br)?;
                    let lit = Table {
                        t: &self.lit,
                        mask: (1u64 << lb) - 1,
                    };
                    let dist = Table {
                        t: &self.dist,
                        mask: (1u64 << db) - 1,
                    };
                    o = block(&mut br, out, o, lit, dist)?;
                }
                _ => return None,
            }
            if last {
                break;
            }
        }
        // тело кончилось ровно там, где кончился поток deflate (лишние байты — прежний путь), и выход заполнен целиком
        (o == out.len() && br.consumed().div_ceil(8) == body.len()).then_some(())
    }

    /// Заголовок динамического блока (RFC 1951, 3.2.7) → длины самых длинных кодов литералов/длин и смещений; таблицы лежат в `self.lit` / `self.dist`.
    fn dynamic_header(&mut self, br: &mut Bits<'_>) -> Option<(u32, u32)> {
        br.refill();
        let hlit = br.take(5) as usize + 257;
        let hdist = br.take(5) as usize + 1;
        let hclen = br.take(4) as usize + 4;
        if hlit > 286 || hdist > 30 {
            return None;
        }
        let mut pl = [0u8; 19];
        for &idx in PRECODE_ORDER.iter().take(hclen) {
            if br.n < 8 {
                br.refill();
            }
            *pl.get_mut(idx)? = br.take(3) as u8;
        }
        self.pu.set_lens(&pl)?;
        let pb = build(&self.pu, &mut self.pre)?;
        if pb == 0 {
            return None;
        }
        let pmask = (1u64 << pb) - 1;
        let total = hlit + hdist;
        let (mut i, mut prev, mut eob) = (0usize, None::<u8>, false);
        self.lu.clear();
        self.du.clear();
        while i < total {
            if br.n < 24 {
                br.refill();
            }
            let e = *self.pre.get((br.buf & pmask) as usize)?;
            let l = u32::from(e & 15);
            if l == 0 {
                return None;
            }
            br.take(l);
            let sym = e >> 4;
            let (val, rep) = match sym {
                0..=15 => (sym as u8, 1usize),
                16 => (prev?, 3 + br.take(2) as usize),
                17 => (0, 3 + br.take(3) as usize),
                _ => (0, 11 + br.take(7) as usize),
            };
            if i + rep > total {
                return None;
            }
            if val != 0 {
                for idx in i..i + rep {
                    if idx < hlit {
                        self.lu.add(idx, val)?;
                        eob |= idx == 256;
                    } else {
                        self.du.add(idx - hlit, val)?;
                    }
                }
            }
            prev = Some(val);
            i += rep;
        }
        // конец блока обязан иметь код
        if !eob {
            return None;
        }
        let lb = build(&self.lu, &mut self.lit)?;
        let db = build(&self.du, &mut self.dist)?;
        (lb != 0).then_some((lb, db))
    }
}

/// Несжатый блок: выровнять по байту, `LEN`/`NLEN`, копия.
fn stored(br: &mut Bits<'_>, out: &mut [u8], o: usize) -> Option<usize> {
    let drop = br.n & 7;
    br.take(drop);
    br.refill();
    let len = br.take(16) as usize;
    let nlen = br.take(16) as usize;
    if len != (!nlen & 0xffff) {
        return None;
    }
    // накопитель держит впереди ≤ 8 целых байт тела: вернуть их потоку и копировать из исходного среза
    let start = br.consumed() / 8;
    let src = br.src.get(start..start + len)?;
    out.get_mut(o..o + len)?.copy_from_slice(src);
    br.pos = start + len;
    br.buf = 0;
    br.n = 0;
    Some(o + len)
}

/// Символы блока до конца блока; `out[o..]` — куда писать, `out[..o]` — окно для повторов.
fn block(
    br: &mut Bits<'_>,
    out: &mut [u8],
    mut o: usize,
    lit: Table<'_>,
    dist: Table<'_>,
) -> Option<usize> {
    loop {
        // на символ с повтором уходит ≤ 38 бит (код 10 + длина 5 + смещение 10 + 13): добор — когда в накопителе меньше
        if br.n < 40 {
            br.refill();
        }
        let e = *lit.t.get((br.buf & lit.mask) as usize)?;
        let l = u32::from(e & 15);
        if l == 0 {
            return None;
        }
        br.buf >>= l;
        br.n -= l;
        let sym = usize::from(e >> 4);
        if sym < 256 {
            *out.get_mut(o)? = sym as u8;
            o += 1;
            continue;
        }
        if sym == 256 {
            return Some(o);
        }
        let li = sym - 257;
        let len =
            usize::from(*LEN_BASE.get(li)?) + br.take(u32::from(*LEN_EXTRA.get(li)?)) as usize;
        let d = *dist.t.get((br.buf & dist.mask) as usize)?;
        let dl = u32::from(d & 15);
        if dl == 0 {
            return None;
        }
        br.buf >>= dl;
        br.n -= dl;
        let di = usize::from(d >> 4);
        let distance =
            usize::from(*DIST_BASE.get(di)?) + br.take(u32::from(*DIST_EXTRA.get(di)?)) as usize;
        if distance > o || o + len > out.len() {
            return None;
        }
        if distance >= 8 && o + len + 8 <= out.len() {
            // не перекрываются в пределах 8 байт: кусками по 8 (запись за `o + len` до 7 байт затрётся следующими символами — место в `out` проверено)
            let (mut s, mut d) = (o - distance, o);
            let end = o + len;
            while d < end {
                out.copy_within(s..s + 8, d);
                s += 8;
                d += 8;
            }
        } else {
            // повтор может перекрывать собственный вывод (distance < len): байт за байтом
            for k in 0..len {
                let b = *out.get(o + k - distance)?;
                *out.get_mut(o + k)? = b;
            }
        }
        o += len;
    }
}

/// Тело deflate, CRC32 и длина из gzip-кадра (RFC 1952). `None` — заголовок необычный (CRC заголовка, зарезервированные флаги, не deflate) или кадр короткий.
pub fn gzip_parts(frame: &[u8]) -> Option<(&[u8], u32, usize)> {
    if frame.get(..3)? != [0x1f, 0x8b, 8] {
        return None;
    }
    let flg = *frame.get(3)?;
    if flg & 0xe2 != 0 {
        return None;
    }
    let mut pos = 10usize;
    if flg & 4 != 0 {
        let xlen = usize::from(u16::from_le_bytes(
            frame.get(pos..pos + 2)?.try_into().ok()?,
        ));
        pos += 2 + xlen;
    }
    for bit in [8u8, 16] {
        if flg & bit != 0 {
            pos += frame.get(pos..)?.iter().position(|&b| b == 0)? + 1;
        }
    }
    let end = frame.len().checked_sub(8)?;
    let crc = u32::from_le_bytes(frame.get(end..end + 4)?.try_into().ok()?);
    let size = u32::from_le_bytes(frame.get(end + 4..)?.try_into().ok()?) as usize;
    Some((frame.get(pos..end)?, crc, size))
}
