//! Формат записи на диске (В-133, `docs/collector-ws-format.md`): запись → строка и обратно без потерь.
//!
//! Делает: `Rec` (разобранная запись биржи, строки — срезы кадра), `Encoder` (состояние одного файла: приём предыдущей
//! записи), `Decoder` (обратное: проверка без потерь, читатель `collect check`). Не знает биржу, файлы и соединения.
//! Числа биржи (цена, размер, ставка) — строками как пришли (В-172); время и счётчики — разностями, точно.
//! Столбцы — в `docs/collector-ws-format.md`; формат тот же, что у Python-ядра `wscore/codec.py` (читатели не меняются).

use std::collections::HashMap;

pub const NS_PER_MS: i64 = 1_000_000;

/// Вид записи данных.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Book,
    Trade,
    Mark,
}

impl Kind {
    pub const ALL: [Kind; 3] = [Kind::Book, Kind::Trade, Kind::Mark];

    pub fn idx(self) -> usize {
        match self {
            Kind::Book => 0,
            Kind::Trade => 1,
            Kind::Mark => 2,
        }
    }

    pub fn code(self) -> u8 {
        match self {
            Kind::Book => b'b',
            Kind::Trade => b't',
            Kind::Mark => b'm',
        }
    }

    /// Имя вида потока в health (`fresh.kinds`).
    pub fn name(self) -> &'static str {
        match self {
            Kind::Book => "book",
            Kind::Trade => "trade",
            Kind::Mark => "mark",
        }
    }
}

/// Лучшие bid/ask: `u` — номер обновления биржи (Bybit: сквозной `seq` — номер `u` у входов Bybit свой, см. `bybit.rs`);
/// `id` — личность записи для снятия дублей (в файл не пишется).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Book<'a> {
    pub sym: &'a [u8],
    pub e: i64,
    pub t: i64,
    pub u: i64,
    pub id: i64,
    pub bid: &'a [u8],
    pub bsz: &'a [u8],
    pub ask: &'a [u8],
    pub asz: &'a [u8],
}

/// Сделка: `a` — ключ порядка, `f`/`l` — первый/последний id в агрегате, `m` — покупатель мейкер, `nq` — размер без RPI;
/// `id` — личность сделки для снятия дублей (Binance — `a`, Bybit — хеш UUID; в файл не пишется).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Trade<'a> {
    pub sym: &'a [u8],
    pub e: i64,
    pub t: i64,
    pub a: i64,
    pub id: i64,
    pub p: &'a [u8],
    pub q: &'a [u8],
    pub f: i64,
    pub l: i64,
    pub m: bool,
    pub nq: &'a [u8],
    /// Признаки сделки одним словом: пусто — обычная; Bybit: `R` — RPI (улучшение цены розничного ордера), `B` — блок-сделка, `BR` — оба.
    pub x: &'a [u8],
}

