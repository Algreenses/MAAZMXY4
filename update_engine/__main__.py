"""One request per process, JSON-lines progress and one final response."""
import json
from pathlib import Path
import sys
import traceback

from .packages import PackageError, read_json_bytes


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def handle(root, request):
    from . import service, transaction, worker
    from .storage import read_json, write_json
    method = request.get('method')
    parameters = request.get('params') or {}
    if not isinstance(parameters, dict):
        raise PackageError('更新请求参数必须是对象。')
    if method == 'status':
        preferences = read_json(root / 'config/project-update.json') or {}
        pending = False
        try:
            worker.pending_package(root)
            pending = True
        except (OSError, ValueError):
            pass
        return dict(**service.local_summary(root), preferences=preferences,
                    pending=pending,
                    source=service.source_configuration(root, preferences.get('channel')))
    if method == 'preferences':
        channel = parameters.get('channel')
        service.source_configuration(root, channel)
        preferences = dict(channel=channel, check_on_start=bool(parameters.get('check_on_start', True)),
                           auto_download=bool(parameters.get('auto_download', False)))
        write_json(root / 'config/project-update.json', preferences)
        return preferences
    if method == 'check':
        preferences = read_json(root / 'config/project-update.json') or {}
        return service.check(root, channel=parameters.get('channel') or preferences.get('channel'),
                             repair=bool(parameters.get('repair', False)))
    if method == 'download':
        return service.stage_update(root, progress=lambda done, total: emit(dict(event='progress', done=done, total=total)))
    if method == 'import':
        import shutil
        from .packages import ensure_no_links, digest_file
        path = ensure_no_links(parameters.get('path', ''))
        if not path.is_file() or path.suffix.lower() != '.zip':
            raise PackageError('请选择完整客户端 ZIP。')
        def copy_package(package, destination, **_):
            if package.kind != 'client':
                raise PackageError('当前清单只选择了资源更新；如需重装请先检查完整修复包。')
            if path.stat().st_size != package.size or digest_file(path) != package.sha256:
                raise PackageError('选择的文件与当前渠道已发布的完整包不一致。')
            with path.open('rb') as source, destination.open('xb') as output:
                shutil.copyfileobj(source, output, 128 * 1024)
        return service.stage_update(root, download=copy_package)
    if method == 'install':
        return worker.start_worker(root, allow_modified=bool(parameters.get('allow_modified', False)))
    if method == 'confirm':
        return transaction.confirm_startup(root, parameters.get('token'))
    raise PackageError('未知更新操作。')


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'worker':
        from .worker import run_worker, _log
        directory = Path(__file__).resolve().parent.parent
        try:
            run_worker(directory, recovery='--recover' in sys.argv[2:])
        except Exception as error:
            # pythonw has no console; keep evidence alongside the worker.
            (directory / 'worker-error.log').write_text(traceback.format_exc(), encoding='utf8')
            if sys.platform == 'win32':
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, str(error) + '\n\n日志：' + str(directory / 'worker-error.log'),
                                                 'MAA造梦西游4 · 更新未完成', 0x10)
            return 1
        return 0
    root = Path(__file__).resolve().parent.parent
    try:
        raw = sys.stdin.buffer.readline(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise PackageError('更新请求过大。')
        request = read_json_bytes(raw)
        if not isinstance(request, dict):
            raise PackageError('更新请求格式无效。')
        emit(dict(ok=True, result=handle(root, request)))
        return 0
    except Exception as error:
        emit(dict(ok=False, error=str(error)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
