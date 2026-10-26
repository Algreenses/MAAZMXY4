"""Small native-task actions for saved-script preflight and recovery outcomes.

Maa owns recognition, navigation and input through the project's Pipeline. This
module never drives a second scheduler or restarts/rebinds a controller. A round
pins the saved library file and its hash in memory; the next round resolves the
current binding again. The front end owns bounded process recovery.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from uuid import UUID, uuid4

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction

from script_library import resolve_recording_reference
from script_studio.model import MAX_FILE_BYTES, RecordingDocument

ROOT = Path(__file__).resolve().parents[1]
RECORDINGS = ROOT / 'recordings'
STATUS_DIRECTORY = ROOT / 'debug/task-runs'
_rounds = {}
_lock = threading.RLock()


class Cancelled(RuntimeError):
    pass


class CaptureUnavailable(RuntimeError):
    pass


class RecordingChanged(ValueError):
    pass


def _parameters(argv):
    value = getattr(argv, 'custom_action_param', {})
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError('脚本任务参数必须是对象。')
    return value


def _key(argv):
    value = getattr(getattr(argv, 'task_detail', None), 'task_id', None)
    if type(value) is not int or value <= 0:
        raise ValueError('缺少原生任务标识。')
    return value


def _check(context):
    if context.tasker.stopping:
        raise Cancelled('任务已取消。')


def status_path(parameters):
    ident = parameters.get('run_id') or str(uuid4())
    if not isinstance(ident, str) or str(UUID(ident)) != ident:
        raise ValueError('脚本任务运行标识必须为标准 UUID。')
    expected = STATUS_DIRECTORY / (ident + '.json')
    if (expected.resolve().parent != STATUS_DIRECTORY.resolve()
            or not expected.resolve().is_relative_to(ROOT.resolve())):
        raise ValueError('脚本任务状态目录无效。')
    supplied = parameters.get('status_path')
    if supplied is not None and Path(supplied).resolve() != expected.resolve():
        raise ValueError('脚本任务状态只能保存在本工程调试目录。')
    instance = parameters.get('instance_id', '')
    if not isinstance(instance, str) or len(instance) > 128:
        raise ValueError('脚本任务实例标识无效。')
    return ident, expected, instance


def _new_state(parameters):
    ident, path, instance = status_path(parameters)
    return dict(version=1, run_id=ident, instance_id=instance, status='running', reason='',
                path=path, recording=None, phase=parameters.get('phase'), started_at=time.time())


def write_status(state, status, reason='', **extra):
    payload = {key: value for key, value in state.items() if key not in ('path', 'recording', 'reference')}
    payload.update(status=status, reason=reason, updated_at=time.time(), **extra)
    path = state['path']
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.task-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf8', newline='\n') as stream:
            json.dump(payload, stream, ensure_ascii=False, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    state.update(status=status, reason=reason)
    print('[script_task] %s: %s' % (status, reason or '已确认'), flush=True)


def _stop_status(state, status, reason, **extra):
    try:
        write_status(state, status, reason, **extra)
    except Exception as error:
        # Failure to report must still return False to Maa, never count a round.
        print('[script_task] 无法保存失败状态：' + str(error), flush=True)


def inspect_recording(reference):
    path = resolve_recording_reference(RECORDINGS, reference)
    with path.open('rb') as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError('绑定脚本超过16MiB。')
    data = json.loads(raw.decode('utf-8-sig'), parse_constant=lambda _: (_ for _ in ()).throw(ValueError('脚本含非有限数值。')))
    document = RecordingDocument.from_dict(data)
    document.to_dict(strict=True)
    if document.diagnostics:
        raise ValueError('绑定脚本存在操作诊断，请先在脚本工具中修复。')
    if not data['actions']:
        raise ValueError('绑定脚本没有可回放的操作。')
    if min(document.base) != 720:
        raise ValueError('任务脚本必须使用新工程的720基准坐标。')
    if any(action.get('op') == 'scroll' and ('x' in action or 'y' in action) for action in data['actions']):
        raise ValueError('带坐标滚轮尚不支持当前控制器回放。')
    return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), base=list(document.base),
                name=data.get('name', path.stem), duration_ms=document.duration_ms)


def capture(context):
    _check(context)
    controller = context.tasker.controller
    try:
        result = controller.post_screencap().wait()
    except Exception as error:
        _check(context)
        raise CaptureUnavailable('截图作业不可用。') from error
    _check(context)
    if not getattr(result, 'succeeded', False):
        raise CaptureUnavailable('截图作业失败。')
    try:
        frame = result.get() if hasattr(result, 'get') else controller.cached_image
    except Exception as error:
        raise CaptureUnavailable('控制器截图读取失败。') from error
    if frame is None or getattr(frame, 'ndim', 0) != 3 or frame.shape[2] < 3:
        raise CaptureUnavailable('控制器没有返回有效截图。')
    return frame


def white_frame(frame):
    """Only an almost uniform white client is a positively known Flash fault."""
    if frame is None or getattr(frame, 'ndim', 0) != 3 or min(frame.shape[:2]) < 100:
        return False
    height, width = frame.shape[:2]
    sample = frame[height//20:height-height//20:4, width//20:width-width//20:4, :3]
    return bool((sample >= 248).all(axis=2).mean() >= .995 and sample.std() <= 3)


def confirm_white_screen(context, wait=time.sleep):
    for index in range(3):
        if not white_frame(capture(context)):
            return False
        if index < 2:
            end = time.monotonic() + .2
            while time.monotonic() < end:
                _check(context)
                wait(min(.02, max(0, end-time.monotonic())))
    return True


def _override(context, patch):
    if not context.override_pipeline(patch):
        raise ValueError('Maa拒绝本轮任务参数。')


def begin_round(context, parameters, state):
    _check(context)
    reference = parameters.get('file', '@task')
    if reference == '@task':
        reference = '@task:' + parameters['task_binding_id']
    recording = inspect_recording(reference)
    filename = Path(recording['path']).name
    expected_file = parameters.get('expected_recording_file')
    expected_hash = parameters.get('expected_recording_sha256')
    if expected_file is not None or expected_hash is not None:
        if expected_file != filename or expected_hash != recording['sha256']:
            raise RecordingChanged('本轮绑定或脚本已修改，自动恢复不会切换到另一份录制。')
    state.update(recording=recording, reference=reference, recording_file=filename,
                 recording_sha256=recording['sha256'])
    routes = parameters.get('routes', {})
    mode = parameters.get('exit_mode', next(iter(routes), None))
    if routes and mode not in routes:
        raise ValueError('请选择该任务支持的脚本完成方式。')
    frame = capture(context)
    height, width = frame.shape[:2]
    if min(height, width) != 720 or any(abs(a-b) > 2 for a,b in zip(recording['base'], (width,height))):
        raise ValueError('脚本与当前720截图尺寸不一致，请使用相同游戏布局重新录制。')
    playback = dict(file=recording['path'], expected_sha256=recording['sha256'],
                    strict_task=True, expected_short_side=720, expected_base=recording['base'], speed=1)
    playback_node = parameters.get('playback_node')
    if not isinstance(playback_node, str) or not playback_node:
        raise ValueError('任务缺少脚本回放节点配置。')
    patch = {playback_node: {'action': {'param': {'custom_action_param': playback}}}}
    if routes:
        route_node = parameters.get('route_node')
        targets = routes[mode]
        if not isinstance(route_node, str) or not isinstance(targets, list) or not all(isinstance(n, str) for n in targets):
            raise ValueError('任务完成路线配置无效。')
        patch[route_node] = {'next': targets}
    _override(context, patch)
    write_status(state, 'running', 'round_started', file=Path(recording['path']).name, exit_mode=mode)


def begin_recovery(context, parameters, state):
    _check(context)
    slot = parameters.get('save_slot', 1)
    slots = parameters.get('resume_slots', {})
    if type(slot) is not int or str(slot) not in slots:
        raise ValueError('请选择该任务支持的已有存档。')
    state['save_slot'] = slot
    node = parameters.get('resume_choice_node')
    guards = parameters.get('resume_guards', [])
    if not isinstance(node, str) or not node or not isinstance(guards, list):
        raise ValueError('任务恢复选项配置无效。')
    _override(context, {node: {'next': [*guards, slots[str(slot)]]}})
    write_status(state, 'running', 'recovery_login', save_slot=slot)


def fail_round(context, state, reason):
    _check(context)
    recording = state.get('recording')
    if recording is not None:
        current = inspect_recording(state['reference'])
        if current['path'] != recording['path'] or current['sha256'] != recording['sha256']:
            write_status(state, 'fatal', 'recording_changed')
            return False
    if confirm_white_screen(context):
        write_status(state, 'recoverable', 'white_screen')
    else:
        write_status(state, 'fatal', reason or 'unrecognized_screen')
    return False


@AgentServer.custom_action('script_task_state')
class ScriptTaskState(CustomAction):
    def run(self, context, argv):
        state, key = None, None
        terminal = False
        try:
            parameters = _parameters(argv)
            key = _key(argv)
            phase = parameters.get('phase')
            if phase in ('begin', 'recovery_begin'):
                state = _new_state(parameters)
                with _lock:
                    _rounds[key] = state
                if phase == 'begin':
                    begin_round(context, parameters, state)
                else:
                    begin_recovery(context, parameters, state)
                return True
            with _lock:
                state = _rounds.get(key)
            if state is None:
                raise ValueError('缺少本轮初始化状态。')
            _check(context)
            terminal = True
            if phase in ('success', 'ready'):
                write_status(state, phase)
                return True
            if phase == 'fatal':
                write_status(state, 'fatal', str(parameters.get('reason') or 'blocked_dialog'))
                return False
            if phase == 'failure':
                return fail_round(context, state, str(parameters.get('reason') or 'unrecognized_screen'))
            raise ValueError('未知脚本任务检查点。')
        except Cancelled:
            terminal = True
            if state is not None:
                _stop_status(state, 'cancelled', 'user_cancelled')
            return False
        except (CaptureUnavailable, RecordingChanged) as error:
            terminal = True
            reason = 'capture_unavailable' if isinstance(error, CaptureUnavailable) else 'recording_changed'
            if state is not None:
                _stop_status(state, 'fatal', reason, message=str(error))
            return False
        except Exception as error:
            terminal = True
            print('[script_task] 已停止：' + str(error), flush=True)
            if state is not None:
                _stop_status(state, 'fatal', 'configuration_or_recording_error', message=str(error))
            return False
        finally:
            if terminal and key is not None:
                with _lock:
                    _rounds.pop(key, None)