/// Mark/индекс/фандинг: `ap`, `pp` (P) — если есть у биржи, иначе пусто; `t` — время следующего начисления.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Mark<'a> {
    pub sym: &'a [u8],
    pub e: i64,
    pub p: &'a [u8],
    pub ap: &'a [u8],
    pub pp: &'a [u8],
    pub i: &'a [u8],
    pub r: &'a [u8],
    pub t: i64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Rec<'a> {
    Book(Book<'a>),
    Trade(Trade<'a>),
    Mark(Mark<'a>),
}

impl<'a> Rec<'a> {
    pub fn kind(&self) -> Kind {
        match self {
            Rec::Book(_) => Kind::Book,
            Rec::Trade(_) => Kind::Trade,
            Rec::Mark(_) => Kind::Mark,
        }
    }

    pub fn sym(&self) -> &'a [u8] {
        match self {
            Rec::Book(b) => b.sym,
            Rec::Trade(t) => t.sym,
            Rec::Mark(m) => m.sym,
        }
    }

    /// Время биржи E, мс (для лага и свежести).
    pub fn e(&self) -> i64 {
        match self {
            Rec::Book(b) => b.e,
            Rec::Trade(t) => t.e,
            Rec::Mark(m) => m.e,
        }
    }

    /// Личность записи по символу: две доставки одной записи (линии, перекрытие замены) имеют одну личность и пишутся один раз.
    /// Book — u (Bybit: `seq`), trade — a (Bybit: хеш UUID), mark — E. Порядок в файле — порядок прихода первой доставки.
    pub fn ident(&self) -> i64 {
        match self {
            Rec::Book(b) => b.id,
            Rec::Trade(t) => t.id,
            Rec::Mark(m) => m.e,
        }
    }
}

/// Состояние кодировщика по символу; новый файл — нули.
#[derive(Clone, Copy, Debug, Default)]
pub struct SymEnc {
    pub last_u: i64,
    pub last_a: i64,
    pub last_l: i64,
}

pub fn put_i64(out: &mut Vec<u8>, v: i64) {
    let mut buf = [0u8; 20];
    let mut n = v.unsigned_abs();
    let mut i = buf.len();
    loop {
        i -= 1;
        // остаток от деления на 10 — одна цифра
        if let Some(slot) = buf.get_mut(i) {
            *slot = b'0' + u8::try_from(n % 10).unwrap_or(0);
        }
        n /= 10;
        if n == 0 {
            break;
        }
    }
    if v < 0 {
        out.push(b'-');
    }
    out.extend_from_slice(buf.get(i..).unwrap_or(&[]));
}

fn tab(out: &mut Vec<u8>) {
    out.push(b'\t');
}

fn field(out: &mut Vec<u8>, s: &[u8]) {
    tab(out);
    out.extend_from_slice(s);
}

fn num(out: &mut Vec<u8>, v: i64) {
    tab(out);
    put_i64(out, v);
}

/// Запись → строка формата; состояние — на файл (приём предыдущей записи).
#[derive(Debug, Default)]
pub struct Encoder {
    prev_ns: i64,
}

impl Encoder {
    pub fn new() -> Self {
        Self::default()
    }

    /// Новый файл: приём предыдущей записи — ноль (первая запись — абсолютное время).
    pub fn reset(&mut self) {
        self.prev_ns = 0;
    }

    fn dns(&mut self, rx_ns: i64) -> i64 {
        let d = rx_ns - self.prev_ns;
        self.prev_ns = rx_ns;
        d
    }

    /// `rx_ns` не должен быть меньше приёма предыдущей записи файла (`dns` ≥ 0): за порядок отвечает писатель.
    pub fn record(&mut self, out: &mut Vec<u8>, rx_ns: i64, st: &mut SymEnc, rec: &Rec<'_>) {
        let rms = rx_ns.div_euclid(NS_PER_MS);
        match rec {
            Rec::Book(b) => {
                out.push(b'b');
                num(out, self.dns(rx_ns));
                num(out, rms - b.e);
                num(out, b.e - b.t);
                field(out, b.sym);
                num(out, b.u - st.last_u);
                st.last_u = b.u;
                field(out, b.bid);
                field(out, b.bsz);
                field(out, b.ask);
                field(out, b.asz);
            }
            Rec::Trade(t) => {
                out.push(b't');
                num(out, self.dns(rx_ns));
                num(out, rms - t.e);
                num(out, t.e - t.t);
                field(out, t.sym);
                num(out, t.a - st.last_a);
                field(out, t.p);
                field(out, t.q);
                num(out, t.l - t.f);
                num(out, t.f - st.last_l);
                st.last_a = t.a + 1;
                st.last_l = t.l + 1;
                num(out, i64::from(t.m));
                tab(out);
                if t.nq != t.q {
                    out.extend_from_slice(t.nq);
                }
                field(out, t.x);
            }
            Rec::Mark(m) => {
                out.push(b'm');
                num(out, self.dns(rx_ns));
                num(out, rms - m.e);
                field(out, m.sym);
                field(out, m.p);
                field(out, m.ap);
                field(out, m.pp);
                field(out, m.i);
                field(out, m.r);
                num(out, m.t);
            }
        }
        out.push(b'\n');
    }

    /// Кадр, не разобранный точно, — как пришёл. Перевод строки в кадре заменяется пробелом (строка формата кончается им;
    /// у бирж в JSON его нет, а между токенами он — пробельный символ).
    pub fn raw(&mut self, out: &mut Vec<u8>, rx_ns: i64, frame: &[u8]) {
        out.push(b'r');
        num(out, self.dns(rx_ns));
        tab(out);
        out.extend(frame.iter().map(|&c| if c == b'\n' { b' ' } else { c }));
        out.push(b'\n');
    }

    pub fn ping(&mut self, out: &mut Vec<u8>, rx_ns: i64, rtt_us: i64) {
        out.push(b'g');
        num(out, self.dns(rx_ns));
        num(out, rtt_us);
        out.push(b'\n');
    }

    pub fn control(&mut self, out: &mut Vec<u8>, rx_ns: i64, what: &str, detail: &str) {
        out.push(b'c');
        num(out, self.dns(rx_ns));
        field(out, what.as_bytes());
        field(out, detail.as_bytes());
        out.push(b'\n');
    }
}

/// Запись файла, восстановленная читателем.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Decoded {
    Book {
        rx_ns: i64,
        e: i64,
        t: i64,
        sym: String,
        u: i64,
        bid: String,
        bsz: String,
        ask: String,
        asz: String,
    },
    Trade {
        rx_ns: i64,
        e: i64,
        t: i64,
        sym: String,
        a: i64,
        p: String,
        q: String,
        f: i64,
        l: i64,
        m: bool,
        nq: String,
        x: String,
    },
    Mark {
        rx_ns: i64,
        e: i64,
        sym: String,
        p: String,
        ap: String,
        pp: String,
        i: String,
        r: String,
        t: i64,
    },
    Ping {
        rx_ns: i64,
        rtt_us: i64,
    },
    Control {
        rx_ns: i64,
        what: String,
        detail: String,
    },
    Raw {
        rx_ns: i64,
        frame: String,
    },
}

