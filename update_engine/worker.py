"""Prepare and run an isolated installer, independent of replaced Python DLLs."""
import os
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

from .packages import (Inventory, PackageError, ensure_no_links, extract_verified)
from .processes import wait_for_exit
from .protocol import Manifest, choose_update
from .service import REASONS, source_configuration, _canonical
from .storage import atomic_bytes, installation_lock, read_json, state_directory, write_json
from .transaction import (RecoveryRequired, current_status, fingerprint, install,
                          installed_inventory, installed_versions, recover)

WORKER_POINTER = 'worker-ready.txt'


def _token(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{32}', value) is None:
        raise PackageError('待安装记录标识无效。')
    return value


def pending_package(root):
    base = state_directory(root)
    pending = read_json(base / 'pending.json')
    if not isinstance(pending, dict):
        raise PackageError('没有已校验的待安装包，请先下载。')
    directory = ensure_no_links(base / 'downloads' / _token(pending.get('id')))
    receipt = read_json(directory / 'receipt.json')
    if not isinstance(receipt, dict) or receipt.get('id') != directory.name:
        raise PackageError('待安装包记录损坏，请重新下载。')
    config = source_configuration(root, receipt['config']['channel'])
    if config != receipt['config']:
        raise PackageError('更新源已改变，请重新下载。')
    manifest = Manifest.parse(receipt['manifest'], config['channel'])
    ledger = read_json(base / 'accepted-manifests.json') or {}
    accepted = ledger.get(config['identity'], {})
    if (accepted.get('sequence') != manifest.sequence or
            accepted.get('sha256') != hashlib.sha256(_canonical(receipt['manifest'])).hexdigest()):
        raise PackageError('待安装包对应的清单已过期或被替换，请重新检查并下载。')
    decision = choose_update(installed_versions(root, allow_damaged=receipt.get('repair', False)), manifest, repair=receipt.get('repair', False))
    if decision.status != 'available' or receipt['kind'] != decision.release.package.kind:
        raise PackageError(REASONS[decision.reason])
    return directory, receipt, decision.release


def prepare_worker(root, *, allow_modified=False):
    """Copy trusted current code/runtime; no installed program files are changed."""
    root = ensure_no_links(root)
    with installation_lock(root):
        if (state_directory(root) / WORKER_POINTER).exists():
            raise RecoveryRequired('独立安装助手已经就绪；请关闭助手后从标准启动器恢复，勿重复发起安装。')
        if current_status(root)['state'] not in ('idle', 'complete', 'rolled_back'):
            raise RecoveryRequired('请先恢复未完成的更新。')
        download, _, _ = pending_package(root)
        old = installed_inventory(root)
        if old is None:
            raise PackageError('首次升级需要手动安装完整客户端。')
        token = uuid.uuid4().hex
        worker = state_directory(root) / 'workers' / token
        worker.mkdir(parents=True, exist_ok=False)
        # Do not execute new migration code from the package. The installed,
        # inventoried planner prepares config changes before replacing assets.
        for name, expected in old.files.items():
            if not name.startswith(('runtimes/python/', 'update_engine/', 'agent/')):
                continue
            source = ensure_no_links(root / name)
            if fingerprint(source) != expected:
                raise PackageError('安装助手依赖已修改或缺失，需要手动完整修复：' + name)
            target = worker / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if fingerprint(target) != expected:
                raise PackageError('安装助手复制校验失败：' + name)
        for name in ('runtimes/python/pythonw.exe', 'update_engine/worker.py', 'agent/task_asset_sync.py'):
            if not (worker / name).is_file():
                raise PackageError('安装助手缺少运行依赖：' + name)
        write_json(worker / 'job.json', dict(root=str(root), id=token, download=download.name,
                                            allow_modified=bool(allow_modified)))
        atomic_bytes(state_directory(root) / WORKER_POINTER, token.encode('ascii'))
        return worker


def start_worker(root, *, allow_modified=False):
    worker = prepare_worker(root, allow_modified=allow_modified)
    process = subprocess.Popen([str(worker / 'runtimes/python/pythonw.exe'), '-E', '-s', '-u',
                                '-X', 'utf8', '-m', 'update_engine', 'worker'], cwd=worker,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               close_fds=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return dict(state='awaiting_exit', pid=process.pid,
                message='更新助手已就绪，等待此目录的客户端退出。不会结束游戏或其他目录的助手。')


def _log(root, message):
    from datetime import datetime
    with (state_directory(root) / 'worker.log').open('a', encoding='utf8') as stream:
        stream.write(datetime.now().isoformat(timespec='seconds') + ' ' + message + '\n')


def _launch(root, token=None):
    environment = os.environ.copy()
    environment.pop('MAAZMXY4_UPDATE_TOKEN', None)
    if token:
        environment['MAAZMXY4_UPDATE_TOKEN'] = token
    return subprocess.Popen([str(root / 'MFAAvalonia.exe')], cwd=root, env=environment, close_fds=True)


def run_worker(worker, *, recovery=False):
    worker = ensure_no_links(worker)
    job = read_json(worker / 'job.json')
    if not isinstance(job, dict) or _token(job.get('id')) != worker.name:
        raise PackageError('安装助手记录无效。')
    root = ensure_no_links(job['root'])
    if worker != state_directory(root) / 'workers' / job['id']:
        raise PackageError('安装助手目录与目标不一致。')
    pointer = state_directory(root) / WORKER_POINTER
    if pointer.read_text(encoding='ascii') != worker.name:
        raise PackageError('安装助手已被替代，未执行安装。')
    # Separate worker lock prevents a launcher and an already-running worker
    # from applying/recovering the same transaction concurrently.
    with installation_lock(worker):
        wait_for_exit(root, worker)
        state = current_status(root)
        if recovery or state['state'] not in ('idle', 'complete', 'rolled_back'):
            restored = recover(root)
            _log(root, '启动恢复结果：' + restored['state'])
            pointer.unlink(missing_ok=True)
            _launch(root)
            return restored
        download, receipt, release = pending_package(root)
        if download.name != job['download']:
            raise PackageError('待安装包已改变，请重新发起安装。')
        stage = worker / ('verified-' + uuid.uuid4().hex)
        inventory = extract_verified(download / 'package.zip', stage, release)
        from .validation import validate_stage
        validate_stage(stage, inventory, source=receipt['config'])
        previous = installed_inventory(root)
        if (receipt.get('repair') and previous is not None
                and previous.files.get('interface.json') == inventory.files.get('interface.json')):
            # Identical task definitions need no migration. Keep configurations
            # byte-for-byte even when the installed interface/assets are broken.
            plans = {}
        else:
            sys.path.insert(0, str(worker / 'agent'))
            from task_asset_sync import prepare_task_assets, _verify_inputs
            _, plans, source_inputs, target_inputs = prepare_task_assets(stage, root, update_version=True)
            _verify_inputs({**source_inputs, **target_inputs})
        try:
            state = install(root, stage, inventory, migration_plans=plans,
                            allow_modified=job.get('allow_modified', False))
            _log(root, '文件安装完成，等待客户端启动确认：' + state['id'])
            client = _launch(root, state['id'])
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if current_status(root)['state'] == 'complete':
                    pointer.unlink(missing_ok=True)
                    (state_directory(root) / 'pending.json').unlink(missing_ok=True)
                    _log(root, '客户端已确认启动，更新完成。')
                    return current_status(root)
                if client.poll() is not None:
                    break
                time.sleep(0.5)
            # Do not replace files beneath a live, unresponsive frontend.
            if client.poll() is None:
                raise RecoveryRequired('新版客户端尚未确认启动。请手动关闭后从标准启动器恢复；备份已保留。')
            wait_for_exit(root, worker, timeout=20)
            recover(root)
            pointer.unlink(missing_ok=True)
            _log(root, '新版启动失败，已恢复旧版本。')
            _launch(root)
            return current_status(root)
        except Exception as error:
            _log(root, '更新未完成：' + str(error))
            if current_status(root)['state'] in ('complete', 'rolled_back', 'idle'):
                pointer.unlink(missing_ok=True)
            raise
