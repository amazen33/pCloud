#!/usr/bin/env python3
"""Bounded synthetic POSIX probe; never reads or removes business data."""
import argparse
import hashlib
import json
import mmap
import os
from pathlib import Path
import re
import stat
import sys


def check(condition):
    if not condition: raise ValueError('Probe condition rejected')


def expected(run, component): return ('pcloud-backend:' + run + ':' + component).encode()


def execute(action, root, run, component, uid, min_bytes, min_inodes, reserve):
    check(os.name == 'posix' and os.getuid() == uid)
    check(re.fullmatch('[0-9a-f]{12}', run) and component in ('loki', 'tempo', 'mimir'))
    root = Path(root).absolute(); directory = root / ('pcloud-probe-' + run + '-' + component)
    check(not root.is_symlink() and root.is_dir())
    target = directory / 'marker'
    if action == 'deny':
        try: target.read_bytes()
        except PermissionError: return {'status': 'PASS', 'checks': ['other-uid-read-denied']}
        raise ValueError('Other UID could read marker')
    # Resolve canonical root and reject symlink ancestors before any write/cleanup.
    check(root.resolve() == root and all(not p.is_symlink() for p in [root, *root.parents]))
    data = expected(run, component)
    if action == 'write':
        import fcntl
        space = os.statvfs(root)
        check(space.f_bavail * space.f_frsize >= max(min_bytes, space.f_blocks * space.f_frsize * reserve)
              and space.f_favail >= max(min_inodes, space.f_files * reserve))
        directory.mkdir(mode=0o700)
        lock = directory / 'lock'
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            second = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
            try:
                try: fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError: pass
                else: raise ValueError('Exclusive lock not enforced')
            finally: os.close(second)
        finally: os.close(fd)
        stage = directory / 'staged'
        fd = os.open(stage, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            check(os.write(fd, data) == len(data)); os.fsync(fd)
            with mmap.mmap(fd, len(data)) as mapped:
                check(mapped[:] == data); mapped.flush()
            os.fsync(fd)
        finally: os.close(fd)
        os.replace(stage, target)
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(dir_fd)
        finally: os.close(dir_fd)
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(root_fd)
        finally: os.close(root_fd)
    check(directory.is_dir() and not directory.is_symlink())
    info = directory.stat()
    check(info.st_uid == uid and stat.S_IMODE(info.st_mode) == 0o700)
    check({p.name for p in directory.iterdir()} == {'marker', 'lock'})
    for p in directory.iterdir():
        info = p.lstat()
        check(stat.S_ISREG(info.st_mode) and info.st_uid == uid and stat.S_IMODE(info.st_mode) == 0o600)
    check(target.read_bytes() == data and (directory / 'lock').stat().st_size == 0)
    if action == 'cleanup':
        # Fixed files only, verified above. No recursive delete and never remove the volume root.
        target.unlink(); (directory / 'lock').unlink(); directory.rmdir()
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(root_fd)
        finally: os.close(root_fd)
    checks = ['fsync', 'mmap', 'exclusive-flock', 'atomic-rename', 'byte-and-inode-reserve'] if action == 'write' else ['persistent-remount-read'] if action == 'read' else ['owned-synthetic-files-removed']
    return {'status': 'PASS', 'checks': checks, 'marker_sha256': hashlib.sha256(data).hexdigest()}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['write', 'read', 'deny', 'cleanup'])
    p.add_argument('--root', required=True); p.add_argument('--run', required=True)
    p.add_argument('--component', required=True); p.add_argument('--uid', type=int, required=True)
    p.add_argument('--min-bytes', type=int, default=2147483648); p.add_argument('--min-inodes', type=int, default=10000)
    p.add_argument('--reserve', type=float, default=0.2)
    a = p.parse_args()
    try:
        report = execute(a.action, a.root, a.run, a.component, a.uid, a.min_bytes, a.min_inodes, a.reserve)
        print(json.dumps(report)); return 0
    except Exception:
        print('{"status":"FAIL","reason":"POSIX-probe-rejected; raw diagnostics suppressed"}'); return 1


if __name__ == '__main__': sys.exit(main())
