"""Install the canonical native candle task into a local runtime, with backup.

Only the candle task, its dedicated options/Pipeline/images and matching saved
task entries are updated. The default invocation is a read-only plan.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile
from uuid import uuid4

TASK_NAME = '悬赏（守卫宝烛）'
ENTRY = TASK_NAME + '-初始化'
OLD_ENTRY = TASK_NAME + '-刷取次数'
PIPELINE_FILE = TASK_NAME + '.json'
SEPARATOR = '<|||>'
MAX_JSON_BYTES = 16 * 1024 * 1024


class SyncConflict(ValueError):
    pass


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    if raw is None:
        raise ValueError('缺少必需的任务配置JSON文件。')
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError('任务配置不能超过16MiB。')
    return json.loads(raw.decode('utf-8-sig'), parse_constant=lambda _value: (_ for _ in ()).throw(ValueError('JSON包含非有限数值。')))


def _encode(value):
    raw = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf8')
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError('任务配置不能超过16MiB。')
    return raw


def _same_json(left, right):
    return json.dumps(left, sort_keys=True, ensure_ascii=False, allow_nan=False) == json.dumps(right, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _safe(root, relative):
    if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative:
        raise ValueError('资源路径必须是工程内相对路径。')
    relative_path = PurePosixPath(relative)
    if relative_path.is_absolute() or any(part in {'.', '..'} for part in relative_path.parts):
        raise ValueError('资源路径不能越出指定工程。')
    path = root.joinpath(*relative_path.parts)
    if path == root or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('资源路径不能越出指定工程。')
    current = root
    for part in relative_path.parts:
        current /= part
        if current.is_symlink() or getattr(current, 'is_junction', lambda: False)():
            raise ValueError('任务安装路径不能经过符号链接或目录联接。')
    return path


def _read(path, inputs):
    raw = path.read_bytes() if path.exists() else None
    inputs[path] = raw
    return raw


def _verify_inputs(inputs):
    for path, expected in inputs.items():
        current = path.read_bytes() if path.exists() else None
        if current != expected:
            raise SyncConflict('准备期间文件发生变化，停止安装：' + str(path))


def _publish(path, raw, expected):
    """Recheck the original bytes immediately before publishing a complete file."""
    current = path.read_bytes() if path.exists() else None
    if (None if current is None else _sha(current)) != expected:
        raise SyncConflict('写入前文件发生变化，未覆盖：' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.native-candle-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        current = path.read_bytes() if path.exists() else None
        if (None if current is None else _sha(current)) != expected:
            raise SyncConflict('暂存期间文件发生变化，未覆盖：' + str(path))
        if expected is None:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass  # A published file remains committed if temporary cleanup fails.


def _templates(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == 'template':
                values = [item] if isinstance(item, str) else item
                if not isinstance(values, list) or not values or any(not isinstance(name, str) for name in values):
                    raise ValueError('Pipeline模板引用无效。')
                yield from values
            else:
                yield from _templates(item)
    elif isinstance(value, list):
        for item in value:
            yield from _templates(item)


def _image_files(root, directory):
    pending = [directory] if directory.exists() else []
    while pending:
        current = pending.pop()
        _safe(root, current.relative_to(root).as_posix())
        for child in sorted(current.iterdir()):
            _safe(root, child.relative_to(root).as_posix())
            if child.is_dir():
                pending.append(child)
            elif child.is_file():
                yield child


def _default_options(task, options):
    result = []
    for name in task.get('option', []):
        schema = options.get(name)
        if not isinstance(schema, dict):
            raise ValueError('任务专用选项缺失：' + str(name))
        selected = dict(name=name)
        if schema.get('type', 'select') == 'select':
            cases = schema.get('cases', [])
            default = '关闭' if name == '宝烛自动恢复' else schema.get('default_case')
            indices = [index for index, case in enumerate(cases) if case.get('name') == default]
            if not indices:
                raise ValueError('任务选项缺少有效默认项：' + name)
            selected['index'] = indices[0]
        elif schema.get('type') == 'input':
            selected['data'] = {item['name']: str(item.get('default', '')) for item in schema.get('inputs', [])}
        else:
            raise ValueError('不支持的原生任务选项类型：' + name)
        result.append(selected)
    return result


def _old_count(item, default):
    value = item.get('repeat_count')
    if value is None:
        value = next((option.get('data', {}).get('刷取次数') for option in item.get('option', [])
                      if isinstance(option, dict) and option.get('name') == '刷取次数'), None)
    if type(value) is bool:
        return default
    try:
        parsed = int(str(value).strip())
        return parsed if 1 <= parsed <= 2147483647 else default
    except (ValueError, TypeError):
        return default


def _is_candle(item):
    return isinstance(item, dict) and (item.get('name') == TASK_NAME or item.get('entry') in {OLD_ENTRY, ENTRY})


def _history_key(value):
    if not isinstance(value, str):
        return False
    name, separator, entry = value.partition(SEPARATOR)
    return bool(separator and (name == TASK_NAME or entry in {OLD_ENTRY, ENTRY}))


def _merge_instance(before, task, defaults):
    after = deepcopy(before)
    items = after.get('TaskItems', [])
    history = after.get('CurrentTasks', [])
    if not isinstance(items, list) or not isinstance(history, list):
        raise ValueError('实例任务列表格式无效。')
    matching = [item for item in items if _is_candle(item)]
    if matching:
        preferred = next((item for item in matching if item.get('entry') == ENTRY), matching[0])
        native = preferred.get('entry') == ENTRY
        updated = deepcopy(preferred)
        for key, value in task.items():
            if key not in {'option', 'repeat_count', 'default_check', 'label'}:
                updated[key] = deepcopy(value)
        updated['entry'] = ENTRY
        updated['name'] = TASK_NAME
        updated['repeat_count'] = preferred.get('repeat_count', task.get('repeat_count', 1)) if native else _old_count(preferred, task.get('repeat_count', 1))
        updated['default_check'] = preferred.get('default_check', task.get('default_check', False))
        if native:
            choices = deepcopy(preferred.get('option', []))
            if not isinstance(choices, list):
                raise ValueError('原生任务选项格式无效。')
            known = {item.get('name') for item in choices if isinstance(item, dict)}
            choices += [deepcopy(item) for item in defaults if item['name'] not in known]
            updated['option'] = choices
        else:
            updated['option'] = deepcopy(defaults)
        replaced = []
        inserted = False
        for item in items:
            if _is_candle(item):
                if not inserted:
                    replaced.append(updated)
                    inserted = True
            else:
                replaced.append(item)
        after['TaskItems'] = replaced
    if matching or any(_history_key(value) for value in history):
        canonical_key = TASK_NAME + SEPARATOR + ENTRY
        updated_history, inserted = [], False
        for value in history:
            if _history_key(value):
                if not inserted:
                    updated_history.append(canonical_key)
                    inserted = True
            else:
                updated_history.append(value)
        if not inserted:
            updated_history.append(canonical_key)
        after['CurrentTasks'] = updated_history
    return after


def sync_native_candle(source_root, target_root, dry_run=True):
    source_root, target_root = Path(source_root).resolve(), Path(target_root).resolve()
    source_inputs, target_inputs, plans = {}, {}, {}
    source_interface_path = _safe(source_root, 'assets/interface.json')
    source_interface = _json(_read(source_interface_path, source_inputs))
    if (not isinstance(source_interface, dict) or not isinstance(source_interface.get('task'), list)
            or not isinstance(source_interface.get('option'), dict)
            or any(not isinstance(item, dict) for item in source_interface['task'])):
        raise ValueError('源任务定义格式无效。')
    tasks = [task for task in source_interface.get('task', []) if task.get('name') == TASK_NAME]
    if len(tasks) != 1 or tasks[0].get('entry') != ENTRY:
        raise ValueError('源工程缺少唯一的原生守卫宝烛任务定义。')
    task = deepcopy(tasks[0])
    options = {name: deepcopy(value) for name, value in source_interface.get('option', {}).items() if name.startswith('宝烛')}
    defaults = _default_options(task, options)
    pipeline_relative = 'assets/resource/base/pipeline/' + PIPELINE_FILE
    pipeline_raw = _read(_safe(source_root, pipeline_relative), source_inputs)
    pipeline = _json(pipeline_raw)
    if not isinstance(pipeline, dict) or ENTRY not in pipeline:
        raise ValueError('原生Pipeline缺少初始化入口。')
    for node in pipeline.values():
        if not isinstance(node, dict):
            raise ValueError('原生Pipeline节点格式无效。')
        for field in ('next', 'on_error'):
            references = node.get(field, [])
            if not isinstance(references, list) or any(not isinstance(key, str) or key not in pipeline for key in references):
                raise ValueError('原生Pipeline包含缺失的后续节点。')

    def plan_bytes(relative, raw):
        path = _safe(target_root, relative)
        old = _read(path, target_inputs)
        if raw != old:
            plans[relative] = (raw, old)

    def plan_json(relative, before, after):
        if not _same_json(before, after):
            plan_bytes(relative, _encode(after))

    runtime_interface_path = _safe(target_root, 'interface.json')
    runtime_raw = _read(runtime_interface_path, target_inputs)
    if runtime_raw is None:
        raise ValueError('目标缺少运行目录 interface.json。')
    before = _json(runtime_raw)
    if (not isinstance(before, dict) or not isinstance(before.get('task'), list)
            or not isinstance(before.get('option'), dict)):
        raise ValueError('目标任务或选项列表格式无效。')
    after = deepcopy(before)
    after_tasks, installed = [], False
    for item in after['task']:
        if _is_candle(item):
            if not installed:
                after_tasks.append(deepcopy(task))
                installed = True
        else:
            after_tasks.append(item)
    if not installed:
        after_tasks.append(deepcopy(task))
    after['task'] = after_tasks
    after['option'].update(options)
    plan_json('interface.json', before, after)
    plan_bytes('resource/base/pipeline/' + PIPELINE_FILE, pipeline_raw)
    source_images = _safe(source_root, 'assets/resource/base/image/bounty-candle')
    for path in _image_files(source_root, source_images):
        relative = path.relative_to(source_root).as_posix()
        raw = _read(_safe(source_root, relative), source_inputs)
        plan_bytes(relative.replace('assets/resource/', 'resource/', 1), raw)
    for template in set(_templates(pipeline)):
        source_template = _safe(source_root, 'assets/resource/base/image/' + template)
        target_template = _safe(target_root, 'resource/base/image/' + template)
        if not source_template.is_file():
            raise ValueError('原生Pipeline源模板不存在：' + template)
        _read(source_template, source_inputs)
        relative = target_template.relative_to(target_root).as_posix()
        if relative not in plans and not target_template.is_file():
            raise ValueError('目标缺少原生Pipeline通用模板：' + template)
        if relative not in plans:
            _read(target_template, target_inputs)
    instances = _safe(target_root, 'config/instances')
    updated_instances = []
    if instances.exists():
        for path in sorted(instances.glob('*.json')):
            relative = path.relative_to(target_root).as_posix()
            _safe(target_root, relative)
            original = _json(_read(path, target_inputs))
            if not isinstance(original, dict):
                raise ValueError('实例配置必须是JSON对象。')
            updated = _merge_instance(original, task, defaults)
            if not _same_json(original, updated):
                updated_instances.append(relative)
                plan_json(relative, original, updated)
    report = dict(dry_run=dry_run, applied=False, backup=None, updated_instances=updated_instances,
                  changes=[dict(path=path, operation='create' if old is None else 'update', sha256=_sha(raw))
                           for path, (raw, old) in plans.items()])
    if dry_run:
        return report
    _verify_inputs({**source_inputs, **target_inputs})
    if not plans:
        report['applied'] = True
        return report
    lock_path = _safe(target_root, '.native-candle-sync.lock')
    lock_descriptor = None
    written = []
    try:
        lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        _verify_inputs({**source_inputs, **target_inputs})
        backup_relative = 'backup/native-candle-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8]
        backup = _safe(target_root, backup_relative)
        backup.mkdir(parents=True, exist_ok=False)
        report['backup'] = backup_relative
        for relative, (_raw, old) in plans.items():
            if old is not None:
                path = _safe(target_root, backup_relative + '/' + relative)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(old)
        (backup / 'installation.json').write_bytes(_encode(dict(version=1, files=[
            dict(path=relative, existed=old is not None, before_sha256=None if old is None else _sha(old), after_sha256=_sha(raw))
            for relative, (raw, old) in plans.items()])))
        for relative, (raw, old) in plans.items():
            path = _safe(target_root, relative)
            _publish(path, raw, None if old is None else _sha(old))
            written.append((path, raw, old))
        report['applied'] = True
        return report
    except Exception as error:
        for path, raw, old in reversed(written):
            try:
                if path.exists() and _sha(path.read_bytes()) == _sha(raw):
                    if old is None:
                        path.unlink()
                    else:
                        _publish(path, old, _sha(raw))
                elif path.exists():
                    error.add_note('回滚保留了其它程序的新修改：' + str(path))
            except OSError as rollback_error:
                error.add_note('未完成回滚，请使用安装备份：' + str(rollback_error))
        if report['backup']:
            error.add_note('安装前备份：' + str(target_root / report['backup']))
        raise
    finally:
        if lock_descriptor is not None:
            os.close(lock_descriptor)
            try:
                lock_path.unlink()
            except OSError:
                pass


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--target', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true', help='Write the plan after the runtime frontend is closed.')
    args = parser.parse_args()
    print(json.dumps(sync_native_candle(args.source, args.target, dry_run=not args.apply), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
