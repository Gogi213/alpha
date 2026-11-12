//! Сканер плоского JSON без аллокаций: объект → поля (ключ, значение) срезами кадра; вложенное — сырым срезом.
//!
//! Зачем свой: кадры бирж строго одной формы, а ядру нужно «разобрано точно или записано как пришло» — набор ключей и
//! значения проверяются сверху (адаптер), сканер лишь режет текст. Строки с экранированием (`\`) не разбираются — вернут
//! `None`, кадр уйдёт сырым (у бирж в разбираемых полях экранирования нет). Любой вход — без паники и без аллокаций.

pub const MAX_FIELDS: usize = 16;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Val<'a> {
    /// Строка без кавычек.
    Str(&'a [u8]),
    /// Число как написано.
    Num(&'a [u8]),
    Bool(bool),
    Null,
    /// Вложенный массив или объект целиком, со скобками.
    Raw(&'a [u8]),
}

impl<'a> Val<'a> {
    pub fn str(self) -> Option<&'a [u8]> {
        match self {
            Val::Str(s) => Some(s),
            _ => None,
        }
    }

    /// Целое со знаком; число с точкой/экспонентой — `None`.
    pub fn int(self) -> Option<i64> {
        match self {
            Val::Num(s) => parse_i64(s),
            _ => None,
        }
    }

    pub fn boolean(self) -> Option<bool> {
        match self {
            Val::Bool(b) => Some(b),
            _ => None,
        }
    }

    pub fn raw(self) -> Option<&'a [u8]> {
        match self {
            Val::Raw(s) => Some(s),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub struct Field<'a> {
    pub key: &'a [u8],
    pub val: Val<'a>,
}

const EMPTY: Field<'static> = Field { key: &[], val: Val::Null };

/// Поля одного объекта в порядке кадра.
#[derive(Clone, Copy, Debug)]
pub struct Obj<'a> {
    fields: [Field<'a>; MAX_FIELDS],
    len: usize,
}

impl<'a> Obj<'a> {
    pub fn len(&self) -> usize {
        self.len
    }

    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    pub fn iter(&self) -> impl Iterator<Item = &Field<'a>> {
        self.fields.iter().take(self.len)
    }

    pub fn get(&self, key: &[u8]) -> Option<Val<'a>> {
        self.iter().find(|f| f.key == key).map(|f| f.val)
    }
}

/// Целое: `-?[0-9]{1,18}`.
pub fn parse_i64(s: &[u8]) -> Option<i64> {
    let (neg, digits) = match s.split_first() {
        Some((b'-', rest)) => (true, rest),
        _ => (false, s),
    };
    if digits.is_empty() || digits.len() > 18 {
        return None;
    }
    let mut v: i64 = 0;
    for &c in digits {
        if !c.is_ascii_digit() {
            return None;
        }
        v = v * 10 + i64::from(c - b'0');
    }
    Some(if neg { -v } else { v })
}

struct Cur<'a> {
    s: &'a [u8],
    i: usize,
}

impl<'a> Cur<'a> {
    fn peek(&self) -> Option<u8> {
        self.s.get(self.i).copied()
    }

    fn ws(&mut self) {
        while matches!(self.peek(), Some(b' ' | b'\n' | b'\r' | b'\t')) {
            self.i += 1;
        }
    }

    fn eat(&mut self, c: u8) -> bool {
        if self.peek() == Some(c) {
            self.i += 1;
            true
        } else {
            false
        }
    }

