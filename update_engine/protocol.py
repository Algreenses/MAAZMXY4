"""Pure update metadata validation and planning; no filesystem or network I/O.

Protocol 2 keeps legacy three-item resource packages and adds complete clients.
Both UIs must use this module rather than infer compatibility from file names.
"""
from dataclasses import dataclass
from functools import total_ordering
import re
from urllib.parse import urlsplit

PRODUCT = 'MAAZMXY4'
PROTOCOL = 2
ARCHITECTURE = 'win-x64'
CHANNELS = frozenset(('stable', 'preview'))
_VERSION = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'
                      r'(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?'
                      r'(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?')


class ProtocolError(ValueError):
    """Metadata is invalid; callers must show a check failure, not up-to-date."""


@total_ordering
class Version:
    def __init__(self, value):
        if not isinstance(value, str) or len(value) > 100:
            raise ProtocolError('版本号必须是 SemVer 字符串。')
        match = _VERSION.fullmatch(value)
        if match is None:
            raise ProtocolError('版本号格式无效：' + value)
        self.text = value
        self.release = tuple(int(match[i]) for i in (1, 2, 3))
        self.prerelease = tuple(match[4].split('.')) if match[4] else ()
        if any(part.isdecimal() and len(part) > 1 and part[0] == '0' for part in self.prerelease):
            raise ProtocolError('预发布版本的数字段不能有前导零。')

    def __eq__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return (self.release, self.prerelease) == (other.release, other.prerelease)

    def __lt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        if self.release != other.release:
            return self.release < other.release
        if not self.prerelease or not other.prerelease:
            return bool(self.prerelease) and not other.prerelease
        for left, right in zip(self.prerelease, other.prerelease):
            if left == right:
                continue
            if left.isdecimal() and right.isdecimal():
                return int(left) < int(right)
            if left.isdecimal() != right.isdecimal():
                return left.isdecimal()
            return left < right
        return len(self.prerelease) < len(other.prerelease)

    def __str__(self):
        return self.text


def _object(value, label):
    if not isinstance(value, dict):
        raise ProtocolError(label + '必须是对象。')
    return value


def _integer(value, label, low=1, high=2**63 - 1):
    if type(value) is not int or not low <= value <= high:
        raise ProtocolError(label + '不在允许的整数范围。')
    return value


def https_url(value):
    if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) < 33 for c in value):
        raise ProtocolError('下载地址格式无效。')
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ProtocolError('下载地址格式无效。') from error
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.fragment or '\\' in value):
        raise ProtocolError('更新地址必须使用不含凭据或片段的 HTTPS URL。')
    return value


@dataclass(frozen=True)
class Package:
    kind: str
    size: int
    sha256: str
    urls: tuple[str, ...]

    @classmethod
    def parse(cls, value, expected_kind):
        value = _object(value, '更新包')
        if value.get('kind') != expected_kind or value.get('format') != 'zip':
            raise ProtocolError('更新包类型或格式不匹配。')
        size = _integer(value.get('size'), '更新包大小', high=8 * 1024**3)
        digest = value.get('sha256')
        if not isinstance(digest, str) or re.fullmatch(r'[0-9a-fA-F]{64}', digest) is None:
            raise ProtocolError('更新包必须提供完整的 SHA-256。')
        urls = value.get('urls')
        if not isinstance(urls, list) or not 1 <= len(urls) <= 8:
            raise ProtocolError('更新包必须提供1至8个下载地址。')
        urls = tuple(https_url(url) for url in urls)
        if len(set(urls)) != len(urls):
            raise ProtocolError('更新包下载地址重复。')
        return cls(expected_kind, size, digest.lower(), urls)


@dataclass(frozen=True)
class Release:
    version: Version
    package: Package
    notes: str
    resource_version: Version | None = None
    min_updater_protocol: int = PROTOCOL
    min_client_version: Version | None = None
    max_client_version: Version | None = None

    @classmethod
    def parse(cls, value, kind, channel):
        value = _object(value, kind + '版本')
        version = Version(value.get('version'))
        if channel == 'stable' and version.prerelease:
            raise ProtocolError('正式渠道不能提供预发布版本。')
        notes = value.get('notes', '')
        if not isinstance(notes, str) or len(notes) > 20000:
            raise ProtocolError('更新说明格式无效。')
        package = Package.parse(value.get('package'), kind)
        if kind == 'client':
            resource = Version(value.get('resource_version'))
            if channel == 'stable' and resource.prerelease:
                raise ProtocolError('正式客户端不能包含预发布资源。')
            return cls(version, package, notes, resource_version=resource,
                       min_updater_protocol=_integer(value.get('min_updater_protocol'), '最低更新协议'))
        minimum = Version(value.get('min_client_version'))
        maximum = Version(value['max_client_version']) if value.get('max_client_version') is not None else None
        if maximum is not None and maximum < minimum:
            raise ProtocolError('客户端兼容范围倒置。')
        return cls(version, package, notes, min_client_version=minimum, max_client_version=maximum)


@dataclass(frozen=True)
class Manifest:
    channel: str
    sequence: int
    architecture: str
    client: Release | None
    resource: Release | None

    @classmethod
    def parse(cls, value, channel='stable', architecture=ARCHITECTURE):
        value = _object(value, '版本清单')
        if type(value.get('schema')) is not int or value['schema'] != PROTOCOL:
            raise ProtocolError('不支持的更新协议，请使用完整客户端人工升级。')
        if value.get('product') != PRODUCT:
            raise ProtocolError('不是本项目的更新清单。')
        if channel not in CHANNELS or value.get('channel') != channel:
            raise ProtocolError('更新渠道不匹配。')
        if value.get('architecture') != architecture:
            raise ProtocolError('更新包平台架构不匹配。')
        sequence = _integer(value.get('sequence'), '发布序号')
        client = Release.parse(value['client'], 'client', channel) if value.get('client') is not None else None
        resource = Release.parse(value['resource'], 'resource', channel) if value.get('resource') is not None else None
        if client is None and resource is None:
            raise ProtocolError('清单没有客户端或资源更新。')
        return cls(channel, sequence, architecture, client, resource)


@dataclass(frozen=True)
class Installed:
    client_version: Version
    resource_version: Version
    updater_protocol: int = PROTOCOL


@dataclass(frozen=True)
class Decision:
    status: str
    reason: str
    release: Release | None = None


def choose_update(installed: Installed | None, manifest: Manifest, *, repair=False,
                  minimum_sequence=0, recovery_required=False):
    """Return an explicit decision; callers must never turn errors into 'latest'."""
    _integer(minimum_sequence, '已接受发布序号', low=0)
    if manifest.sequence < minimum_sequence:
        return Decision('blocked', 'manifest_stale')
    if installed is None:
        return Decision('manual', 'bootstrap_required')
    client, resource = manifest.client, manifest.resource
    wants_client = client is not None and (client.version > installed.client_version
        or (repair or recovery_required) and client.version == installed.client_version)
    if wants_client:
        if client.min_updater_protocol > installed.updater_protocol:
            return Decision('manual', 'updater_too_old')
        if client.resource_version < installed.resource_version:
            return Decision('blocked', 'client_would_downgrade_resources')
        return Decision('available', 'client_update', client)
    if repair or recovery_required:
        return Decision('blocked', 'compatible_full_package_required')
    if resource is not None and resource.version > installed.resource_version:
        if (installed.client_version < resource.min_client_version
                or resource.max_client_version is not None and installed.client_version > resource.max_client_version):
            return Decision('blocked', 'client_upgrade_required')
        return Decision('available', 'resource_update', resource)
    return Decision('up_to_date', 'no_new_version')
