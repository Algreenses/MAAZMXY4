"""File installation with a durable undo journal and explicit startup acceptance.

All entry points that mutate an installation acquire the same OS lock. The
caller must first release this installation's application processes. The
worker runs outside the destination Python runtime so it can replace it.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from .packages import (CLIENT_RECORD, RESOURCE_ITEMS, Inventory, PackageError, digest_file,
                       ensure_no_links, managed_name)
from .protocol import Installed, Version
from .storage import atomic_bytes, atomic_copy, installation_lock, read_json, state_directory, write_json


class RecoveryRequired(PackageError):
    pass


def fingerprint(path):
    path = ensure_no_links(path)
    if not path.exists():
        return None
    if not path.is_file():
        raise PackageError('目标不是常规文件：' + str(path))
    return dict(size=path.stat().st_size, sha256=digest_file(path))


def installed_inventory(root):
    raw = read_json(Path(root) / CLIENT_RECORD)
    return Inventory.parse(raw, 'client') if raw is not None else None


def installed_versions(root, *, allow_damaged=False):
    inventory = installed_inventory(root)
    if inventory is None:
        return None
    try:
        interface = read_json(Path(root) / 'interface.json')
    except (ValueError, OSError):
        if not allow_damaged:
            raise
        interface = None
    if not isinstance(interface, dict) or interface.get('version') != inventory.resource_version:
        if allow_damaged:
            # A full repair may recover a missing/corrupt interface, but a
            # valid newer resource version must still never be downgraded.
            resource = Version(inventory.resource_version)
            if isinstance(interface, dict):
                try:
                    resource = max(resource, Version(interface.get('version')))
                except ValueError:
                    pass
            return Installed(Version(inventory.client_version), resource)
        raise RecoveryRequired('资源版本与安装记录不一致，请使用完整包修复。')
    return Installed(Version(inventory.client_version), Version(inventory.resource_version))


def _migration_path(name):
    # This narrow exception is for the trusted built-in migration planner only;
    # a package cannot declare these protected user files in its inventory.
    return (name == 'recordings/.bindings.json' or
            re.fullmatch(r'config/instances/[^/\\:]+\.json', name) is not None)


def _target(root, name):
    if name != CLIENT_RECORD and not _migration_path(name):
        managed_name(name, 'client')
    if any(part in ('', '.', '..') or part.endswith((' ', '.')) for part in name.split('/')):
        raise PackageError('恢复日志中的路径无效。')
    return ensure_no_links(root / name)


def _active(root):
    base = state_directory(root)
    pointer = read_json(base / 'current.json')
    if pointer is None:
        return None
    token = pointer.get('id') if isinstance(pointer, dict) else None
    if not isinstance(token, str) or re.fullmatch('[0-9a-f]{32}', token) is None:
        raise RecoveryRequired('更新恢复记录无效，请保留 temp/project-update 供检查。')
    directory = ensure_no_links(base / 'transactions' / token)
    state = read_json(directory / 'state.json')
    if not isinstance(state, dict) or state.get('id') != token:
        raise RecoveryRequired('更新事务记录缺失或损坏。')
    return directory, state


def current_status(root):
    active = _active(Path(root))
    return active[1] if active else {'state': 'idle'}


def _mark(directory, state, status, **extra):
    state.update(state=status, **extra)
    write_json(directory / 'state.json', state)


def _backup(path, destination, expected):
    if expected is None:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, destination)
    with destination.open('r+b') as stream:
        os.fsync(stream.fileno())
    if fingerprint(destination) != expected or fingerprint(path) != expected:
        raise PackageError('备份期间文件发生变化：' + str(path))


def _append_started(directory, index):
    with (directory / 'started.jsonl').open('ab') as stream:
        stream.write((str(index) + '\n').encode('ascii'))
        stream.flush()
        os.fsync(stream.fileno())


def _started(directory, count):
    path = ensure_no_links(directory / 'started.jsonl')
    if not path.exists():
        return []
    if path.stat().st_size > count * 12 + 12:
        raise RecoveryRequired('更新操作日志过大。')
    raw = path.read_bytes()
    # A torn last append precedes the file operation and is safe to ignore.
    lines = raw.split(b'\n')[:-1]
    try:
        indices = [int(line) for line in lines]
    except ValueError as error:
        raise RecoveryRequired('更新操作日志损坏。') from error
    if indices != list(range(len(indices))) or len(indices) > count:
        raise RecoveryRequired('更新操作日志顺序无效。')
    return indices


def _rollback(root, directory, state):
    plan = read_json(directory / 'plan.json')
    if not isinstance(plan, list):
        raise RecoveryRequired('缺少更新恢复计划。')
    failures = []
    for index in reversed(_started(directory, len(plan))):
        try:
            item = plan[index]
            path = _target(root, item['path'])
            current = fingerprint(path)
            if current == item['before']:
                continue  # interrupted before replace, or already rolled back
            if current != item['after']:
                raise RecoveryRequired('更新后被其他程序修改，保留现场：' + item['path'])
            if item['before'] is None:
                path.unlink()
            else:
                backup = ensure_no_links(directory / 'backup' / str(index))
                if fingerprint(backup) != item['before']:
                    raise RecoveryRequired('更新备份校验失败：' + item['path'])
                atomic_copy(backup, path)
        except (OSError, ValueError, KeyError, TypeError) as error:
            failures.append(str(error))
    if failures:
        _mark(directory, state, 'recovery_required', errors=failures)
        raise RecoveryRequired('自动回滚未完成，备份与日志已保留。' + '；'.join(failures))
    _mark(directory, state, 'rolled_back')
    return state


def recover(root):
    """Restore interrupted work before launching a client. Never guess success."""
    root = ensure_no_links(root)
    with installation_lock(root):
        active = _active(root)
        if not active:
            return {'state': 'idle'}
        directory, state = active
        if state['state'] in ('applying', 'pending_health', 'recovery_required'):
            return _rollback(root, directory, state)
        return state


def confirm_startup(root, token):
    """Called by the newly started frontend after its initialization succeeds."""
    root = ensure_no_links(root)
    with installation_lock(root):
        active = _active(root)
        if not active:
            raise RecoveryRequired('没有待确认的更新。')
        directory, state = active
        if state['id'] != token or state['state'] != 'pending_health':
            raise RecoveryRequired('启动确认与更新事务不匹配。')
        inventory = installed_inventory(root)
        if inventory is None:
            raise RecoveryRequired('启动确认缺少安装记录。')
        for name in ('MFAAvalonia.exe', 'MFAAvalonia.dll', 'updater.py', 'agent/main.py', 'interface.json'):
            expected = inventory.files.get(name)
            if expected is None or fingerprint(root / name) != expected:
                raise RecoveryRequired('启动确认文件不完整：' + name)
        _mark(directory, state, 'complete')
        return state


def install(root, stage, inventory, *, migration_plans=None, allow_modified=False, fault=None):
    """Install an already validated stage, then await explicit frontend health.

    migration_plans are generated by prepare_task_assets(), never deserialized
    from a downloaded package. fault is an offline fault-injection callback.
    """
    root, stage = ensure_no_links(root), ensure_no_links(stage)
    if root == stage or root.is_relative_to(stage):
        raise PackageError('暂存目录不能是安装目录或其上级。')
    inventory = Inventory.parse(inventory.to_dict(), inventory.kind)
    with installation_lock(root):
        active = _active(root)
        if active and active[1]['state'] not in ('complete', 'rolled_back'):
            raise RecoveryRequired('存在未完成的更新，请先恢复。')
        old = installed_inventory(root)
        if old is None:
            raise PackageError('旧客户端缺少安装记录，请首次手动安装完整客户端。')
        if Version(inventory.resource_version) < Version(old.resource_version):
            raise PackageError('更新包会降级资源，已拒绝。')
        if inventory.kind == 'client' and Version(inventory.client_version) < Version(old.client_version):
            raise PackageError('更新包会降级客户端，已拒绝。')
        if inventory.kind == 'resource' and (Version(old.client_version) < Version(inventory.min_client_version)
                or inventory.max_client_version and Version(old.client_version) > Version(inventory.max_client_version)):
            raise PackageError('资源包与当前客户端不兼容。')
        before_files = dict(old.files)
        desired = dict(inventory.files)
        if inventory.kind == 'resource':
            # Preserve the historical whole-three-item semantics, backing up
            # local changes and old extra files before replacing them.
            for folder in ('resource', 'agent'):
                base = ensure_no_links(root / folder)
                if base.exists():
                    for path in base.rglob('*'):
                        ensure_no_links(path)
                        if path.is_file():
                            before_files.setdefault(path.relative_to(root).as_posix(), fingerprint(path))
            before_files = {n: v for n, v in before_files.items() if n.split('/')[0] in RESOURCE_ITEMS}
            final_files = {n: v for n, v in old.files.items() if n.split('/')[0] not in RESOURCE_ITEMS}
            final_files.update(desired)
            final_inventory = Inventory('client', old.client_version, inventory.resource_version, final_files)
        else:
            final_inventory = inventory
        changes, modified = [], []
        for name in sorted(set(before_files) | set(desired)):
            managed_name(name, inventory.kind)
            path = _target(root, name)
            before, after = fingerprint(path), desired.get(name)
            if before is not None and name not in before_files:
                raise PackageError('未知文件与更新文件冲突，未覆盖：' + name)
            if before is not None and before != old.files.get(name):
                modified.append(name)
            if after is not None and fingerprint(stage / name) != after:
                raise PackageError('暂存文件校验失败：' + name)
            if before != after:
                changes.append((name, before, after, None))
        if modified and not allow_modified:
            raise PackageError('受管理文件被修改；需确认备份后覆盖：' + '、'.join(modified[:10]))
        for name, (raw, prior) in (migration_plans or {}).items():
            if not _migration_path(name):
                continue  # canonical resource files are already handled above
            before = fingerprint(_target(root, name))
            expected = None if prior is None else dict(size=len(prior), sha256=hashlib.sha256(prior).hexdigest())
            if before != expected:
                raise PackageError('迁移计划生成后用户配置发生变化：' + name)
            after = dict(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            if before != after:
                changes.append((name, before, after, raw))
        record = json.dumps(final_inventory.to_dict(), ensure_ascii=False, indent=2).encode('utf8')
        changes.append((CLIENT_RECORD, fingerprint(root / CLIENT_RECORD),
                        dict(size=len(record), sha256=hashlib.sha256(record).hexdigest()), record))
        needed = sum((before or {}).get('size', 0) + (after or {}).get('size', 0)
                     for _, before, after, _ in changes) + 32 * 1024**2
        if shutil.disk_usage(root).free < needed:
            raise PackageError('磁盘空间不足以暂存、备份和完成更新。')
        token = uuid.uuid4().hex
        directory = state_directory(root) / 'transactions' / token
        directory.mkdir(parents=True, exist_ok=False)
        plan = []
        for index, (name, before, after, raw) in enumerate(changes):
            _backup(_target(root, name), directory / 'backup' / str(index), before)
            if after is not None:
                incoming = directory / 'incoming' / str(index)
                incoming.parent.mkdir(exist_ok=True)
                if raw is not None:
                    atomic_bytes(incoming, raw)
                else:
                    _backup(stage / name, incoming, after)
            plan.append(dict(path=name, before=before, after=after))
        write_json(directory / 'plan.json', plan)
        state = dict(id=token, state='applying', kind=inventory.kind,
                     client_version=final_inventory.client_version, resource_version=final_inventory.resource_version,
                     modified_files=modified)
        write_json(directory / 'state.json', state)
        write_json(state_directory(root) / 'current.json', {'id': token})
        try:
            for index, item in enumerate(plan):
                path = _target(root, item['path'])
                if fingerprint(path) != item['before']:
                    raise PackageError('安装期间文件被其他程序修改：' + item['path'])
                _append_started(directory, index)
                if fault:
                    fault('before_replace', index)
                if item['after'] is None:
                    path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(directory / 'incoming' / str(index), path)
                if fault:
                    fault('after_replace', index)
            if inventory.kind == 'client':
                (root / 'runtimes/win-x64/native/plugins').mkdir(parents=True, exist_ok=True)
            _mark(directory, state, 'pending_health')
            return state
        except Exception:
            _rollback(root, directory, state)
            raise
