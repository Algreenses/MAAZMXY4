"""Bounded HTTPS transport. TLS validates origin; hashes validate package bytes."""
import hashlib
import os
import ssl
import time
import urllib.request
import uuid

from .packages import PackageError, ensure_no_links
from .protocol import https_url


class Cancelled(PackageError):
    pass


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url):
    request = urllib.request.Request(https_url(url), headers={
        'User-Agent': 'MAAZMXY4-Updater/2', 'Accept-Encoding': 'identity', 'Cache-Control': 'no-cache'})
    opener = urllib.request.build_opener(HTTPSRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    response = opener.open(request, timeout=20)
    try:
        https_url(response.geturl())
        if response.headers.get('Content-Encoding', 'identity') != 'identity':
            raise PackageError('更新服务器返回了不支持的内容编码。')
    except Exception:
        response.close()
        raise
    return response


def fetch_manifest(url, *, opener=open_url):
    with opener(https_url(url)) as response:
        raw = response.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            raise PackageError('远端版本清单过大。')
        return raw


def download(package, destination, *, progress=lambda *_: None, cancelled=lambda: False,
             opener=open_url, report=lambda *_: None):
    destination = ensure_no_links(destination)
    if destination.exists():
        raise PackageError('下载目标已存在，不能覆盖未知文件。')
    destination.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    for url in package.urls:
        if cancelled():
            raise Cancelled('已取消下载。')
        temporary = destination.with_name(destination.name + '.part-' + uuid.uuid4().hex)
        try:
            size, digest, start = 0, hashlib.sha256(), time.monotonic()
            with opener(url) as response, temporary.open('xb') as output:
                declared = response.headers.get('Content-Length')
                if declared is not None and int(declared) != package.size:
                    raise PackageError('服务器文件大小与发布清单不一致。')
                while block := response.read(128 * 1024):
                    if cancelled():
                        raise Cancelled('已取消下载。')
                    size += len(block)
                    if size > package.size or time.monotonic() - start > 3600:
                        raise PackageError('下载超过声明大小或允许时限。')
                    digest.update(block)
                    output.write(block)
                    progress(size, package.size)
                output.flush()
                os.fsync(output.fileno())
            if size != package.size or digest.hexdigest() != package.sha256:
                raise PackageError('下载不完整或 SHA-256 校验失败。')
            # Hard-link publication is exclusive; another writer's file is not
            # overwritten. Temporary and destination share the same volume.
            os.link(temporary, destination)
            return url
        except Cancelled:
            raise
        except (OSError, ValueError) as error:
            errors.append(str(error))
            report(url, str(error))
        finally:
            temporary.unlink(missing_ok=True)
    raise PackageError('所有下载来源均失败：' + '；'.join(errors))
