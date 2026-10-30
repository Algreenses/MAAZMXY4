"""把光标挪开（只移动，不按键）。

为什么需要：点击关卡图标后如果游戏卡顿没进去，光标会一直停在图标上，
图标进入高亮态，而 `万世冥阁场景.png` / `七叶树窟场景.png` 这些模板拍的
都是**未高亮**的样子 —— 下一轮 `识别XXX场景` 就会像昨晚那样只匹配到
0.55，然后整条任务被判失败（用户 m10908）。

不能用 Swipe 代替：那个地图是可以拖动的，按住划会把地图拽走
（用户 m10920「地图是可以拉动的，不能让鼠标按着划，只能直接划」）。
所以用 MAA 原生的 `post_relative_move`（当前仅 Win32 controller 支持）。
"""
import json

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction


@AgentServer.custom_action('move_cursor')
class MoveCursor(CustomAction):
    """按相对位移挪动光标，不按下任何键。"""

    def run(self, context, argv):
        raw = getattr(argv, 'custom_action_param', None) or '{}'
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except Exception:  # noqa: BLE001
            args = {}
        dx = int(args.get('dx', -600))
        dy = int(args.get('dy', -380))
        try:
            context.controller.post_relative_move(dx, dy).wait()
        except Exception as error:  # noqa: BLE001
            # 挪光标失败不该让任务失败：最坏情况只是图标还高亮着，
            # 后面的识别会自己判出来。
            print('move_cursor 失败：%s' % error, flush=True)
        return CustomAction.RunResult(success=True)
