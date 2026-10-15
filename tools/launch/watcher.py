"""TK-244, В-9: события файловой системы через inotify (stdlib ctypes к libc, только Linux) — сервер формы пересчитывает кэш по событию, не по
таймеру. Следит за конкретными каталогами формы (снимок Летописи, запросы, прогоны на глубину depth, каталог заданий alsched) — не за
/data целиком, обходов нет: подкаталог прогона, появившийся позже, берётся по событию IN_CREATE. Нет inotify (Windows, нет libc) —
Watcher() бросает OSError, сервер падает на дешёвый stat-опрос раз в секунду."""
import ctypes
import ctypes.util
import os
import select
import struct

IN_CLOSE_WRITE, IN_MOVED_TO, IN_CREATE, IN_DELETE, IN_ISDIR = 0x8, 0x80, 0x100, 0x200, 0x40000000
MASK = IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE | IN_DELETE


class Watcher:
    def __init__(self, dirs):
        """dirs: [(каталог, глубина)] — глубина 0: только он сам; 1: и подкаталоги; и т. д."""
        try:
            libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
            self._add = libc.inotify_add_watch
            self.fd = libc.inotify_init1(os.O_NONBLOCK)
        except (OSError, AttributeError) as e:
            raise OSError(f"inotify недоступен: {e}")
        if self.fd < 0:
            raise OSError("inotify_init1 отказал")
        self.wd = {}
        for d, depth in dirs:
            self._tree(d, depth)
        if not self.wd:
            raise OSError("ни одного каталога под наблюдением")

    def _watch(self, path, depth):
        wd = self._add(self.fd, os.fsencode(path), MASK)
        if wd >= 0:
            self.wd[wd] = (path, depth)
        return wd >= 0

    def _tree(self, path, depth):
        if not self._watch(path, depth) or depth <= 0:
            return
        try:
            subs = [e.path for e in os.scandir(path) if e.is_dir(follow_symlinks=False)]
        except OSError:
            return
        for p in subs:
            self._tree(p, depth - 1)

    def wait(self, timeout):
        """Блокируется до события или timeout с; True — были события (очередь прочитана целиком)."""
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return False
        try:
            buf = os.read(self.fd, 1 << 16)
        except BlockingIOError:
            return False
        i = 0
        while i + 16 <= len(buf):
            wd, mask, _, ln = struct.unpack_from("iIII", buf, i)
            name = buf[i + 16:i + 16 + ln].split(b"\0", 1)[0]
            i += 16 + ln
            ent = self.wd.get(wd)
            if ent and mask & IN_CREATE and mask & IN_ISDIR and ent[1] > 0:
                self._tree(os.path.join(ent[0], os.fsdecode(name)), ent[1] - 1)
        return True
