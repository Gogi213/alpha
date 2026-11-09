//! TK-516: чтение суток Bybit ob200 (zip, jsonl) — без аллокаций на сообщение: сырой deflate из zip через flate2 (zlib-rs), разбор по схеме
//! вручную (`fastjson`), любое отклонение строки от схемы — прежний путь serde_json. Результат разбора тот же, что у serde_json + `str::parse`.
use crate::num::{parse_f64, parse_f64_b};
use serde::Deserialize;
use std::io::{BufRead, BufReader};

#[derive(Deserialize)]
struct BybitData<'a> {
    #[serde(borrow)]
    b: Vec<(&'a str, &'a str)>,
    #[serde(borrow)]
    a: Vec<(&'a str, &'a str)>,
    u: Option<i64>,
}

#[derive(Deserialize)]
struct BybitMsg<'a> {
    ts: i64,
    #[serde(rename = "type")]
    ty: &'a str,
    #[serde(borrow)]
    data: BybitData<'a>,
}

/// Сообщение Bybit в разобранном виде: уровни уже числами (буферы переиспользуются между сообщениями).
#[derive(Default)]
pub struct Msg {
    pub ts: i64,
    pub snap: bool,
    pub u: Option<i64>,
    pub b: Vec<(f64, f64)>,
    pub a: Vec<(f64, f64)>,
}

/// Разбор по схеме `{"topic":..,"type":..,"ts":N,"data":{"s":..,"b":[[".",".."],..],"a":[..],"u":N,..},..}`.
/// Экранирование, не-ASCII, вложенное значение неизвестного ключа, нецелое ts/u, иная форма уровня, повтор ключа — `None` (строку разбирает serde_json).
pub mod fastjson {
    use super::{parse_f64_b, Msg};

    struct Cur<'a> {
        s: &'a [u8],
        i: usize,
    }

    impl<'a> Cur<'a> {
        fn eat(&mut self, c: u8) -> Option<()> {
            (*self.s.get(self.i)? == c).then(|| self.i += 1)
        }

        /// Строка без экранирования и не-ASCII: `"..."` -> байты содержимого (ASCII, т. е. заведомо UTF-8).
        fn str_b(&mut self) -> Option<&'a [u8]> {
            self.eat(b'"')?;
            let st = self.i;
            while let Some(&c) = self.s.get(self.i) {
                if c == b'"' {
                    let r = &self.s[st..self.i];
                    self.i += 1;
                    return Some(r);
                }
                if c == b'\\' || c >= 0x80 {
                    return None;
                }
                self.i += 1;
            }
            None
        }

        fn str(&mut self) -> Option<&'a str> {
            std::str::from_utf8(self.str_b()?).ok()
        }

        /// Целое со знаком, не длиннее 18 цифр; следом обязателен `,` или `}` (иначе число не целое — на serde).
        fn int(&mut self) -> Option<i64> {
            let neg = self.s.get(self.i) == Some(&b'-');
            self.i += neg as usize;
            let (st, mut v) = (self.i, 0i64);
            while let Some(&c) = self.s.get(self.i) {
                let d = c.wrapping_sub(b'0');
                if d >= 10 {
                    break;
                }
                if self.i - st == 18 {
                    return None;
                }
                v = v * 10 + d as i64;
                self.i += 1;
            }
            if self.i == st || !matches!(self.s.get(self.i), Some(b',' | b'}')) {
                return None;
            }
            Some(if neg { -v } else { v })
        }

        /// Скалярное значение неизвестного ключа: строка или число/литерал до `,`/`}`.
        fn skip_scalar(&mut self) -> Option<()> {
            if self.s.get(self.i) == Some(&b'"') {
                return self.str().map(|_| ());
            }
            let st = self.i;
            while let Some(&c) = self.s.get(self.i) {
                match c {
                    b',' | b'}' => break,
                    b'{' | b'[' | b'"' | b'\\' | b']' => return None,
                    _ => self.i += 1,
                }
            }
            (self.i > st).then_some(())
        }

        fn levels(&mut self, out: &mut Vec<(f64, f64)>) -> Option<()> {
            out.clear();
            self.eat(b'[')?;
            if self.s.get(self.i) == Some(&b']') {
                self.i += 1;
                return Some(());
            }
            loop {
                self.eat(b'[')?;
                let p = self.str_b()?;
                self.eat(b',')?;
                let q = self.str_b()?;
                self.eat(b']')?;
                out.push((parse_f64_b(p), parse_f64_b(q)));
                match self.s.get(self.i)? {
                    b',' => self.i += 1,
                    b']' => {
                        self.i += 1;
                        return Some(());
                    }
                    _ => return None,
                }
            }
        }

        /// Объект: для каждого ключа `f` разбирает значение; `None` — ключ/значение вне схемы.
        fn object(&mut self, mut f: impl FnMut(&mut Self, &'a str) -> Option<()>) -> Option<()> {
            self.eat(b'{')?;
            if self.s.get(self.i) == Some(&b'}') {
                self.i += 1;
                return Some(());
            }
            loop {
                let k = self.str()?;
                self.eat(b':')?;
                f(self, k)?;
                match self.s.get(self.i)? {
                    b',' => self.i += 1,
                    b'}' => {
                        self.i += 1;
                        return Some(());
                    }
                    _ => return None,
                }
            }
        }
    }

    pub fn parse(line: &[u8], m: &mut Msg) -> Option<()> {
        let mut c = Cur { s: line, i: 0 };
        let (mut ts, mut ty, mut seen_data) = (None, None, false);
        m.u = None;
        m.b.clear();
        m.a.clear();
        c.object(|c, k| match k {
            "ts" if ts.is_none() => {
                ts = Some(c.int()?);
                Some(())
            }
            "type" if ty.is_none() => {
                ty = Some(c.str()?);
                Some(())
            }
            "data" if !seen_data => {
                seen_data = true;
                let (mut hb, mut ha) = (false, false);
                c.object(|c, k| match k {
                    "b" if !hb => {
                        hb = true;
                        c.levels(&mut m.b)
                    }
                    "a" if !ha => {
                        ha = true;
                        c.levels(&mut m.a)
                    }
                    "u" if m.u.is_none() => {
                        m.u = Some(c.int()?);
                        Some(())
                    }
                    "b" | "a" | "u" => None,
                    _ => c.skip_scalar(),
                })?;
                (hb && ha).then_some(())
            }
            "ts" | "type" | "data" => None,
            _ => c.skip_scalar(),
        })?;
        if c.i != line.len() {
            return None;
        }
        m.ts = ts?;
        m.snap = ty? == "snapshot";
        Some(())
    }
}

