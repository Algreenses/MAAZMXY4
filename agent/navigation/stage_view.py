"""Restore visible stage icons on source-calibrated, scrollable normal maps."""
from functools import lru_cache
import json
from pathlib import Path

from .graph import NavigationError

DATA = Path(__file__).resolve().parents[1] / 'data/navigation/stage-scroll.json'


@lru_cache(maxsize=1)
def _recipes():
    return {item['target']: item for item in json.loads(DATA.read_text(encoding='utf8'))['maps']}


def wait_stage_ready(context, navigator, target):
    """Let Maa poll a temporarily missing icon, without authorizing any input."""
    navigator._check()
    remaining = max(1, int((navigator.deadline-navigator.clock())*1000))
    entry, ready, modal = 'Nav_StageWait', 'Nav_StageWaitReady', 'Nav_StageWaitModal'
    definitions = {
        entry: dict(action=dict(type='DoNothing'), next=[modal,ready],
                    timeout=min(1200,remaining),rate_limit=200,post_delay=0),
        ready: dict(recognition=dict(type='And',param=dict(all_of=[target])),
                    action=dict(type='DoNothing'),next=[],post_delay=0),
        modal: dict(recognition=dict(type='Or',param=dict(any_of=['Result_RevivePrompt','Nav_CommonConfirm'])),
                    action=dict(type='DoNothing'),next=[],post_delay=0),
    }
    navigator.log('等待关卡图标稳定：仅重新识别，不发送点击。')
    result=context.run_task(entry,definitions)
    navigator._check()
    # A modal can also end the wait. The caller must re-localize the full scene
    # before it can return success or attempt a guarded viewport restoration.
    return result is not None and result.status.succeeded


def restore_stage_view(context, navigator, target):
    recipe = _recipes().get(target)
    if recipe is None:
        return False
    # Each step re-localizes the whole scene before the native action performs
    # its own fresh same-frame map/mode/terrain/prompt guard. Never drag a boss.
    if getattr(navigator,'deadline',None) is None:
        navigator.deadline = navigator.clock() + 120
    for step in range(recipe['max_steps'] + 1):
        navigator._check()
        state = navigator.locate()
        if state == 'task_destination':
            return True
        if state not in ('task_map', 'map_a', 'map_b'):
            return False
        frame = navigator.capture()
        target_hit = context.run_recognition(target, frame)
        navigator._check()
        if target_hit is None:
            raise ValueError('缺少关卡识别资源。')
        if target_hit.hit:
            return True
        if step == recipe['max_steps']:
            return False
        guard = context.run_recognition(recipe['drag_node'], frame)
        navigator._check()
        if guard is None or not guard.hit:
            navigator.log('大地图视野未恢复：未确认地图、普通模式或可拖动地形，已停止。')
            return False
        navigator.log(f"恢复大地图视野：{recipe['task']}（{step+1}/{recipe['max_steps']}）")
        result = context.run_task('GameManagement_ScrollInvoke', {'GameManagement_ScrollInvoke': dict(
            action=dict(type='DoNothing'), next=[recipe['drag_node']], timeout=3000, post_delay=0)})
        navigator._check()
        if result is None or not result.status.succeeded:
            raise NavigationError('大地图拖动未确认，已停止。', reason='transition_failed')
        navigator._settle()
    return False
