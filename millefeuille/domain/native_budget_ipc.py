"""Strict historical descriptor parser, ordinary callers reject supplied budgets."""
import os
from millefeuille.domain import standalone_writeback as n

def split_flags(flags):
    flags = list(flags); fds = None
    if "--native-budget-v2" in flags:
        i = flags.index("--native-budget-v2")
        n.require(len(flags) >= i + 4)
        values = flags[i+1:i+4]
        n.require(all(type(v) is str and v.isascii() and v.isdecimal() and len(v) <= 6 for v in values))
        fds = tuple(int(v) for v in values)
        n.require(len(set(fds)) == 3 and all(3 <= v <= 999999 for v in fds))
        import stat
        n.require(all(stat.S_ISFIFO(os.fstat(fd).st_mode) for fd in fds[:2]))
        import fcntl
        n.require(stat.S_ISFIFO(os.fstat(fds[2]).st_mode))
        mode = fcntl.fcntl(fds[2], fcntl.F_GETFL)
        n.require(mode & os.O_ACCMODE == os.O_RDONLY and mode & os.O_NONBLOCK)
        del flags[i:i+4]
    n.require("--native-budget-v2" not in flags)
    return flags, fds
