"""Durable metadata and one writer per installation. No process termination."""
from contextlib import contextmanager
import json
import os
import shutil
from pathlib import Path
import uuid

from .packages import ensure_no_links, read_json_bytes, PackageError


def read_json(path):
    path = ensure_no_links(path)
    if not path.is_file():
        return None
    with path.open('rb') as stream:
        from .packages import MAX_METADATA
        return read_json_bytes(stream.read(MAX_METADATA + 1))


def atomic_bytes(path, raw):
    path = ensure_no_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new-' + uuid.uuid4().hex)
    try:
        with temporary.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path, value):
    atomic_bytes(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf8'))


def atomic_copy(source, path):
    source, path = ensure_no_links(source), ensure_no_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new-' + uuid.uuid4().hex)
    try:
        with source.open('rb') as reader, temporary.open('xb') as output:
            shutil.copyfileobj(reader, output, 128 * 1024)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def state_directory(root):
    root = ensure_no_links(root)
    if not root.is_dir():
        raise PackageError('安装目录不存在。')
    path = ensure_no_links(root / 'temp/project-update')
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def installation_lock(root):
    """OS releases this lock on death; the empty lock file is intentionally kept."""
    path = ensure_no_links(state_directory(root) / 'writer.lock')
    with path.open('a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'\0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise PackageError('此安装目录已有更新操作，请等待完成。') from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
