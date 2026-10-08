p = 'src/commands/lob/bounce_grid.rs'
raw = open(p, 'rb').read().decode('utf-8')
assert '\r\n' in raw
s = raw.replace('\r\n', '\n')
blk = open('zz_block.tmp', encoding='utf-8').read()
lines = s.split('\n')
assert lines[1043] == '<<<<<<< ours' and lines[1288] == '>>>>>>> theirs', (lines[1043], lines[1288])
new = lines[:1043] + blk.rstrip('\n').split('\n') + lines[1289:]
s = '\n'.join(new)
old = """fn bind_params<'a, F: Fn(&FilterSet, &'a [usize]) -> DayParams<'a>>(f: F) -> F {
    f
}"""
assert old in s
s = s.replace(old, """fn bind_params<'a, F>(f: F) -> F
where
    F: Fn(&FilterSet, &'a [usize], &'a std::sync::OnceLock<Vec<u32>>) -> DayParams<'a>,
{
    f
}""")
open(p, 'wb').write(s.replace('\n', '\r\n').encode('utf-8'))
