"""Package inventory and bounded extraction shared by build and install paths.

This module does not execute package content or alter an existing installation.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import stat
import zipfile

from .protocol import ARCHITECTURE, PRODUCT, PROTOCOL, ProtocolError, Version, _integer, _object

CLIENT_RECORD = 'client-installation.json'
RESOURCE_RECORD = 'resource-package.json'
RESOURCE_ITEMS = ('resource', 'agent', 'interface.json')
PRESERVED = frozenset(('recordings', 'config', 'appsettings.json', 'backup', 'debug',
                       'logs', 'temp', '.local-migrations', '.git', 'agents.md'))
CLIENT_DIRS = frozenset(('resource', 'agent', 'libs', 'runtimes', 'update_engine', 'docs'))
CLIENT_FILES = frozenset(('interface.json', 'updater.py', 'MFAAvalonia.exe', 'MFAAvalonia.dll',
                          'MFAAvalonia.deps.json', 'MFAAvalonia.runtimeconfig.json', 'libloader.dll',
                          'MAA造梦西游4.exe', 'launcher-build.json', 'script-studio-build.json', 'update-config.json',
                          'DependencySetup_依赖库安装_win.bat', 'LICENSE', 'NOTICE', 'README.md'))
MAX_FILES = 50000
MAX_UNPACKED = 8 * 1024**3
MAX_METADATA = 16 * 1024**2
_DEVICE = re.compile(r'(?:CON|PRN|AUX|NUL|CLOCK\$|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?$', re.I)


class PackageError(ProtocolError):
    pass


def relative_name(value):
    if (not isinstance(value, str) or not value or len(value) > 240 or '\\' in value
            or ':' in value or any(ord(c) < 32 for c in value)):
        raise PackageError('更新包包含无效路径。')
    parts = value.split('/')
    for part in parts:
        if (not part or part in ('.', '..') or part.endswith((' ', '.'))
                or part.casefold() in ('.git', '.runtime-cache', '.local-migrations')
                or _DEVICE.fullmatch(part.split('.', 1)[0].rstrip(' '))
                or re.search(r'~[0-9]', part) or any(c in part for c in '<>"|?*')):
            raise PackageError('更新包包含不安全路径：' + value)
    if parts[0].casefold() in PRESERVED or any(p.casefold() == 'agents.md' for p in parts):
        raise PackageError('更新包不能覆盖用户数据或本地协作说明：' + value)
    return value


def managed_name(name, kind):
    name = relative_name(name)
    parts = name.split('/')
    if kind == 'resource':
        allowed = name == 'interface.json' or len(parts) > 1 and parts[0] in ('agent', 'resource')
    elif kind == 'client':
        allowed = (len(parts) > 1 and parts[0] in CLIENT_DIRS or name in CLIENT_FILES
                   or len(parts) == 1 and re.fullmatch(r'(?:LICENSE|NOTICE)-[A-Za-z0-9_.-]+\.txt', name))
    else:
        raise PackageError('未知更新包种类。')
    if not allowed:
        raise PackageError('更新包试图修改范围之外的文件：' + name)
    return name


def ensure_no_links(path):
    """Check every existing ancestor, including Windows directory junctions."""
    path = Path(path).absolute()
    for item in (path, *path.parents):
        if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
            raise PackageError('安装路径不允许符号链接或目录联接：' + str(item))
    return path


def digest_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PackageError('JSON 字段重复：' + key)
        result[key] = value
    return result


def read_json_bytes(raw):
    if len(raw) > MAX_METADATA:
        raise PackageError('更新元数据过大。')
    try:
        return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(PackageError('非法 JSON 数值：' + value)))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PackageError('更新元数据不是有效 UTF-8 JSON。') from error


def _fingerprints(value, kind):
    value = _object(value, '安装文件清单')
    if not 1 <= len(value) <= MAX_FILES:
        raise PackageError('安装文件数量不在允许范围。')
    result, folded, total = {}, set(), 0
    for name, item in value.items():
        managed_name(name, kind)
        if name.casefold() in folded:
            raise PackageError('安装清单存在大小写重名。')
        folded.add(name.casefold())
        item = _object(item, '文件摘要')
        size = _integer(item.get('size'), '文件大小', low=0, high=MAX_UNPACKED)
        digest = item.get('sha256')
        if not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None:
            raise PackageError('安装文件摘要无效。')
        result[name] = {'size': size, 'sha256': digest}
        total += size
    if total > MAX_UNPACKED:
        raise PackageError('解压体积超过允许范围。')
    for name in folded:
        parts = name.split('/')
        if any('/'.join(parts[:i]) in folded for i in range(1, len(parts))):
            raise PackageError('安装清单文件与目录冲突。')
    return result


@dataclass(frozen=True)
class Inventory:
    kind: str
    client_version: str | None
    resource_version: str
    files: dict
    min_client_version: str | None = None
    max_client_version: str | None = None

    @property
    def record_name(self):
        return CLIENT_RECORD if self.kind == 'client' else RESOURCE_RECORD

    def to_dict(self):
        raw = dict(schema=1, product=PRODUCT, kind=self.kind, architecture=ARCHITECTURE,
                   resource_version=self.resource_version, files=self.files)
        if self.kind == 'client':
            raw.update(client_version=self.client_version, updater_protocol=PROTOCOL)
        else:
            raw.update(min_client_version=self.min_client_version, max_client_version=self.max_client_version)
        return raw

    @classmethod
    def parse(cls, raw, kind):
        raw = _object(raw, '安装清单')
        if (type(raw.get('schema')) is not int or raw['schema'] != 1
                or raw.get('product') != PRODUCT or raw.get('kind') != kind
                or raw.get('architecture') != ARCHITECTURE):
            raise PackageError('安装清单版本、产品、类型或平台无效。')
        resource = str(Version(raw.get('resource_version')))
        client = minimum = maximum = None
        if kind == 'client':
            client = str(Version(raw.get('client_version')))
            if type(raw.get('updater_protocol')) is not int or raw['updater_protocol'] != PROTOCOL:
                raise PackageError('完整客户端更新协议不匹配。')
        elif kind == 'resource':
            minimum = str(Version(raw.get('min_client_version')))
            maximum = str(Version(raw['max_client_version'])) if raw.get('max_client_version') is not None else None
            if maximum and Version(maximum) < Version(minimum):
                raise PackageError('资源包兼容范围倒置。')
        else:
            raise PackageError('未知安装清单种类。')
        return cls(kind, client, resource, _fingerprints(raw.get('files'), kind), minimum, maximum)


def build_inventory(files, *, kind, resource_version, client_version=None,
                    min_client_version=None, max_client_version=None):
    records = {}
    for source, name in files:
        source = ensure_no_links(source)
        if not source.is_file() or name in records:
            raise PackageError('构建输入缺失、重复或不是常规文件：' + str(source))
        records[name] = dict(size=source.stat().st_size, sha256=digest_file(source))
    raw = Inventory(kind, client_version, resource_version, records,
                    min_client_version, max_client_version).to_dict()
    return Inventory.parse(raw, kind)


def write_inventory_files(archive, files, inventory):
    """Write exactly the inventoried bytes; concurrent source edits fail build."""
    for source, name in files:
        source = ensure_no_links(source)
        size, digest = 0, hashlib.sha256()
        with source.open('rb') as reader, archive.open(name, 'w') as output:
            while block := reader.read(128 * 1024):
                size += len(block)
                if size > inventory.files[name]['size']:
                    raise PackageError('打包期间源文件变大：' + name)
                digest.update(block)
                output.write(block)
        if {'size': size, 'sha256': digest.hexdigest()} != inventory.files[name]:
            raise PackageError('打包期间源文件发生变化，请重新构建：' + name)


def inspect_archive(archive, release):
    """Validate inventory/entry agreement before writing even one extracted file."""
    entries = archive.infolist()
    if len(entries) > MAX_FILES * 2:
        raise PackageError('压缩包条目过多。')
    metadata_name = CLIENT_RECORD if release.package.kind == 'client' else RESOURCE_RECORD
    regular, directories, folded, total = {}, set(), set(), 0
    for entry in entries:
        if '\0' in entry.orig_filename:
            raise PackageError('压缩包路径包含空字符。')
        name = entry.filename[:-1] if entry.is_dir() else entry.filename
        relative_name(name)
        mode = entry.external_attr >> 16
        if (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
                or entry.external_attr & 0x400 or entry.flag_bits & 1):
            raise PackageError('压缩包含链接、特殊文件或加密条目。')
        if name.casefold() in folded:
            raise PackageError('压缩包含重复或大小写冲突路径。')
        folded.add(name.casefold())
        if entry.is_dir():
            directories.add(name)
            continue
        total += entry.file_size
        if total > MAX_UNPACKED + MAX_METADATA:
            raise PackageError('压缩包解压体积过大。')
        regular[name] = entry
    if metadata_name not in regular or regular[metadata_name].file_size > MAX_METADATA:
        raise PackageError('缺少或超大的安装清单。')
    inventory = Inventory.parse(read_json_bytes(archive.read(metadata_name)), release.package.kind)
    if set(regular) != set(inventory.files) | {metadata_name}:
        raise PackageError('压缩包文件与安装清单不一致。')
    all_files_folded = {name.casefold() for name in regular}
    for directory in directories:
        parts = directory.casefold().split('/')
        if any('/'.join(parts[:i]) in all_files_folded for i in range(1, len(parts) + 1)):
            raise PackageError('压缩包文件与目录冲突。')
        if (not any(name.startswith(directory + '/') for name in inventory.files)
                and not (inventory.kind == 'client' and directory == 'runtimes/win-x64/native/plugins')):
            raise PackageError('压缩包包含未声明的空目录。')
    if inventory.kind == 'client':
        if inventory.client_version != str(release.version) or inventory.resource_version != str(release.resource_version):
            raise PackageError('完整包版本与远端声明不一致。')
    elif (inventory.resource_version != str(release.version)
          or inventory.min_client_version != str(release.min_client_version)
          or inventory.max_client_version != (str(release.max_client_version) if release.max_client_version else None)):
        raise PackageError('资源包版本或兼容要求与远端声明不一致。')
    for name, item in inventory.files.items():
        if regular[name].file_size != item['size']:
            raise PackageError('压缩包文件大小与清单不符：' + name)
    return inventory, directories


def extract_verified(package_path, destination, release):
    """Extract only to a new owned directory; never overwrite an existing path."""
    package_path = ensure_no_links(package_path)
    if (package_path.stat().st_size != release.package.size
            or digest_file(package_path) != release.package.sha256):
        raise PackageError('更新包大小或 SHA-256 校验失败。')
    destination = ensure_no_links(destination)
    if destination.exists():
        raise PackageError('解压目录必须是不存在的新目录。')
    with zipfile.ZipFile(package_path) as archive:
        inventory, directories = inspect_archive(archive, release)
        destination.mkdir(parents=False)
        try:
            for name, fingerprint in inventory.files.items():
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                size, digest = 0, hashlib.sha256()
                with archive.open(name) as source, target.open('xb') as output:
                    while block := source.read(128 * 1024):
                        size += len(block)
                        if size > fingerprint['size']:
                            raise PackageError('文件超出声明大小：' + name)
                        digest.update(block)
                        output.write(block)
                if size != fingerprint['size'] or digest.hexdigest() != fingerprint['sha256']:
                    raise PackageError('文件摘要校验失败：' + name)
            for name in directories:
                (destination / name).mkdir(parents=True, exist_ok=True)
            # The validated record is serialized canonically, not copied blindly.
            (destination / inventory.record_name).write_text(
                json.dumps(inventory.to_dict(), ensure_ascii=False, indent=2), encoding='utf8')
        except BaseException:
            # Only this invocation created destination. Never clean a caller's
            # existing directory after a failed or interrupted validation.
            import shutil
            shutil.rmtree(destination)
            raise
    return inventory
