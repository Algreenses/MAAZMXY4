"""Compile private ZaoMengScript jobs and register an inert native MFA instance.

Compilation only writes to a temporary copy of source user data. Applying a plan
is explicit, hash-checked, backed up and does not start a task or an application.
The raw task snapshots deliberately never use the editor's 720p conversions.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
from uuid import UUID, uuid4


PRIVATE = ".local-migrations/zaomeng"
STATE = PRIVATE + "/tasks.json"
RESOURCE = PRIVATE + "/resource"
CONTROLLER_NAME = "Win32-原工程任务（原尺寸）"
RESOURCE_NAME = INSTANCE_NAME = "原工程任务"
INSTANCE_ID = "zaomeng-imported"
INSTANCE_PATH = f"config/instances/{INSTANCE_ID}.json"
ACTION_NAME = "migrated_task"
MAX_JSON = 16 * 1024 * 1024


class MigrationConflict(ValueError):
    pass


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _encode(value):
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > MAX_JSON:
        raise ValueError("迁移 JSON 超过 16 MiB。")
    return raw


def _decode(raw):
    if len(raw) > MAX_JSON:
        raise ValueError("来源 JSON 超过 16 MiB。")
    return json.loads(raw.decode("utf-8-sig"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("JSON 不能包含非有限数值。")))


def _read_json(path, default=None):
    return _decode(path.read_bytes()) if path.exists() else deepcopy(default)


def _source_snapshot(root):
    paths = set()
    for relative in ("zaomeng", "assets/assistant", "data/scripts", "data/assistant/resource", "data/models/ocr"):
        directory = root / relative
        if directory.exists():
            paths.update(path for path in directory.rglob("*") if path.is_file()
                         and "__pycache__" not in path.parts and path.suffix != ".pyc"
                         and (relative != "zaomeng" or path.suffix == ".py"))
    paths.update(root / "data/assistant" / name for name in ("queue.json", "profile.json", "recovery.json")
                 if (root / "data/assistant" / name).exists())
    result = {}
    for path in sorted(paths):
        if not path.resolve().is_relative_to(root):
            raise ValueError("源数据不能指向工程外部：" + str(path))
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        result[path.relative_to(root).as_posix()] = digest.hexdigest()
    return result


def _safe(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError("迁移路径必须是项目内相对路径。")
    parts = PurePosixPath(relative)
    if parts.is_absolute() or any(part in {".", ".."} for part in parts.parts):
        raise ValueError("迁移路径不能越出项目。")
    path = root.joinpath(*parts.parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("迁移路径指向了项目外部。")
    for parent in (path, *path.parents):
        if parent == root.parent:
            break
        if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
            raise ValueError("迁移目标不能经过符号链接或目录联接。")
    return path


def _atomic(path, raw, expected):
    current = path.read_bytes() if path.exists() else None
    if (None if current is None else _sha(current)) != expected:
        raise MigrationConflict("目标在迁移期间发生变化，已停止覆盖：" + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".task-migration-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if expected is None:
            os.link(temporary, path)
        else:
            # A second check also covers concurrent changes made while staging.
            if not path.exists() or _sha(path.read_bytes()) != expected:
                raise MigrationConflict("目标在写入前发生变化：" + str(path))
            os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # Publication succeeded; a temporary cleanup must not invent a failed transaction.


def _source_modules(source_root):
    """Load the pure source compiler under a distinct, per-invocation package."""
    name = "_zaomeng_migration_" + uuid4().hex
    directory = source_root / "zaomeng"
    spec = importlib.util.spec_from_file_location(name, directory / "__init__.py", submodule_search_locations=[str(directory)])
    if spec is None or spec.loader is None:
        raise ValueError("源工程缺少 zaomeng Python 包。")
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    spec.loader.exec_module(package)
    return name, importlib.import_module(name + ".assistant_library"), importlib.import_module(name + ".macros"), importlib.import_module(name + ".flows")


def _prune(pipeline, navigation, entry, success, failures):
    path = Path(__file__).resolve().parents[1] / "agent/task_migration/navigation.py"
    spec = importlib.util.spec_from_file_location("_task_migration_pruning", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.prune_navigation(pipeline, navigation, entry=entry, success_node=success, failure_nodes=failures)


def raw_recording(source):
    """Map absolute source times to legacy gaps while keeping original pixels."""
    width, height = source["window"]["width"], source["window"]["height"]
    actions, markers, previous = [], [], 0
    mapping = {"key_down": "keydown", "key_up": "keyup", "mouse_down": "down", "mouse_up": "up", "mouse_move": "move", "wheel": "scroll"}
    for event in source["events"]:
        at, kind, data = event["t_ms"], event["kind"], event["data"]
        if kind == "marker":
            markers.append({"at_ms": at, "label": data["label"]})
            continue
        if kind not in mapping:
            raise ValueError("不支持的源录制事件：" + str(kind))
        action = {"t": at - previous, "op": mapping[kind]}
        previous = at
        if kind.startswith("key_"):
            action["key"] = data["vk"]
        else:
            action.update(x=data["x"], y=data["y"])
            if kind in {"mouse_down", "mouse_up"}:
                action["button"] = data["button"]
            elif kind == "wheel":
                if data["delta"] % 120:
                    raise ValueError("原尺寸任务不能静默转换非 120 整倍数滚轮。")
                action.update(dx=data["delta"] // 120 if data["axis"] == "horizontal" else 0,
                              dy=data["delta"] // 120 if data["axis"] == "vertical" else 0)
        actions.append(action)
    return dict(version=1, name=source["name"], base=[width, height], actions=actions,
                duration_ms=source["duration_ms"], source_macro_id=source["id"],
                coordinate_space="raw-client", source_markers=markers)


def _compile(source_root, script_result):
    records = script_result.get("active_recordings", script_result.get("recordings", []))
    recovery = _read_json(source_root / "data/assistant/recovery.json", {})
    package_name = None
    previous_bytecode = sys.dont_write_bytecode
    output, files = [], {}
    try:
        sys.dont_write_bytecode = True
        package_name, library_module, macro_module, flow_module = _source_modules(source_root)
        with tempfile.TemporaryDirectory(prefix="zaomeng-task-compile-") as temporary:
            data = Path(temporary) / "data"
            assistant = data / "assistant"
            assistant.mkdir(parents=True)
            for name in ("queue.json", "profile.json"):
                shutil.copyfile(source_root / "data/assistant" / name, assistant / name)
            shutil.copytree(source_root / "data/assistant/resource", assistant / "resource")
            scripts_dir = data / "scripts"
            scripts_dir.mkdir()
            source_bytes = {}
            for path in sorted((source_root / "data/scripts").glob("*.json")):
                source_bytes[path.stem] = path.read_bytes()
                (scripts_dir / path.name).write_bytes(source_bytes[path.stem])
            library = library_module.AssistantLibrary(source_root, data)
            scripts = {script.id: script for script in macro_module.ScriptLibrary(scripts_dir).list_scripts()}
            width, height = library.profile["width"], library.profile["height"]
            for request in library.load_requests():
                ident = str(UUID(request["id"]))
                compiled = library.prepare(request, scripts, width, height, allow_reference=False, navigation=True)
                pipeline, navigation = _prune(compiled.flow.pipeline, compiled.navigation, compiled.flow.entry,
                                              compiled.success_node, compiled.failure_nodes)
                relative_root = f"{PRIVATE}/tasks/{ident}"
                macros = {}
                for macro_id, macro in compiled.flow.macros.items():
                    relative = f"raw_recordings/{macro_id}.json"
                    files[f"{relative_root}/{relative}"] = _encode(raw_recording(macro))
                    macros[macro_id] = relative
                images = set()
                for node in pipeline.values():
                    images.update(flow_module.recognition_templates(node["recognition"]))
                for name in sorted(images):
                    source = flow_module.asset_path(compiled.root, name)
                    files[f"{relative_root}/image/{name}"] = source.read_bytes()
                bundle = dict(version=1, source_size=[width, height], pipeline=pipeline, navigation=navigation,
                              entry=compiled.flow.entry, success_node=compiled.success_node,
                              failure_nodes=deepcopy(compiled.failure_nodes), iterations=1,
                              max_seconds=compiled.flow.max_seconds, recovery=deepcopy(recovery), macros=macros)
                files[f"{relative_root}/bundle.json"] = _encode(bundle)
                selected = request.get("macro_id")
                source_hash = _sha(source_bytes[selected]) if selected in source_bytes else None
                library_record = next((record for record in records if record.get("source_id") == selected
                                       and record.get("source_sha256") == source_hash), None)
                if selected and library_record is None:
                    raise MigrationConflict("本轮脚本迁移结果缺少当前源录制版本，请重新迁移操作脚本：" + selected)
                target_modified = bool(library_record and (library_record.get("target_modified") or library_record.get("status") == "target_modified"))
                output.append(dict(source_id=ident, source_task_id=request["task_id"], source_enabled=request["enabled"],
                                   label=compiled.label, source_iterations=request["iterations"],
                                   source_macro_id=selected, source_macro_sha256=source_hash,
                                   library_recording=deepcopy(library_record), library_target_modified=target_modified, bundle=bundle,
                                   bundle_path=f"{relative_root}/bundle.json", image_count=len(images),
                                   warnings=list(compiled.warnings)))
    finally:
        sys.dont_write_bytecode = previous_bytecode
        if package_name:
            for name in list(sys.modules):
                if name == package_name or name.startswith(package_name + "."):
                    del sys.modules[name]
    return output, files


def _append_csv(value, item):
    values = [part.strip() for part in str(value or "").split(",") if part.strip()]
    return ",".join(dict.fromkeys([*values, item]))


def _register(interface, instance, settings, tasks):
    interface, instance, settings = deepcopy(interface), deepcopy(instance), deepcopy(settings)
    definitions = interface.setdefault("task", [])
    names = []
    wrappers = {}
    for task in tasks:
        ident = task["source_id"]
        name, entry = "原工程任务_" + ident, "原工程任务_" + ident + "_单轮"
        names.append(name)
        description = (f"由原工程实际队列迁移：{task['label']}。\n\n使用原尺寸输入环境 {task['bundle']['source_size'][0]}×{task['bundle']['source_size'][1]}，"
                       "请保持游戏前台。原工程自动重启恢复尚未接入，失败停止。\n\n"
                       f"原计划 {task['source_iterations']} 轮，由本前端的重复次数统一计数；首次请改为 1 轮验证。")
        definition = dict(name=name, label="原工程 · " + task["label"], entry=entry, default_check=False,
                          repeatable=True, repeat_count=task["source_iterations"],
                          description=description, controller=[CONTROLLER_NAME], resource=[RESOURCE_NAME], option=[])
        existing = next((item for item in definitions if item.get("entry") == entry), None)
        if existing is None:
            definitions.append(definition)
        else:
            definition = deepcopy(existing)  # Keep user-edited labels/defaults/iterations on repeated migration.
        items = instance.setdefault("TaskItems", [])
        if not any(item.get("entry") == entry for item in items):
            saved = deepcopy(definition)
            saved["default_check"] = False
            items.append(saved)
        history_key = name + "<|||>" + entry
        history = instance.setdefault("CurrentTasks", [])
        if history_key not in history:
            history.append(history_key)
        wrappers[f"{RESOURCE}/pipeline/{ident}.json"] = _encode({entry: {
            "recognition": {"type": "DirectHit"}, "action": {"type": "Custom", "param": {
                "custom_action": ACTION_NAME, "custom_action_param": {"task_id": ident}}},
            "next": [], "on_error": [], "max_hit": 1}})
        task.update(name=name, entry=entry)

    controllers = interface.setdefault("controller", [])
    original_names = [item["name"] for item in definitions if item.get("name") not in names
                      and CONTROLLER_NAME not in item.get("controller", [])]
    for controller in controllers:
        if controller.get("name") == CONTROLLER_NAME:
            continue
        allowed = controller.get("task", original_names)
        allowed = [allowed] if isinstance(allowed, str) else allowed
        controller["task"] = [name for name in allowed if name not in names]
    raw = next((item for item in controllers if item.get("name") == CONTROLLER_NAME), None)
    if raw is None:
        raw = dict(name=CONTROLLER_NAME, type="Win32", display_raw=True, task=list(names),
                   win32=dict(class_regex="ApolloRuntimeContentWindow", window_regex="造梦", screencap="FramePool", mouse="Seize", keyboard="Seize"))
        controllers.append(raw)
    else:
        if raw.get("display_raw") is not True:
            raise MigrationConflict("已存在的原工程控制器不是原尺寸模式，保留用户配置并停止迁移。")
        raw["task"] = list(dict.fromkeys([*raw.get("task", []), *names]))
    resources = interface.setdefault("resource", [])
    private_resource = next((item for item in resources if item.get("name") == RESOURCE_NAME), None)
    if private_resource is None:
        resources.append(dict(name=RESOURCE_NAME, path=["./" + RESOURCE], controller=[CONTROLLER_NAME]))
    elif private_resource.get("path") != ["./" + RESOURCE]:
        raise MigrationConflict("同名私人资源已有不同路径，未覆盖。")
    if instance and instance.get("InstanceName", INSTANCE_NAME) != INSTANCE_NAME:
        raise MigrationConflict("专用实例 ID 已被其它实例使用，未覆盖。")
    instance.update(InstanceName=INSTANCE_NAME, CurrentController=1, CurrentControllerName=CONTROLLER_NAME,
                    Resource=RESOURCE_NAME, ContinueRunningWhenError=False, IncludeInGlobalStart=False,
                    BeforeTask="None", AfterTask="None")
    settings["Instances.List"] = _append_csv(settings.get("Instances.List"), INSTANCE_ID)
    settings["Instances.Order"] = _append_csv(settings.get("Instances.Order", settings.get("Instances.List")), INSTANCE_ID)
    return interface, instance, settings, wrappers


def migrate_tasks(source_root, target_root, script_result, dry_run=False):
    source_root, target_root = Path(source_root).resolve(), Path(target_root).resolve()
    if source_root == target_root or source_root.is_relative_to(target_root) or target_root.is_relative_to(source_root):
        raise ValueError("来源与目标工程必须是独立目录。")
    if not isinstance(script_result, dict):
        raise ValueError("需要本轮操作脚本迁移结果。")
    # Snapshot configuration before lengthy compilation, then compare hashes again at commit.
    configuration = {}
    for relative, default in (("interface.json", {}), (INSTANCE_PATH, {}), ("appsettings.json", {}), (STATE, {})):
        path = _safe(target_root, relative)
        raw = path.read_bytes() if path.exists() else None
        configuration[relative] = (raw, _decode(raw) if raw is not None else deepcopy(default))
    interface = configuration["interface.json"][1]
    if not interface.get("resource") or not isinstance(interface.get("task"), list):
        raise ValueError("目标缺少有效的运行 interface.json。")
    source_snapshot = _source_snapshot(source_root)
    tasks, generated = _compile(source_root, script_result)
    report = dict(version=1, source_root=str(source_root), target_root=str(target_root), dry_run=dry_run,
                  applied=False, tasks=tasks, conflicts=[], changes=[], backup=None, source_snapshot=source_snapshot)
    if not tasks:
        return report
    interface, instance, settings, wrappers = _register(interface, configuration[INSTANCE_PATH][1],
                                                       configuration["appsettings.json"][1], tasks)
    generated.update(wrappers)
    model_root = source_root / "data/models/ocr"
    for required in ("det.onnx", "rec.onnx", "keys.txt"):
        if not (model_root / required).is_file():
            raise ValueError("原任务缺少原工程 OCR 模型：" + required)
    for path in sorted(model_root.rglob("*")):
        if path.is_file():
            generated[f"{RESOURCE}/model/ocr/{path.relative_to(model_root).as_posix()}"] = path.read_bytes()
    previous = configuration[STATE][1]
    if previous and (previous.get("version") != 1 or not isinstance(previous.get("files"), dict)):
        raise ValueError("任务迁移清单格式无效，原文件保留。")
    owned = previous.get("files", {})
    plans = {}
    for relative, raw in generated.items():
        path = _safe(target_root, relative)
        old = path.read_bytes() if path.exists() else None
        if old is not None and old != raw and _sha(old) != owned.get(relative):
            report["conflicts"].append(relative)
        plans[relative] = (raw, old)
    for relative, value in (("interface.json", interface), (INSTANCE_PATH, instance), ("appsettings.json", settings)):
        old_raw, old_value = configuration[relative]
        # MFA rewrites JSON formatting/key order during normal use. Preserve its
        # exact bytes when registration would make no semantic configuration change.
        raw = old_raw if old_raw is not None and value == old_value else _encode(value)
        plans[relative] = (raw, old_raw)
    state = dict(version=1, source_root=str(source_root), source_snapshot=source_snapshot,
                 files={**owned, **{path: _sha(raw) for path, raw in generated.items()}},
                 tasks=[{key: value for key, value in task.items() if key != "bundle"} for task in tasks])
    plans[STATE] = (_encode(state), configuration[STATE][0])
    source_after = _source_snapshot(source_root)
    report["conflicts"].extend("source:" + relative for relative in sorted(set(source_snapshot) | set(source_after))
                               if source_snapshot.get(relative) != source_after.get(relative))
    for relative, (raw, old) in plans.items():
        if raw != old:
            report["changes"].append(dict(path=relative, operation="create" if old is None else "update", sha256=_sha(raw)))
    if dry_run:
        return report
    if report["conflicts"]:
        raise MigrationConflict("保留已修改的迁移文件，未写入任何任务配置：" + "、".join(report["conflicts"]))
    lock_path = _safe(target_root, PRIVATE + "/.tasks.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_descriptor = None
    written = []
    try:
        lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        if _source_snapshot(source_root) != source_snapshot:
            raise MigrationConflict("来源在提交前发生变化，未写入任何配置。")
        for relative, (_, old) in plans.items():
            path = _safe(target_root, relative)
            current = path.read_bytes() if path.exists() else None
            if current != old:
                raise MigrationConflict("目标在编译期间发生变化，未写入：" + relative)
        changed = [(path, raw, old) for path, (raw, old) in plans.items() if raw != old]
        if changed:
            backup = PRIVATE + "/backups/" + uuid4().hex
            for relative, _, old in changed:
                if old is not None:
                    path = _safe(target_root, backup + "/" + relative)
                    _atomic(path, old, None)
            report["backup"] = backup
        for relative, raw, old in changed:
            _atomic(_safe(target_root, relative), raw, None if old is None else _sha(old))
            written.append((relative, raw, old))
        report["applied"] = True
    except Exception:
        # Only roll back files this transaction still owns; never replace a concurrent user edit.
        for relative, raw, old in reversed(written):
            path = _safe(target_root, relative)
            if path.exists() and _sha(path.read_bytes()) == _sha(raw):
                if old is None:
                    path.unlink()
                else:
                    _atomic(path, old, _sha(raw))
        raise
    finally:
        if lock_descriptor is not None:
            os.close(lock_descriptor)
            lock_path.unlink(missing_ok=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--script-result", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Write reviewed local configuration; default only reports a plan.")
    args = parser.parse_args()
    result = migrate_tasks(args.source, args.target, _read_json(args.script_result), dry_run=not args.apply)
    summary = {**result, "tasks": [{key: value for key, value in task.items() if key != "bundle"} for task in result["tasks"]]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