    /// После открывающей кавычки: срез до закрывающей; обратная косая черта — `None`.
    fn string(&mut self) -> Option<&'a [u8]> {
        let start = self.i;
        let rest = self.s.get(start..)?;
        let n = rest.iter().position(|&c| c == b'"' || c == b'\\')?;
        if rest.get(n) != Some(&b'"') {
            return None;
        }
        self.i = start + n + 1;
        rest.get(..n)
    }

    /// От `[` или `{`: срез до парной скобки (строки внутри могут быть с экранированием).
    fn nested(&mut self) -> Option<&'a [u8]> {
        let start = self.i;
        let mut depth = 0usize;
        let mut in_str = false;
        let mut esc = false;
        while let Some(c) = self.peek() {
            self.i += 1;
            if in_str {
                if esc {
                    esc = false;
                } else if c == b'\\' {
                    esc = true;
                } else if c == b'"' {
                    in_str = false;
                }
                continue;
            }
            match c {
                b'"' => in_str = true,
                b'[' | b'{' => depth += 1,
                b']' | b'}' => {
                    depth = depth.checked_sub(1)?;
                    if depth == 0 {
                        return self.s.get(start..self.i);
                    }
                }
                _ => {}
            }
        }
        None
    }

    fn value(&mut self) -> Option<Val<'a>> {
        match self.peek()? {
            b'"' => {
                self.i += 1;
                self.string().map(Val::Str)
            }
            b'[' | b'{' => self.nested().map(Val::Raw),
            b't' | b'f' | b'n' => {
                let rest = self.s.get(self.i..)?;
                let (lit, v) = if rest.starts_with(b"true") {
                    (4, Val::Bool(true))
                } else if rest.starts_with(b"false") {
                    (5, Val::Bool(false))
                } else if rest.starts_with(b"null") {
                    (4, Val::Null)
                } else {
                    return None;
                };
                self.i += lit;
                Some(v)
            }
            _ => {
                let start = self.i;
                while matches!(self.peek(), Some(b'0'..=b'9' | b'-' | b'+' | b'.' | b'e' | b'E')) {
                    self.i += 1;
                }
                if self.i == start {
                    None
                } else {
                    self.s.get(start..self.i).map(Val::Num)
                }
            }
        }
    }
}

/// Объект целиком: весь срез — один объект (хвост из пробелов допустим), не более `MAX_FIELDS` полей.
pub fn parse_object(s: &[u8]) -> Option<Obj<'_>> {
    let mut c = Cur { s, i: 0 };
    c.ws();
    if !c.eat(b'{') {
        return None;
    }
    let mut obj = Obj { fields: [EMPTY; MAX_FIELDS], len: 0 };
    c.ws();
    if !c.eat(b'}') {
        loop {
            c.ws();
            if !c.eat(b'"') {
                return None;
            }
            let key = c.string()?;
            c.ws();
            if !c.eat(b':') {
                return None;
            }
            c.ws();
            let val = c.value()?;
            let slot = obj.fields.get_mut(obj.len)?;
            *slot = Field { key, val };
            obj.len += 1;
            c.ws();
            if c.eat(b',') {
                continue;
            }
            if c.eat(b'}') {
                break;
            }
            return None;
        }
    }
    c.ws();
    if c.i == s.len() {
        Some(obj)
    } else {
        None
    }
}

/// Элементы массива верхнего уровня: `f` получает срез элемента; `false` от `f` или нарушенная форма — `None`.
/// Возвращает число элементов.
pub fn each_elem<'a, F: FnMut(&'a [u8]) -> bool>(arr: &'a [u8], mut f: F) -> Option<usize> {
    let mut c = Cur { s: arr, i: 0 };
    c.ws();
    if !c.eat(b'[') {
        return None;
    }
    let mut n = 0usize;
    c.ws();
    if c.eat(b']') {
        c.ws();
        return (c.i == arr.len()).then_some(0);
    }
    loop {
        c.ws();
        let start = c.i;
        match c.peek()? {
            b'"' => {
                c.i += 1;
                c.string()?;
            }
            b'[' | b'{' => {
                c.nested()?;
            }
            _ => c.value().map(|_| ())?,
        }
        if !f(arr.get(start..c.i)?) {
            return None;
        }
        n += 1;
        c.ws();
        if c.eat(b',') {
            continue;
        }
        if c.eat(b']') {
            break;
        }
        return None;
    }
    c.ws();
    (c.i == arr.len()).then_some(n)
}

/// Ровно одна пара строк `[["цена","размер"]]` (книга Bybit L1).
pub fn single_pair(arr: &[u8]) -> Option<(&[u8], &[u8])> {
    let mut pair: Option<&[u8]> = None;
    let n = each_elem(arr, |e| {
        pair = Some(e);
        true
    })?;
    if n != 1 {
        return None;
    }
    let mut strs: [&[u8]; 2] = [&[], &[]];
    let mut k = 0usize;
    let m = each_elem(pair?, |e| {
        let inner = e.strip_prefix(b"\"").and_then(|x| x.strip_suffix(b"\""));
        match (inner, strs.get_mut(k)) {
            (Some(s), Some(slot)) if !s.contains(&b'\\') && !s.contains(&b'"') => {
                *slot = s;
                k += 1;
                true
            }
            _ => false,
        }
    })?;
    let [price, size] = strs;
    (m == 2).then_some((price, size))
}
