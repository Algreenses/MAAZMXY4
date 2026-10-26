"""Execute one private, image-verified ZaoMengScript task through Maa.

MFA supplies a dedicated raw-size Win32/Seize controller. The ordinary 720p
controller and public task templates are never reconfigured here. Loading a
bundle is inert; only an explicitly started ``migrated_task`` action runs it.
"""

from copy import deepcopy
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction

from script_studio.model import RecordingDocument
from task_migration.navigation import GraphNavigator, validate_navigation


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = PROJECT_ROOT / ".local-migrations" / "zaomeng"
MAX_BYTES = 16 * 1024 * 1024
OUTCOME_ACTION = "migrated_task_outcome"
_RUNS = {}
_RUNS_LOCK = threading.Lock()


class TaskStopped(RuntimeError):
    pass


def _read_json(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("迁移文件超过16MiB。")
    return json.loads(raw.decode("utf-8-sig"),
                      parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("迁移文件包含非有限数值。")))


def _private_path(path, parent, private_root):
    if not isinstance(path, (str, Path)) or not str(path):
        raise ValueError("迁移文件路径无效。")
    path = Path(path)
    resolved = (path if path.is_absolute() else Path(parent) / path).resolve()
    if not resolved.is_relative_to(private_root.resolve()):
        raise ValueError("迁移文件不能指向本地私有目录以外。")
    return resolved


def _integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label}必须为{minimum}～{maximum}的整数。")
    return value


def load_bundle(path, private_root=None):
    """Read and validate a local bundle without creating resources or input."""
    root = Path(private_root or PRIVATE_ROOT).resolve()
    path = _private_path(path, root, root)
    raw = _read_json(path)
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1:
        raise ValueError("迁移任务需要version=1。")
    size = raw.get("source_size")
    if not isinstance(size, list) or len(size) != 2:
        raise ValueError("迁移任务缺少原客户区尺寸。")
    for value in size:
        _integer(value, 1, 32768, "原客户区尺寸")
    pipeline = raw.get("pipeline")
    if not isinstance(pipeline, dict) or not 1 <= len(pipeline) <= 5000:
        raise ValueError("迁移任务Pipeline无效。")
    for name, node in pipeline.items():
        if not isinstance(name, str) or not name or not isinstance(node, dict):
            raise ValueError("迁移Pipeline节点无效。")
        for field in ("next", "on_error"):
            references = node.get(field, [])
            if not isinstance(references, list) or any(not isinstance(key, str) or key not in pipeline for key in references):
                raise ValueError("迁移Pipeline包含无效后续节点。")
    for field in ("entry", "success_node"):
        if raw.get(field) not in pipeline:
            raise ValueError("迁移任务入口或成功标记不存在。")
    failures = raw.get("failure_nodes")
    if (not isinstance(failures, dict) or any(key not in pipeline or not isinstance(reason, str) or not reason
                                             for key, reason in failures.items())
            or raw['success_node'] in failures):
        raise ValueError("迁移任务失败标记无效。")
    iterations = _integer(raw.get("iterations", 1), 1, 100000, "单次任务迭代次数")
    limit = _integer(raw.get("max_seconds", 600), 1, 86400, "单轮运行上限")
    validate_navigation(raw.get("navigation"), pipeline)
    macro_paths = raw.get("macros", {})
    if not isinstance(macro_paths, dict):
        raise ValueError("迁移任务录制列表无效。")
    macros = {}
    for ident, value in macro_paths.items():
        if not isinstance(ident, str) or not ident:
            raise ValueError("迁移任务录制标识无效。")
        macro_path = _private_path(value, path.parent, root)
        recording = _read_json(macro_path)
        document = RecordingDocument.from_dict(recording)
        document.to_dict()  # Refuse unbalanced held input before any task starts.
        if type(recording.get('version')) is not int or recording['version'] != 1 or document.base != size:
            raise ValueError("任务录制必须保留原客户区坐标，不能使用720p转换后的副本。")
        if not recording.get('actions'):
            raise ValueError("任务绑定的录制没有键鼠输入。")
        macros[ident] = macro_path
    return SimpleNamespace(data=raw, path=path, private_root=root, source_size=list(size),
                           iterations=iterations, max_seconds=limit, macros=macros)