/// Запасной разбор строки serde_json (прежний путь) в тот же `Msg`.
fn serde_msg(line: &[u8], m: &mut Msg) {
    let j: BybitMsg = serde_json::from_slice(line).unwrap();
    m.ts = j.ts;
    m.snap = j.ty == "snapshot";
    m.u = j.data.u;
    m.b.clear();
    m.a.clear();
    m.b.extend(j.data.b.iter().map(|(p, q)| (parse_f64(p), parse_f64(q))));
    m.a.extend(j.data.a.iter().map(|(p, q)| (parse_f64(p), parse_f64(q))));
}

/// Разобрать строку сообщения: быстрый путь, при отклонении от схемы — serde_json.
pub fn parse_line(line: &[u8], m: &mut Msg) {
    if fastjson::parse(line, m).is_none() {
        serde_msg(line, m);
    }
}

/// Поток распакованных данных суток Bybit: запись 0 zip; deflate — сырой поток через flate2, иначе путь zip.
fn reader(zip_path: &str) -> Box<dyn BufRead> {
    let mut za = zip::ZipArchive::new(std::fs::File::open(zip_path).unwrap()).unwrap();
    let raw = {
        let e = za.by_index_raw(0).unwrap();
        (e.compression() == zip::CompressionMethod::Deflated).then(|| (e.data_start(), e.compressed_size()))
    };
    match raw {
        Some((start, size)) => {
            let mut f = std::fs::File::open(zip_path).unwrap();
            std::io::Seek::seek(&mut f, std::io::SeekFrom::Start(start)).unwrap();
            let raw = BufReader::with_capacity(1 << 18, std::io::Read::take(f, size));
            Box::new(BufReader::with_capacity(1 << 20, flate2::bufread::DeflateDecoder::new(raw)))
        }
        None => {
            // не deflate (в данных поставщика не встречается): запись целиком в память
            let mut v = Vec::new();
            std::io::Read::read_to_end(&mut za.by_index(0).unwrap(), &mut v).unwrap();
            Box::new(std::io::Cursor::new(v))
        }
    }
}

