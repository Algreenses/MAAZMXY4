"""把光标挪开（虚拟输入：只给游戏发鼠标消息，**不动真实光标**）。

为什么需要：点击关卡图标后如果游戏卡顿没进去，光标会一直停在图标上，
图标进入高亮态，而 `万世冥阁场景.png` / `七叶树窟场景.png` 这些模板拍的
都是**未高亮**的样子 —— 下一轮 `识别XXX场景` 就会只匹配到 0.55，然后
整条任务被判失败（用户 m10908）。

不能用 Swipe 代替：那个地图是可以拖动的，按住划会把地图拽走
（用户 m10920「地图是可以拉动的，不能让鼠标按着划，只能直接划」）。

为什么不用 MAA 的 `post_relative_move`：实测在 Win32 controller 上
**行为不可预测** —— 连续三次 `(-150,-100)` 让光标落到了
(304,525) → (51,333) → (428,407)，跟请求的位移毫无关系
（2026-10-31 02:54 实测）。

为什么不用 `SetCursorPos`：它移动的是**真实光标**，用户正在用电脑时会被抢走
（用户 m11771「不行，必须虚拟输入，影响我前台」）。所以这里改成往游戏窗口
PostMessage 一条 `WM_MOUSEMOVE`，坐标给窗口客户区的左上角 —— 游戏的悬停
判定跟着这条消息走，而系统光标一动不动。
"""
import ctypes
import ctypes.wintypes
import json

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction

WM_MOUSEMOVE = 0x0200


def _find_game_window():
    """兜底：按标题找游戏窗口（controller.info['hwnd'] 偶发为 None）。"""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if '造梦盒子' in buf.value and user32.IsWindowVisible(hwnd):
                found.append(int(hwnd))
        return True

    user32.EnumWindows(_cb, 0)
    return found[0] if found else 0


def _post_move(hwnd, x, y):
    """给窗口发一条 WM_MOUSEMOVE（客户区坐标），返回 (ok, 说明)。"""
    user32 = ctypes.windll.user32
    lparam = ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)
    ok = user32.PostMessageW(int(hwnd), WM_MOUSEMOVE, 0, lparam)
    if not ok:
        return False, 'PostMessageW 返回 0（窗口可能已关闭）'
    return True, '客户区 (%d,%d)' % (x, y)


@AgentServer.custom_action('move_cursor')
class MoveCursor(CustomAction):
    """给游戏发一条鼠标移动消息，把悬停判定挪到角落，不动真实光标。"""

    def run(self, context, argv):
        raw = getattr(argv, 'custom_action_param', None) or '{}'
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except Exception:  # noqa: BLE001
            args = {}
        x = int(args.get('x', 20))
        y = int(args.get('y', 20))
        try:
            ctrl = context.tasker.controller
            hwnd = 0
            # controller.info 在不同版本里是 dict 也可能是个对象，两种都试
            info = getattr(ctrl, 'info', None)
            if isinstance(info, dict):
                hwnd = info.get('hwnd') or 0
            elif info is not None:
                hwnd = getattr(info, 'hwnd', 0) or 0
            if not hwnd:
                conn = getattr(ctrl, 'connection_info', None)
                if isinstance(conn, dict):
                    hwnd = conn.get('hwnd') or 0
                elif conn is not None:
                    hwnd = getattr(conn, 'hwnd', 0) or 0
            if not hwnd:
                hwnd = _find_game_window()
            if not hwnd:
                print('move_cursor 拿不到 hwnd，跳过', flush=True)
                return CustomAction.RunResult(success=True)
            ok, detail = _post_move(hwnd, x, y)
            print('move_cursor 虚拟移开 → %s %s' % ('成功' if ok else '失败', detail), flush=True)
        except Exception as error:  # noqa: BLE001
            print('move_cursor 异常：%s: %s' % (type(error).__name__, error), flush=True)
        return CustomAction.RunResult(success=True)