def prepare_bundle(bundle):
    """Give every invocation isolated node names and private absolute assets."""
    raw, root = bundle.data, bundle.private_root
    prefix = "Migrated_" + uuid4().hex[:12] + "_"
    token = uuid4().hex
    mapping = {name: prefix + name for name in raw['pipeline']}
    failure_mapping = {name: prefix + "Outcome_" + name for name in raw['failure_nodes']}
    image_root = _private_path(raw.get('image_root', 'image'), bundle.path.parent, root)

    def rewrite_templates(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'template':
                    paths = [item] if isinstance(item, str) else item
                    if not isinstance(paths, list) or not paths:
                        raise ValueError("迁移模板引用无效。")
                    resolved = []
                    for filename in paths:
                        target = _private_path(filename, image_root, root)
                        if not target.is_file():
                            raise ValueError("迁移模板不存在：" + str(target))
                        resolved.append(str(target))
                    value[key] = resolved[0] if isinstance(item, str) else resolved
                else:
                    rewrite_templates(item)
        elif isinstance(value, list):
            for item in value:
                rewrite_templates(item)

    pipeline = {}
    for name, original in raw['pipeline'].items():
        node = deepcopy(original)
        for field in ('next', 'on_error'):
            if field in node:
                node[field] = [failure_mapping.get(key, mapping[key]) for key in node[field]]
        rewrite_templates(node.get('recognition'))
        action = node.get('action', {})
        if action.get('type') == 'Custom':
            param = action.get('param', {})
            if param.get('custom_action') != 'ZaomengRecordedMacro':
                raise ValueError("迁移任务含不支持的自定义动作。")
            action_param = param.get('custom_action_param', {})
            if isinstance(action_param, str):
                action_param = json.loads(action_param)
            macro_id = action_param.get('macro')
            if macro_id not in bundle.macros:
                raise ValueError("迁移任务引用了未提供的录制。")
            node['action'] = dict(type='Custom', param=dict(custom_action='battle_play',
                custom_action_param=dict(file=str(bundle.macros[macro_id]), speed=1.0)))
        pipeline[mapping[name]] = node
    # Keep the original failure probes read-only for graph localization. Only
    # business references jump to the corresponding outcome action alias.
    for name, reason in raw['failure_nodes'].items():
        node = deepcopy(pipeline[mapping[name]])
        node.update(next=[], on_error=[], action=dict(type='Custom', param=dict(
            custom_action=OUTCOME_ACTION, custom_action_param=dict(token=token, success=False, reason=reason))))
        pipeline[failure_mapping[name]] = node
    success = mapping[raw['success_node']]
    pipeline[success].update(next=[], on_error=[], action=dict(type='Custom', param=dict(
        custom_action=OUTCOME_ACTION, custom_action_param=dict(token=token, success=True))))
    navigation = deepcopy(raw['navigation'])
    navigation['abort'] = {mapping[name]: reason for name, reason in navigation.get('abort', {}).items()}
    for node in navigation['nodes']:
        for field in ('recognition', 'any', 'exclude'):
            if field in node:
                node[field] = [mapping[name] for name in node[field]]
    for edge in navigation['edges']:
        edge['entry'] = mapping[edge['entry']]
    validate_navigation(navigation, pipeline)
    entries = {mapping[raw['entry']], *(edge['entry'] for edge in navigation['edges'])}
    wrappers = {}
    for index, entry in enumerate(sorted(entries)):
        wrapper = prefix + f"Dispatch_{index}"
        pipeline[wrapper] = dict(recognition=dict(type='DirectHit'), action=dict(type='DoNothing'),
                                next=[entry], timeout=pipeline[entry].get('timeout', 10000))
        wrappers[entry] = wrapper
    return SimpleNamespace(pipeline=pipeline, navigation=navigation, entry=mapping[raw['entry']],
                           success_node=success, failure_nodes={failure_mapping[name]: reason
                           for name, reason in raw['failure_nodes'].items()}, wrappers=wrappers,
                           token=token, source_size=bundle.source_size, iterations=bundle.iterations,
                           max_seconds=bundle.max_seconds, source=bundle)


class ForegroundGuard:
    """Read-only target checks; a watchdog cancels Seize on target/focus loss."""

    def __init__(self, context, size, *, window_api=None, clock=None):
        self.context, self.size = context, tuple(size)
        self.clock = clock or time.monotonic
        info = context.tasker.controller.info
        if (not isinstance(info, dict) or str(info.get('type', '')).lower() != 'win32'
                or type(info.get('hwnd')) is not int or info['hwnd'] <= 0
                or info.get('keyboard_method') != 1 or info.get('mouse_method') != 1):
            raise ValueError("迁移任务需要专用Win32原尺寸控制器及Seize键盘、鼠标输入。")
        self.hwnd = info['hwnd']
        if window_api is None:
            from script_studio.capture import _Win32WindowAPI
            window_api = _Win32WindowAPI()
        self.api = window_api
        self.identity = self.api.inspect(self.hwnd)
        self.deadline = None
        self.error = None
        self._stop = threading.Event()
        self._thread = None
        self.check(require_foreground=False)

    def check(self, require_foreground=True):
        if self.error:
            raise TaskStopped(self.error)
        if self.context.tasker.stopping:
            raise TaskStopped("已停止迁移任务。")
        if self.deadline is not None and self.clock() >= self.deadline:
            raise TimeoutError("迁移任务本轮运行超时。")
        current = self.api.inspect(self.hwnd)
        if any(current[key] != self.identity[key] for key in ('pid', 'class_name', 'width', 'height')):
            raise TaskStopped("目标窗口身份或尺寸已改变，迁移任务已停止。")
        if (current['width'], current['height']) != self.size:
            raise ValueError("目标客户区必须保持原尺寸%d×%d。" % self.size)
        if require_foreground and not self.api.is_foreground(self.hwnd):
            raise TaskStopped("游戏窗口失去焦点，迁移任务已停止。")

    def await_foreground(self, *, timeout=10, wait=None, emit=None, notify=None):
        """Allow the explicit Start click to be followed by a manual window switch."""
        wait = wait or time.sleep
        emit = emit or (lambda _message: None)
        self.check(require_foreground=False)
        if self.api.is_foreground(self.hwnd):
            return
        message = f"请在{timeout}秒内切回目标游戏窗口；迁移任务将在获焦后开始。"
        emit(message)
        if notify is None:
            try:
                from toast import toast
                notify = toast
            except Exception:
                notify = lambda *_args: None
        try:
            notify('迁移任务等待游戏窗口', message)
        except Exception:
            pass
        end = self.clock() + timeout
        while self.clock() < end:
            self.check(require_foreground=False)
            if self.api.is_foreground(self.hwnd):
                return
            wait(min(.05, max(0, end - self.clock())))
        self.check(require_foreground=False)
        if self.api.is_foreground(self.hwnd):
            return
        raise TaskStopped("等待游戏窗口获焦超时，未发送任务输入；请重新开始并切回游戏。")

    def start(self):
        def watch():
            while not self._stop.wait(.05):
                try:
                    self.check()
                except Exception as error:
                    self.error = str(error)
                    try:
                        self.context.tasker.post_stop()
                    except Exception:
                        pass
                    return
        self._thread = threading.Thread(target=watch, daemon=True, name='migrated-task-foreground')
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(.5)


class ContextSession:
    def __init__(self, context, runtime, guard, *, wait, clock, outcome):
        self.context, self.runtime, self.guard = context, runtime, guard
        self.wait, self.clock, self.outcome = wait, clock, outcome

    def screenshot(self):
        self.guard.check()
        controller = self.context.tasker.controller
        job = controller.post_screencap()
        end = self.clock() + 10
        while not job.done:
            self.guard.check()
            if self.clock() >= end:
                raise TimeoutError("迁移任务截图超时。")
            self.wait(.02)
        self.guard.check()
        if not job.succeeded:
            raise RuntimeError("迁移任务截图失败。")
        pixels = controller.cached_image
        if pixels is None or tuple(pixels.shape[:2]) != tuple(reversed(self.runtime.source_size)):
            raise ValueError("截图必须保留原1175×737客户区；请选择迁移任务专用原尺寸控制器。")
        if tuple(controller.resolution) != tuple(self.runtime.source_size):
            raise ValueError("控制器原始分辨率与迁移任务不一致。")
        return pixels.copy()

    def recognize(self, name, pixels):
        self.guard.check()
        detail = self.context.run_recognition(name, pixels)
        self.guard.check()
        if detail is None:
            raise RuntimeError("Maa未能运行迁移识别节点：" + name)
        return detail

    def run_entry(self, entry):
        self.guard.check()
        detail = self.context.run_task(self.runtime.wrappers[entry])
        self.guard.check()
        if self.outcome['failure']:
            raise RuntimeError(self.outcome['failure'])
        if detail is None or not getattr(getattr(detail, 'status', None), 'succeeded', False):
            raise RuntimeError("迁移任务步骤失败或回放未完成。")
        return detail


class MigratedTaskRunner:
    def __init__(self, context, bundle, *, guard=None, clock=None, wait=None, log=None):
        self.context, self.bundle = context, bundle
        self.runtime = prepare_bundle(bundle)
        self.clock, self.wait = clock or time.monotonic, wait or time.sleep
        self.log = log or (lambda message: print('[migrated_task] ' + message, flush=True))
        self.guard = guard or ForegroundGuard(context, bundle.source_size, clock=self.clock)
        self.outcome = dict(success=False, failure='')
        self.completed = 0

    def run(self, iterations=None):
        count = _integer(self.bundle.iterations if iterations is None else iterations, 1, 100000, '单次任务迭代次数')
        self.guard.await_foreground(wait=self.wait, emit=self.log)
        self.guard.check()
        if not self.context.override_pipeline(self.runtime.pipeline):
            raise ValueError("Maa拒绝迁移Pipeline，未发送任务输入。")
        session = ContextSession(self.context, self.runtime, self.guard, wait=self.wait, clock=self.clock, outcome=self.outcome)
        session.screenshot()  # Reject the normal 720p controller before actions.
        with _RUNS_LOCK:
            _RUNS[self.runtime.token] = self.outcome
        try:
            self.guard.start()
            for _ in range(count):
                self.outcome.update(success=False, failure='')
                self.guard.deadline = self.clock() + self.bundle.max_seconds
                navigator = GraphNavigator(session, self.runtime.navigation, check=self.guard.check,
                                           clock=self.clock, wait=self.wait, emit=self.log)
                navigator.run()
                session.run_entry(self.runtime.entry)
                self.guard.check()
                if not self.outcome['success'] or self.outcome['failure']:
                    raise RuntimeError(self.outcome['failure'] or "未确认任务成功返回界面，本轮不计次。")
                self.completed += 1
                self.log(f"本轮已确认完成；本次调用已完成 {self.completed}/{count} 轮。")
            return self.completed
        finally:
            self.guard.close()
            with _RUNS_LOCK:
                _RUNS.pop(self.runtime.token, None)


@AgentServer.custom_action(OUTCOME_ACTION)
class MigratedTaskOutcome(CustomAction):
    def run(self, context, argv):
        param = json.loads(argv.custom_action_param)
        with _RUNS_LOCK:
            outcome = _RUNS.get(param.get('token'))
            if outcome is None:
                return False
            if param.get('success') is True:
                if not outcome['failure']:
                    outcome['success'] = True
                return True
            outcome['failure'] = param.get('reason') or '迁移任务未完成。'
            return False


@AgentServer.custom_action("migrated_task")
class MigratedTask(CustomAction):
    def run(self, context, argv):
        try:
            param = json.loads(argv.custom_action_param)
            ident = param['task_id']
            if not isinstance(ident, str) or str(UUID(ident)) != ident:
                raise ValueError("迁移任务标识无效。")
            bundle = load_bundle(PRIVATE_ROOT / 'tasks' / ident / 'bundle.json')
            if bundle.data.get('recovery'):
                print('[migrated_task] 源恢复设置仅保留为迁移元数据；自动重启与重新登录未启用。', flush=True)
            runner = MigratedTaskRunner(context, bundle)
            runner.run(param.get('iterations'))
            return True
        except Exception as error:
            print('[migrated_task] 已停止：' + str(error), flush=True)
            return False