/// Сутки Bybit: для каждого сообщения внутри суток — `f(час, сообщение)`; возвращает число сообщений вне суток.
pub fn scan(zip_path: &str, mut f: impl FnMut(usize, &Msg)) -> usize {
    let mut rd = reader(zip_path);
    let (mut line, mut day0, mut dropped, mut m) = (Vec::new(), None::<i64>, 0usize, Msg::default());
    while {
        line.clear();
        rd.read_until(b'\n', &mut line).unwrap() > 0
    } {
        let t = line.trim_ascii();
        if t.is_empty() {
            continue;
        }
        parse_line(t, &mut m);
        let d0 = *day0.get_or_insert(m.ts - m.ts.rem_euclid(86_400_000));
        let h = (m.ts - d0).div_euclid(3_600_000);
        if !(0..24).contains(&h) {
            dropped += 1;
            continue;
        }
        f(h as usize, &m);
    }
    dropped
}

/// Строки книги одного сообщения Bybit: сначала бид (сторона 2), потом аск; снимок fu=-1 и бит0, дельта fu=u.
pub fn rows(m: &Msg, mut emit: impl FnMut(i64, i64, f64, f64, u8)) {
    let fu = if m.snap { -1 } else { m.u.unwrap() };
    for (side, lv) in [(2u8, &m.b), (0u8, &m.a)] {
        for &(p, q) in lv {
            emit(m.ts, fu, p, q, m.snap as u8 | side);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn both(line: &str) -> (Option<Msg>, Msg) {
        let mut f = Msg::default();
        let fast = fastjson::parse(line.as_bytes(), &mut f).map(|_| f);
        let mut s = Msg::default();
        serde_msg(line.as_bytes(), &mut s);
        (fast, s)
    }

    fn eq(a: &Msg, b: &Msg) -> bool {
        let bits = |v: &Vec<(f64, f64)>| v.iter().map(|&(p, q)| (p.to_bits(), q.to_bits())).collect::<Vec<_>>();
        a.ts == b.ts && a.snap == b.snap && a.u == b.u && bits(&a.b) == bits(&b.b) && bits(&a.a) == bits(&b.a)
    }

    #[test]
    fn typical_lines_fast_equals_serde() {
        for l in [
            r#"{"topic":"orderbook.200.DOGEUSDT","type":"snapshot","ts":1789603201680,"data":{"s":"DOGEUSDT","b":[["0.08086","86427"],["0.08085","425962.5"]],"a":[["0.08088","1"]],"u":1,"seq":34995990538},"cts":1789603201661}"#,
            r#"{"topic":"x","type":"delta","ts":1789603201780,"data":{"s":"D","b":[["0.08086","18677"]],"a":[],"u":41953061,"seq":3},"cts":1}"#,
            r#"{"type":"delta","ts":5,"data":{"a":[],"b":[],"u":7}}"#,
            r#"{"data":{"b":[["1e-5","2"]],"a":[["-0","0.000"]],"u":0},"ts":-3,"type":"other"}"#,
            r#"{"topic":"t","type":"delta","ts":1,"data":{"s":"D","b":[],"a":[]}}"#,
        ] {
            let (f, s) = both(l);
            assert!(eq(&f.unwrap_or_else(|| panic!("fast None: {l}")), &s), "{l}");
        }
    }

    #[test]
    fn off_schema_lines_return_none() {
        for l in [
            r#"{"type":"delta","ts":1.5,"data":{"b":[],"a":[],"u":1}}"#,
            r#"{"type":"delta","ts":1,"data":{"b":[["1","2","3"]],"a":[],"u":1}}"#,
            r#"{"type":"delta","ts":1,"data":{"b":[[1,2]],"a":[],"u":1}}"#,
            r#"{"type":"delta","ts":1,"data":{"b":[],"a":[],"u":1,"x":[1]}}"#,
            r#"{"type":"delta","ts":1,"ts":2,"data":{"b":[],"a":[],"u":1}}"#,
            r#"{"type":"delta","ts":1,"data":{"b":[],"u":1}}"#,
            r#"{"type":"delta","ts":1,"data":{"b":[],"a":[],"u":1}} x"#,
            r#"{"type":"delta","data":{"b":[],"a":[],"u":1}}"#,
            r#"{"type":"delta","ts":1,"data":{"b":[],"a":[],"u":1"#,
            "",
            "{",
        ] {
            assert!(fastjson::parse(l.as_bytes(), &mut Msg::default()).is_none(), "{l}");
        }
    }
}
