"""Restore visible stage icons on two source-calibrated, scrollable normal maps."""
import json
from pathlib import Path

from .graph import NavigationError

DATA = Path(__file__).resolve().parents[1] / 'data/navigation/stage-scroll.json'


def restore_stage_view(context, navigator, target):
    recipe = next((item for item in json.loads(DATA.read_text(encoding='utf8'))['maps']
                   if item['target'] == target), None)
    if recipe is None:
        return False
    # Each step re-localizes the whole scene before the native action performs
    # its own fresh same-frame map/mode/terrain/prompt guard. Never drag a boss.
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
        if target_hit is None:
            raise ValueError('缺少关卡识别资源。')
        if target_hit.hit:
            return True
        if step == recipe['max_steps']:
            return False
        guard = context.run_recognition(recipe['drag_node'], frame)
        if guard is None or not guard.hit:
            return False
        navigator.log(f"恢复大地图视野：{recipe['task']}（{step+1}/{recipe['max_steps']}）")
        result = context.run_task('GameManagement_ScrollInvoke', {'GameManagement_ScrollInvoke': dict(
            action=dict(type='DoNothing'), next=[recipe['drag_node']], timeout=3000, post_delay=0)})
        navigator._check()
        if result is None or not result.status.succeeded:
            raise NavigationError('大地图拖动未确认，已停止。', reason='transition_failed')
        navigator._settle()
    return False
