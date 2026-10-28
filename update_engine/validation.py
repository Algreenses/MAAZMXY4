"""Offline package dependency checks; no game connection or package execution."""
from .packages import PackageError, ensure_no_links
from .runtime import PYTHON_REQUIRED, PYTHON_DIRECTORIES
from .storage import read_json

RESOURCE_REQUIRED = ('interface.json', 'agent/main.py', 'agent/script_studio_service.py',
                     'agent/task_asset_sync.py', 'agent/recordings/manifest.json')
CLIENT_REQUIRED = ('MFAAvalonia.exe', 'MFAAvalonia.dll', 'MFAAvalonia.deps.json',
                   'MFAAvalonia.runtimeconfig.json', 'libloader.dll', 'libs/MFAAvalonia.Core.dll',
                   'runtimes/win-x64/native/MaaFramework.dll', 'MAA造梦西游4.exe', 'launcher-build.json',
                   'updater.py', 'update_engine/__main__.py', 'update_engine/worker.py',
                   'update_engine/runtime.py', 'update-config.json', 'script-studio-build.json')


def validate_stage(stage, inventory, *, source=None):
    stage = ensure_no_links(stage)
    required = list(RESOURCE_REQUIRED)
    if inventory.kind == 'client':
        required += list(CLIENT_REQUIRED)
        required += ['runtimes/python/' + name for name in PYTHON_REQUIRED if name not in PYTHON_DIRECTORIES]
        for name in PYTHON_DIRECTORIES:
            prefix = 'runtimes/python/' + name + '/'
            if not any(file.startswith(prefix) for file in inventory.files):
                raise PackageError('完整包缺少运行依赖目录：' + prefix)
    for name in required:
        if (name not in inventory.files or inventory.files[name]['size'] <= 0
                or not ensure_no_links(stage / name).is_file()):
            raise PackageError('更新包缺少必需运行文件：' + name)
    interface = read_json(stage / 'interface.json')
    if (not isinstance(interface, dict) or interface.get('version') != inventory.resource_version
            or not isinstance(interface.get('task'), list) or not interface['task']
            or not isinstance(interface.get('option'), dict)):
        raise PackageError('包内任务定义、版本或选项不完整。')
    if inventory.kind == 'client' and source is not None:
        from .service import source_configuration
        next_source = source_configuration(stage)
        if (next_source['repository'], next_source['channel']) != (source['repository'], source['channel']):
            raise PackageError('完整包试图切换仓库或更新渠道，请使用明确的手动安装流程。')
