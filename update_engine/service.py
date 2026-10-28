"""The shared update check and download service for both desktop entry points."""
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

from . import network
from .packages import PackageError, extract_verified, read_json_bytes
from .protocol import CHANNELS, PRODUCT, Manifest, choose_update, https_url
from .storage import installation_lock, read_json, state_directory, write_json
from .transaction import current_status, installed_inventory, installed_versions

REASONS = {
    'bootstrap_required': '旧客户端需要首次安装新版完整客户端，才能启用统一更新。',
    'manifest_stale': '下载源中的清单比此前已接受的发布更旧，请稍后重试。',
    'updater_too_old': '更新协议已升级，请下载完整客户端手动安装。',
    'client_would_downgrade_resources': '完整客户端内的资源比本地旧，等待发布者提供匹配整包。',
    'compatible_full_package_required': '需要兼容的完整客户端修复包。',
    'client_upgrade_required': '此资源需要其他客户端版本，请先升级完整客户端。',
    'client_update': '发现完整客户端更新，需要退出助手后安装。',
    'resource_update': '发现资源更新，仅更新资源、Agent 和任务定义。',
    'no_new_version': '当前渠道没有更高的兼容版本。',
}


def source_configuration(root, channel=None):
    raw = read_json(Path(root) / 'update-config.json')
    if not isinstance(raw, dict) or raw.get('schema') != 1 or raw.get('product') != PRODUCT:
        raise PackageError('缺少有效的项目更新源配置，请使用完整客户端安装。')
    repository = raw.get('repository')
    if not isinstance(repository, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise PackageError('更新源仓库标识无效。')
    selected = channel or raw.get('default_channel', 'stable')
    if selected not in CHANNELS:
        raise PackageError('更新渠道无效。')
    urls = raw.get('channels', {}).get(selected)
    if not isinstance(urls, list) or not 1 <= len(urls) <= 8:
        raise PackageError('当前渠道没有配置更新源。')
    urls = [https_url(url) for url in urls]
    identity = repository + ':' + selected
    return dict(repository=repository, channel=selected, urls=urls, identity=identity)


def local_summary(root):
    inventory = installed_inventory(root)
    return dict(client_version=inventory.client_version if inventory else None,
                resource_version=inventory.resource_version if inventory else None,
                transaction=current_status(root))


def _canonical(raw):
    return json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf8')


def check(root, *, channel=None, repair=False, fetch=network.fetch_manifest):
    root = Path(root)
    config = source_configuration(root, channel)
    with installation_lock(root):
        ledger_path = state_directory(root) / 'accepted-manifests.json'
        ledger = read_json(ledger_path) or {}
        if not isinstance(ledger, dict):
            raise PackageError('本地更新来源记录无效。')
        accepted = ledger.get(config['identity'], {})
        if (not isinstance(accepted, dict) or type(accepted.get('sequence', 0)) is not int
                or accepted.get('sequence', 0) < 0):
            raise PackageError('本地更新发布序号无效。')
    candidates, failures, digests = [], [], {}
    # Compare healthy mirrors, not merely the first successful HTTP response.
    for url in config['urls']:
        try:
            raw = read_json_bytes(fetch(url))
            manifest = Manifest.parse(raw, config['channel'])
            digest = hashlib.sha256(_canonical(raw)).hexdigest()
        except (OSError, ValueError) as error:
            failures.append(dict(url=url, error=str(error)))
            continue
        previous = digests.setdefault(manifest.sequence, digest)
        if previous != digest or (manifest.sequence == accepted.get('sequence') and digest != accepted.get('sha256')):
            raise PackageError('同一发布序号存在不同内容，请发布者修正清单后重试。')
        candidates.append((manifest.sequence, manifest, raw, digest, url))
    if not candidates:
        raise PackageError('检查更新失败，未能读取任何有效清单。' + '；'.join(item['error'] for item in failures))
    _, manifest, raw, digest, url = max(candidates, key=lambda item: item[0])
    # Re-read under lock: another checker may have accepted a newer feed while
    # this request was in flight. Do not overwrite its monotonic state.
    with installation_lock(root):
        ledger = read_json(ledger_path) or {}
        accepted = ledger.get(config['identity'], {})
        if manifest.sequence == accepted.get('sequence') and digest != accepted.get('sha256'):
            raise PackageError('发布清单与已接受记录冲突。')
        decision = choose_update(installed_versions(root, allow_damaged=repair), manifest, repair=repair,
                                 minimum_sequence=accepted.get('sequence', 0),
                                 recovery_required=current_status(root)['state'] == 'recovery_required')
        if manifest.sequence >= accepted.get('sequence', 0):
            ledger[config['identity']] = dict(sequence=manifest.sequence, sha256=digest)
            write_json(ledger_path, ledger)
        result = dict(status=decision.status, reason=decision.reason, message=REASONS[decision.reason],
                      channel=config['channel'], repository=config['repository'], source=url,
                      checked_at=int(time.time()), warnings=failures, **local_summary(root))
        if decision.release is not None:
            release = decision.release
            result['update'] = dict(kind=release.package.kind, version=str(release.version),
                                    resource_version=str(release.resource_version or release.version),
                                    size=release.package.size, notes=release.notes)
        write_json(state_directory(root) / 'checked.json', dict(config=config, manifest=raw,
                   result=result, repair=repair))
        return result


def stage_update(root, *, progress=lambda *_: None, cancelled=lambda: False, download=network.download):
    root = Path(root)
    with installation_lock(root):
        cached = read_json(state_directory(root) / 'checked.json')
        if current_status(root)['state'] not in ('idle', 'complete', 'rolled_back'):
            raise PackageError('存在未完成的安装，请先从标准启动器恢复。')
        if not isinstance(cached, dict):
            raise PackageError('请先检查更新。')
        config = source_configuration(root, cached['config']['channel'])
        if config != cached['config']:
            raise PackageError('更新来源已改变，请重新检查。')
        manifest = Manifest.parse(cached['manifest'], config['channel'])
        ledger = read_json(state_directory(root) / 'accepted-manifests.json') or {}
        accepted = ledger.get(config['identity'], {})
        if (manifest.sequence != accepted.get('sequence') or
                hashlib.sha256(_canonical(cached['manifest'])).hexdigest() != accepted.get('sha256')):
            raise PackageError('待下载清单已失效，请重新检查。')
        decision = choose_update(installed_versions(root, allow_damaged=cached.get('repair', False)), manifest, repair=cached.get('repair', False),
                                 minimum_sequence=accepted.get('sequence', 0))
        if decision.status != 'available':
            raise PackageError(REASONS[decision.reason])
        directory = state_directory(root) / 'downloads' / uuid.uuid4().hex
        directory.mkdir(parents=True, exist_ok=False)
        package = directory / 'package.zip'
        download(decision.release.package, package, progress=progress, cancelled=cancelled)
        inventory = extract_verified(package, directory / 'stage', decision.release)
        receipt = dict(id=directory.name, manifest=cached['manifest'], config=config,
                       kind=inventory.kind, repair=cached.get('repair', False), state='verified')
        write_json(directory / 'receipt.json', receipt)
        write_json(state_directory(root) / 'pending.json', {'id': directory.name})
        return dict(state='verified', id=directory.name, kind=inventory.kind,
                    version=inventory.client_version or inventory.resource_version,
                    message='下载与校验完成；退出助手后才能安装。')