/// Обратное к `Encoder`: состояние (предыдущие u/a/l, приём) — на файл.
#[derive(Debug, Default)]
pub struct Decoder {
    prev_ns: i64,
    last_u: HashMap<String, i64>,
    last_a: HashMap<String, i64>,
    last_l: HashMap<String, i64>,
}

fn int(f: &[&str], i: usize) -> Result<i64, String> {
    f.get(i)
        .ok_or_else(|| format!("нет столбца {i}"))?
        .parse::<i64>()
        .map_err(|e| format!("столбец {i}: {e}"))
}

fn text(f: &[&str], i: usize) -> Result<String, String> {
    f.get(i)
        .map(|s| (*s).to_string())
        .ok_or_else(|| format!("нет столбца {i}"))
}

impl Decoder {
    pub fn new() -> Self {
        Self::default()
    }

    /// Строка файла (без перевода строки) → запись. Пустая строка — ошибка.
    pub fn line(&mut self, line: &str) -> Result<Decoded, String> {
        let kind = line.as_bytes().first().copied().ok_or("пустая строка")?;
        let f: Vec<&str> = if kind == b'r' {
            line.splitn(3, '\t').collect()
        } else {
            line.split('\t').collect()
        };
        self.prev_ns += int(&f, 1)?;
        let rx_ns = self.prev_ns;
        let rms = rx_ns.div_euclid(NS_PER_MS);
        match kind {
            b'b' => {
                let e = rms - int(&f, 2)?;
                let sym = text(&f, 4)?;
                let u = self.last_u.get(&sym).copied().unwrap_or(0) + int(&f, 5)?;
                self.last_u.insert(sym.clone(), u);
                Ok(Decoded::Book {
                    rx_ns,
                    e,
                    t: e - int(&f, 3)?,
                    sym,
                    u,
                    bid: text(&f, 6)?,
                    bsz: text(&f, 7)?,
                    ask: text(&f, 8)?,
                    asz: text(&f, 9)?,
                })
            }
            b't' => {
                let e = rms - int(&f, 2)?;
                let sym = text(&f, 4)?;
                let a = self.last_a.get(&sym).copied().unwrap_or(0) + int(&f, 5)?;
                let first = self.last_l.get(&sym).copied().unwrap_or(0) + int(&f, 9)?;
                let last = first + int(&f, 8)?;
                self.last_a.insert(sym.clone(), a + 1);
                self.last_l.insert(sym.clone(), last + 1);
                let q = text(&f, 7)?;
                let nq = f
                    .get(11)
                    .filter(|s| !s.is_empty())
                    .map_or_else(|| q.clone(), |s| (*s).to_string());
                Ok(Decoded::Trade {
                    rx_ns,
                    e,
                    t: e - int(&f, 3)?,
                    sym,
                    a,
                    p: text(&f, 6)?,
                    q,
                    f: first,
                    l: last,
                    m: f.get(10) == Some(&"1"),
                    nq,
                    x: f.get(12).map_or_else(String::new, |s| (*s).to_string()),
                })
            }
            b'm' => Ok(Decoded::Mark {
                rx_ns,
                e: rms - int(&f, 2)?,
                sym: text(&f, 3)?,
                p: text(&f, 4)?,
                ap: text(&f, 5)?,
                pp: text(&f, 6)?,
                i: text(&f, 7)?,
                r: text(&f, 8)?,
                t: int(&f, 9)?,
            }),
            b'g' => Ok(Decoded::Ping {
                rx_ns,
                rtt_us: int(&f, 2)?,
            }),
            b'c' => Ok(Decoded::Control {
                rx_ns,
                what: text(&f, 2)?,
                detail: f.get(3).map_or_else(String::new, |s| (*s).to_string()),
            }),
            b'r' => Ok(Decoded::Raw {
                rx_ns,
                frame: text(&f, 2)?,
            }),
            other => Err(format!("неизвестный тип записи {:?}", char::from(other))),
        }
    }
}
