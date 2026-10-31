# -*- coding: utf-8 -*-
"""九转炼丹 —— 决策层（纯逻辑，不碰鼠标）

规则（玩家确认）：
  - 点空格放置「下颗丹药」
  - 3 个同级球横/竖连成直线 → 合成 1 个高一级的球
  - 合成结果落在三颗里**最靠左 / 最靠下**的那一格
  - 合成后自动连锁；九级不能再合
  - 丹渣（-1）是障碍，只能用净丹露清除
"""
from __future__ import annotations

from dataclasses import dataclass

GRID_ROWS = {
    0: (2, 3, 4),
    1: (1, 2, 3, 4, 5),
    2: (0, 1, 2, 3, 4, 5, 6),
    3: (0, 1, 2, 3, 4, 5, 6),
    4: (1, 2, 3, 4, 5),
    5: (2, 3, 4),
}
MAX_LEVEL = 9

Board = dict  # {(r, c): 0=空 / -1=渣 / 1..9}


def empty_cells(board: Board) -> list[tuple[int, int]]:
    """可落子的格子。

    含 -2（识别拿不准）：只认 v==0 的话，一旦空格被认成 ?，空格数会归零、
    立刻误报「棋盘满了」。真正防卡死靠调用方的「点过没反应就拉黑」机制。
    """
    return [k for k, v in board.items() if v <= 0 and v != -1]


def _neighbors(r: int, c: int) -> list[tuple[int, int]]:
    """棋盘是菱形，用「同行相邻列」和「同列相邻行」两个方向即可。"""
    return [(r, c - 1), (r, c + 1), (r - 1, c), (r + 1, c)]


def find_lines(board: Board, anchors: set | None = None) -> list[tuple[tuple[int, int], ...]]:
    """找出所有「3 个相邻同级球」的直线（横或竖），返回三格坐标元组。

    anchors 非空时只保留**包含其中任一格**的线——游戏里盘面上已有的三连
    不会自己消，只有新放/新合成的位置参与了才判定。
    """
    out: list[tuple[tuple[int, int], ...]] = []
    seen: set[tuple] = set()

    def _push(run: list[tuple[int, int]]) -> None:
        if len(run) >= 3:
            key = tuple(run[:3])
            if key not in seen:
                seen.add(key)
                out.append(key)

    for r, cols in GRID_ROWS.items():
        # 横向
        run: list[tuple[int, int]] = []
        for c in sorted(cols):
            v = board.get((r, c), 0)
            if v > 0 and run and board.get(run[-1], 0) == v:
                run.append((r, c))
            else:
                _push(run)
                run = [(r, c)] if v > 0 else []
        _push(run)

    # 纵向
    for c in range(7):
        run = []
        for r in range(6):
            if c not in GRID_ROWS[r]:
                _push(run)
                run = []
                continue
            v = board.get((r, c), 0)
            if v > 0 and run and board.get(run[-1], 0) == v:
                run.append((r, c))
            else:
                _push(run)
                run = [(r, c)] if v > 0 else []
        _push(run)

    if anchors:
        out = [L for L in out if any(cell in anchors for cell in L)]
    return out


def _landing(line: tuple[tuple[int, int], ...]) -> tuple[int, int]:
    """合成球落在哪：先取列最小的（最左），同列则取行最大的（最下）。"""
    return min(line, key=lambda rc: (rc[1], -rc[0]))


def merge_once(board: Board, line: tuple[tuple[int, int], ...]) -> Board:
    nb = dict(board)
    level = board[line[0]]
    for cell in line:
        nb[cell] = 0
    land = _landing(line)
    nb[land] = min(level + 1, MAX_LEVEL)
    return nb


@dataclass
class SimResult:
    score: float
    merges: int
    top_level: int
    chains: int
    board: Board


def simulate(board: Board, pos: tuple[int, int], ball: int) -> SimResult:
    """在 pos 放 ball，只判定与「新落点」相关的连线，逐轮连锁，返回结果。"""
    b = dict(board)
    b[pos] = ball
    merges = 0
    chains = 0
    top = 0
    earned = 0.0
    anchors = {pos}

    while True:
        lines = find_lines(b, anchors)
        if not lines:
            break
        # 保险丝：如果 find_lines 反复返回同一批（比如 merge_once 没真正改动盘面），
        # 这里会无限循环。连锁正常不会超过 6 轮，给到 50 已经非常宽松。
        if chains >= 50:
            break
        chains += 1
        lines.sort(key=lambda L: -b[L[0]])
        next_anchors: set = set()
        for L in lines:
            if b[L[0]] <= 0:
                continue
            lvl = b[L[0]]
            if not all(b[cell] == lvl for cell in L):
                continue
            land = _landing(L)
            b = merge_once(b, L)
            merges += 1
            top = max(top, min(lvl + 1, MAX_LEVEL))
            earned += (lvl + 1) ** 2 * (1 + 0.35 * (chains - 1))
            next_anchors.add(land)
        anchors = next_anchors

    return SimResult(earned, merges, top, chains, b)


def _center_bonus(pos: tuple[int, int]) -> int:
    """越靠近棋盘中心分越高（-3~6）。

    `_crowd_bonus` 只会「贴着最近的球」，如果第一颗球落在最上面一行，
    后面就会一路贴着边往下/往上堆，整盘球都挤在边缘。加这一项让**开局
    往中心落**，边缘留给后面合成用。
    """
    dr = abs(pos[0] - 2.5)
    dc = abs(pos[1] - 3.0)
    return int(round(6 - (dr + dc) * 0.8))


def _crowd_bonus(board: Board, pos: tuple[int, int]) -> int:
    """「离最近的已有球」越近加分越高（0~3）。

    盘面上暂时没有同号可贴时（游戏随机发牌，常见），如果还往空处撒，
    球会越来越散、谁也合不了。贴在已有球边上至少能保持密集，
    等发到同号时就能连成线。
    """
    best = 99
    for k, v in board.items():
        if v == 0:
            continue
        d = abs(k[0] - pos[0]) + abs(k[1] - pos[1])
        if d < best:
            best = d
    if best >= 99:
        return 1
    return max(0, 6 - best)


def reserved_cells(board: Board) -> dict[tuple[int, int], int]:
    """返回「这格必须留给某个号」→ {位置: 需要几号}。

    两类稀缺位置（用户 m06583 / m06599）：
      1. 两连（`七 七`）的延伸格。一组两连有**两个**延伸位，
         只有**两个都被占、或只剩一个空**时才算死；两个都空时随便占一个没问题。
      2. 断点两连（`七 空 七`）中间那格——它只有这一个位置，必须保护。
    """
    groups: list[tuple[int, list[tuple[int, int]]]] = []
    for pos, v in board.items():
        if v <= 0:
            continue
        r, c = pos
        for dr, dc in ((0, 1), (1, 0)):
            # ---- 连续两连：run 长度 2 时，两端各往外一格是延伸位 ----
            run = [pos]
            for k in (1, 2):
                nb = (r + dr * k, c + dc * k)
                if board.get(nb) == v:
                    run.append(nb)
                else:
                    break
            if len(run) == 2:
                slots = []
                for end, step in ((run[0], -1), (run[-1], 1)):
                    p2 = (end[0] + dr * step, end[1] + dc * step)
                    if p2 in board:
                        slots.append(p2)
                if slots:
                    groups.append((v, slots))
            # ---- 断点两连：pos 与 pos+2 同号、中间一格为空 ----
            mid = (r + dr, c + dc)
            far = (r + dr * 2, c + dc * 2)
            if board.get(mid) == 0 and board.get(far) == v:
                groups.append((v, [mid]))

    out: dict[tuple[int, int], int] = {}
    for v, slots in groups:
        free = [p for p in slots if board.get(p) == 0]
        if len(free) == 1:
            # 只剩一个空位 → 这是这组唯一的希望，谁都不许占。
            # 同一格被两组抢时**留高级球的**（用户 m06923）：高级球合出来更
            # 难、占格更久、分数更高，低级的两个放弃了顶多再等多来两颗。
            out[free[0]] = max(out.get(free[0], 0), v)
        # free 为 2 个：随便占一个都行，不保护
        # free 为 0 个：这组已经废了
    return out


def _pair_bonus(board: Board, pos: tuple[int, int], ball: int) -> int:
    """放下后紧贴的同号球个数（0~4）。

    无三连时所有候选都是 0 分，只按「第一个空格」挑会把球全堆在左上角。
    贴着同号放能凑出两连，是下一步合成的前提。
    """
    r, c = pos
    n = 0
    for nb in _neighbors(r, c):
        if board.get(nb) == ball:
            n += 1
    return n


def _gather(board: Board, pos: tuple[int, int], ball: int) -> int:
    """到最近同号球的曼哈顿距离；没有同号返回 99。

    和 `_pair_bonus`（只数紧贴）/ `_ring_same`（只数同行同列）不同：
    这个不管方向也不管远近，只要场上有同号就给出距离。五~八级必须凑满
    三颗才能升级，散在棋盘两头就永远合不成（用户 m09898「还是没有凑一起」），
    所以要让新来的一颗主动往最近那颗搬，而不是各挑各的风水位。
    """
    ds = [abs(pos[0] - q[0]) + abs(pos[1] - q[1])
          for q, v in board.items() if v == ball]
    return min(ds) if ds else 99


def _near_same(board: Board, pos: tuple[int, int], ball: int) -> int:
    """隔一格还有同号球的邻居数（给两连留出路）。"""
    r, c = pos
    n = 0
    for nb in _neighbors(r, c):
        if board.get(nb, 0) != 0:
            continue
        for nb2 in _neighbors(*nb):
            if nb2 != pos and board.get(nb2) == ball:
                n += 1
                break
    return n


def _foreign(board: Board, pos: tuple[int, int], ball: int) -> int:
    """紧贴的**异号**球个数：越多越堵，尽量避开。"""
    r, c = pos
    return sum(1 for nb in _neighbors(r, c) if board.get(nb, 0) not in (0, ball))


def _ring_same(board: Board, pos: tuple[int, int], ball: int) -> int:
    """同行/同列、距离 2 格以内、且中间没有异号挡住的同号球个数。

    只有横向/纵向相邻才可能连成三连，斜向对凑线毫无帮助——之前按八邻域
    打分，结果球全被摆到斜对角上，看着就是「乱放」。
    """
    r, c = pos
    n = 0
    for dr, dc in ((0, -1), (0, 1), (-1, 0), (1, 0)):
        same = 0
        open_end = False
        for step in (1, 2):
            rr, cc = r + dr * step, c + dc * step
            if (rr, cc) not in board:
                break
            v = board[(rr, cc)]
            if v == ball:
                same += 1
                continue
            open_end = (v == 0)   # 尽头是空格，才有地方补第三颗
            break
        else:
            # 走满两格都是同号球：得再看第三格是不是空的，才知道能不能补上
            rr, cc = r + dr * 3, c + dc * 3
            open_end = board.get((rr, cc), -1) == 0
        if same and open_end:
            n += same
    return n


def _ring_foreign(board: Board, pos: tuple[int, int], ball: int) -> int:
    """八邻域里异号球的个数（含斜向）。"""
    r, c = pos
    n = 0
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            v = board.get((r + dr, c + dc), 0)
            if v not in (0, ball):
                n += 1
    return n


def _room(board: Board, pos: tuple[int, int], ball: int = 0) -> int:
    """位置 (r,c) 所在的行/列，含自己最多能排出多长的连续可用段。

    棋盘是菱形，边缘格子（比如 (2,0)、(3,0)）上下都没格子了，就算
    两个八挨着也永远凑不出 tercet —— 把八级放那儿等于自废一个球
    （用户 m07079「这里只有2个格子，不可能三连」）。返回横竖两个方向
    的最大值，小于 3 就是死路。
    """
    r, c = pos
    best = 1
    for dr, dc in ((0, 1), (1, 0)):
        n = 1
        for sign in (1, -1):
            k = 1
            while (r + dr * k * sign, c + dc * k * sign) in board:
                _v = board[(r + dr * k * sign, c + dc * k * sign)]
                if _v != 0 and _v != ball:
                    break          # 被异号堵住，这一侧到头了
                n += 1
                k += 1
        best = max(best, n)
    return best


def _extendable(board: Board, pos: tuple[int, int], ball: int) -> int:
    """这个位置放下去后，形成的「两连」有几个是**还能长成三连**的。

    只看「旁边有同号」是不够的：用户反馈「会在只能两连没办法三连的地方放，
    这样子还不如放在别的空的地方」——比如一边是 `5 7 _` 这样两端都被堵死的
    两连，放下去永远凑不出第三个，纯占格子。所以这里顺着同号邻居的轴线往
    两头看：**只要有一头是空格，这条两连就还有救**，计 1。
    """
    n = 0
    r0, c0 = pos
    for dr, dc in ((0, 1), (1, 0)):
        # 沿 (+dr,+dc) 方向的连续同号段 + 反方向也要一起看
        seg = [(r0, c0)]
        rr, cc = r0 + dr, c0 + dc
        while board.get((rr, cc)) == ball:
            seg.append((rr, cc)); rr += dr; cc += dc
        rr, cc = r0 - dr, c0 - dc
        while board.get((rr, cc)) == ball:
            seg.insert(0, (rr, cc)); rr -= dr; cc -= dc
        if len(seg) < 2:
            continue
        head = (seg[0][0] - dr, seg[0][1] - dc)
        tail = (seg[-1][0] + dr, seg[-1][1] + dc)
        if board.get(head) == 0 or board.get(tail) == 0:
            n += 1
    return n



def _kills_pair(board: Board, pos: tuple[int, int], ball: int) -> int:
    """把 ball 放到 pos，会让几条「本来还能长」的两连彻底没救。

    用户 m08126：两个八竖在最左列，左右都没位置，右边那个格子被一颗六占了
    ——那条两连就永远凑不出第三个，两颗八变成两块废石头。
    只看「我放下去有没有好处」是不够的，还得看「我这一手会不会把别人的活路堵死」。
    """
    if board.get(pos) != 0:
        return 0
    b2 = dict(board)
    b2[pos] = ball
    killed = 0
    seen = set()
    for p, v in board.items():
        if v <= 0:
            continue
        for nb in _neighbors(*p):
            if board.get(nb) != v:
                continue
            key = (p, nb) if p <= nb else (nb, p)
            if key in seen:
                continue
            seen.add(key)
            if _extendable(board, p, v) > 0 and _extendable(b2, p, v) == 0:
                killed += 1
    return killed



def _open_bonus(board, pos):
    """周围 8 格里还有多少空格——越大越"开阔"。没戏的球往开阔处放，
    盘面就不会挤成一堆，也不会把某一圈占死。"""
    r, c = pos
    n = 0
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            v = board.get((r + dr, c + dc))
            if v == 0:
                n += 1
    return n


def _core_weight(pos):
    """离正中心越近越好（专供「合成落点」用）。

    和 `_center_weight` 的区别：那个是**环形的中圈**偏好（适合随手放球，
    既不挤中心也不贴边），这个是**真正向中心凸**——合出来的高级球要留在
    中间才有机会继续往上合。九级封顶了反过来用（取负）。
    """
    dr = abs(pos[0] - 2.5)
    dc = abs(pos[1] - 3.0)
    d = (dr + dc) / 2.0
    return int(round(12 - d * 6))

def _center_weight(ball: int, pos: tuple[int, int]) -> int:
    """偏好「中圈」：不贴边角，也不挤正中间。

    上一版按等级把一二级球硬赶到边缘，结果是球全堆在棋盘外圈、中间空着，
    反而没地方凑三连（用户原话：「现在都会往边缘放，导致没有空间连成三个，
    应该折中一点，不能在角落但是也不能在正中间」）。

    现在统一用「离中心多远」打分：
      d ≈ 0（正中心）   → 0 分（留给高级球，也避免低级球占死中心）
      d ≈ 1.2（中圈）   → 最高分
      d ≥ 2.5（边缘角） → 负分
    高等级球把峰值往中心挪一点，低等级球往中圈外挪一点，但都不走极端。
    """
    dr = abs(pos[0] - 2.5)
    dc = abs(pos[1] - 3.0)
    d = (dr + dc) / 2.0                      # 0 = 正中心, 2.5 = 最外圈
    # 一二级球：**能合就合、能贴就贴，只有「实在没地方放」时才会走到这里**，
    # 那就往外圈丢。理由是中后期基本不再出一二级球，它们留在中圈只会占死
    # 合成高级球要用的位置（用户 m06394）。
    # 一到五级：越靠外越好。原来只有一二两级这样，三~五和八九级抢同一圈，
    # 于是前期低级球把中圈铺满、后期高级球回来没地方周转（用户 m11662
    # 「是因为前期把中间的路堵住了」）。中圈统一留给六~九级。
    if ball <= 5:
        # 正分且越靠外越高（d 从 0.25 到 1.75）
        return int(round(d * 6))
    # d 的实际范围是 0.25（正中心）~1.75（四个角），峰值放 0.7 更靠中心
    peak = 0.70 - 0.06 * max(0, min(3, ball - 6))   # 球越大，最佳圈越靠中心
    return int(round(6 - abs(d - peak) * 4))


def _makes_three(board: Board, pos: tuple[int, int], ball: int) -> bool:
    """放上这颗球后，它所在的某条横/竖线能不能凑够 3 个同号（含它自己）。

    这是「贴着同号」的升级版：`_pair_bonus` 只数「挨着几个」，贴着两个
    连成 L 形的球也会加分，但那样凑不出直线。用户说的「有两个在一起不放
    旁边反而放到拐角」以及「贴着但连不成」，都靠这个判据收紧。
    """
    r, c = pos
    for dr, dc in ((0, 1), (1, 0)):
        n = 1
        for k in (1, 2):
            if board.get((r + dr * k, c + dc * k)) == ball:
                n += 1
            else:
                break
        for k in (1, 2):
            if board.get((r - dr * k, c - dc * k)) == ball:
                n += 1
            else:
                break
        if n >= 3:
            return True
    return False


def plan(board: Board, ball: int) -> tuple[tuple[int, int] | None, SimResult | None]:
    """枚举所有空格，选最好的一步。返回 (位置, 模拟结果)。

    排序逻辑分两段：
      1. **能合成**的候选优先——按得分/合成次数/最高等级排；
      2. 合不了的时候不要乱放：**贴着同号球放**（凑两连，为下一步铺路），
         同时避开异号球（别把有用的格子堵死）。
    早期版本在这里加了「周围球越少越好」的惩罚，结果把同号也推开了，
    表现就是「一直躲着不让球连成线」，所以去掉了。
    """
    best_pos: tuple[int, int] | None = None
    best_res: SimResult | None = None
    best_key: tuple | None = None
    for pos in empty_cells(board):
        res = simulate(board, pos, ball)
        if res.merges > 0:
            key = (1, res.score, res.merges, res.top_level, 0)
        else:
            key = (0, 0, 0, 0,
                   _pair_bonus(board, pos, ball) * 20
                   + _ring_same(board, pos, ball) * 8
                   + _near_same(board, pos, ball) * 4
                   - _foreign(board, pos, ball) * 2
                   + _crowd_bonus(board, pos)
                   + _center_weight(ball, pos)
                   + _merge_center)
        if best_key is None or key > best_key:
            best_pos, best_res, best_key = pos, res, key
    return best_pos, best_res


# ---------------------------------------------------------------- 2 步前瞻
# 「来的球不一样」——用户实测下一颗与盘面无关，所以按固定权重展开即可。
# 权重先用 run.log 里统计的 52 个样本（1:6 2:4 3:6 4:6 5:9 6:8 7:11 8:2），
# 9 品没出现过，给一个小先验，免得把它当「不可能」。
NEXT_WEIGHTS = (
    (1, 6), (2, 4), (3, 6), (4, 6), (5, 9),
    (6, 8), (7, 11), (8, 2), (9, 1),
)


def _leaf_value(board: Board) -> float:
    """叶子估值：盘面好不好。

    - 高级球本身值钱（等级平方：9 级 = 81，7 级 = 49）；
    - 空格是「以后还能合」的本钱，每个给 22 分；
    - 丹渣是纯负担，扣 40。
    系数是拍的，但相对关系稳定：**一个 9 级球 ≈ 3.7 个空格**。
    """
    v = 0.0
    for lv in board.values():
        if lv > 0:
            v += lv * lv
        elif lv == 0:
            v += 22.0
        elif lv == -1:
            v -= 40.0
    # 「同号相邻对」也是一种本钱：两连只差一颗就能合，比两颗各自孤立的
    # 同号球值钱得多。不加这一项时，叶子估值对所有布局都一样高，搜索就会
    # 挑出「看起来空格一样多、实际全是孤立球」的那种盘面，越走越死。
    seen = set()
    pairs = 0
    for (r, c), lv in board.items():
        if lv <= 0:
            continue
        for dr, dc in ((0, 1), (1, 0)):
            nb = (r + dr, c + dc)
            if board.get(nb) == lv:
                key = tuple(sorted(((r, c), nb)))
                if key not in seen:
                    seen.add(key)
                    pairs += 1
    v += pairs * 15.0
    return v


LAST_DETAIL: list = []   # [(val, pos, room, pair, ext, ring, near, cw)]


def plan2(board: Board, ball: int, weights=None) -> tuple[tuple[int, int] | None, float]:
    # 场上现有最高等级，用于「突破新高」奖励。
    _top_now = max([v for v in board.values() if v > 0], default=0)
    """两步前瞻：先挑第一手，再把「下一颗是什么」按权重展开，取期望最大的那手。

    对应 V_h = max_a [ r9(s,x,a) + Σ_k p(k|s') V_{h-1}(s',k) ]，其中 V 在叶子
    处用 `_leaf_value` 近似（精确 DP 状态空间是 10^30，算不了）。
    """
    w = weights or NEXT_WEIGHTS
    tot = float(sum(x[1] for x in w)) or 1.0
    best_pos, best_val = None, -1e18
    # 只用「确定是空」的格子。`?`（-2）看着像空、其实常常是球或丹渣，
    # 点上去没反应，日志里就是一串「盘面没变，拉黑 (r,c)」。
    cells = [k for k, v in board.items() if v == 0]
    if not cells:
        # 全是 ? 的时候也要能走：识别把空格判成 ? 时，plan2 原本会直接
        # 返回 (None, 0)，表现为「明明还有空位却不下子」（用户 m07336
        # 「球能落上去，但是你一直不点」）。这里退一步把 ? 也当空格用。
        cells = [k for k, v in board.items() if v == -2]
    if not cells:
        cells = empty_cells(board)
    if not cells:
        return None, 0.0
    # ── 先分裂候选 ──────────────────────────────────────────────
    # 「能合成」是硬优先级：只要有任何一个空格能合，就只在能合的里面挑。
    # 之前把中圈奖励直接混进总分，导致它为了一点位置分宁可放弃合成
    # （用户：「并且在能合成的时候不合成，特意避开」）。
    _sims = [(pos, simulate(board, pos, ball)) for pos in cells]
    _detail: list = []
    _merges = [(pos, r1) for pos, r1 in _sims if r1.merges > 0]
    if _merges:
        _cands = _merges
    else:
        # 「能跟现有同号挨上」也是硬优先级，仅次于能合成。
        #
        # 只靠 lay 里的 _extendable*35 顶不住：两步前瞻的 v2 量级是 1000+，
        # 位置分最大才 150，等于没用。于是会出现「场上有两个同号挨着，
        # 却把新球扔到拐角」的情况（用户 m06383 原话）。
        # 空格少的时候要「留蓄水池」：这时候只允许真能凑成三连的落点，
        # 单纯「贴着同号」就免了 —— 贴上去也只是把最后几个空位填满，
        # 后面没地方等第三颗（用户：「还没到最后60秒就已经把盘铺满了」）。
        _n_free = len([k for k, v in board.items() if v == 0])
        _reserved = reserved_cells(board)
        _pairish = [
            (pos, r1) for pos, r1 in _sims
            if _makes_three(board, pos, ball)
            or (_n_free > 2 and (
                _extendable(board, pos, ball) > 0
                # 能贴上同号，但这个位置不能是「别人两连的第三格」：
                # 拿别的号占掉那里，那组两连就永远合不成了。
                or (_pair_bonus(board, pos, ball) > 0
                    # 贴上去却两头都堵死的「死两连」不算可用位：
                    # 实测一局两个 8 上下相邻、两头是 1 和 3，日志已标 ext=0，
                    # 但候选集里只有它俩，于是硬着头皮放（用户 m11211
                    # 「上下两头全是低级球，早就被占了」）。
                    and _extendable(board, pos, ball) > 0
                    and _reserved.get(pos, ball) == ball)))
        ]
        # 兜底时也要避开「别人两连的第三格」：`_pairish` 为空（这一手谁都
        # 贴不上）时原来直接放开到所有空格，于是随手把某个两连的最后希望
        # 占掉了（用户 m06905「为什么这个 7 把 8 的三连毒死了」）。
        if _pairish:
            # 贴同号也要让开保护位：别人的两连只剩这一个希望位，被一颗
            # 贴不上来的球占掉就永久做废了。宁可去角落（用户 m06952
            # 「低级单独的棋子宁可随便找个角落放，也不能堵死高级棋子的
            # 三连机会」）。
            # 保护位只对「级别不高于它」的球生效：八级球贴同号的机会比
            # 一个二级球的两连值钱得多（用户说过「高级的三连比低级的保护
            # 优先级更高」「低级单独的棋子宁可随便找个角落放」）。之前一律
            # 让开，导致八级球被一个二级保护位逼到右上角去了（m07142）。
            # 这里原来是 ，于是「保护位要 5、手上正好是 5」也被
            # 挡掉了 —— 明明能三连却被自己的保护规则拦下（用户 m09202
            # 「为什么不直接贴一起三连」）。改成 ：正主可以坐自己的位。
            _safe = [(p2, r2) for p2, r2 in _pairish
                     if p2 not in _reserved or _reserved[p2] <= ball]
            _cands = _safe or _pairish
        else:
            # 空格实在不够时，从**低级**的保护位开始让步：高级球的两连更
            # 值钱（合出来占格久、分数高），守到最后一刻（用户 m06923）。
            for _lvl in (1, 2, 3, 4, 5, 6, 7, 8):
                _free = [(p2, r2) for p2, r2 in _sims
                         if _reserved.get(p2, 99) > _lvl]
                if _free:
                    break
            _cands = _free or _sims
    for pos, r1 in _cands:
        b1 = r1.board
        v1 = float(r1.score)
        v2 = 0.0
        c1 = empty_cells(b1)
        if not c1:
            v2 = _leaf_value(b1)
        else:
            for k, cnt in w:
                pk = cnt / tot
                best2 = -1e18
                for pos2 in c1:
                    r2 = simulate(b1, pos2, k)
                    s2 = float(r2.score) + _leaf_value(r2.board)
                    if s2 > best2:
                        best2 = s2
                v2 += pk * best2
        # 布局分：光看「立刻得分 + 叶子估值」时，往哪儿放都是平的（都是 0 分、
        # 空格数也一样），于是它会在遍历到的第一个空格（往往是上半盘）随手放，
        # 而下半盘空着。这里把「贴同号 / 两格内同号 / 避开异号 / 中圈偏好」加回来。
        _ext = _extendable(board, pos, ball)
        _pair = _pair_bonus(board, pos, ball)
        # 合成后的新球就落在这一格上。如果这一格贴着边，新球以后也难再升——
        # 所以「能合成」的候选要额外奖励它靠近中圈，权重比普通中圈偏好更高，
        # 让左右都能合时优先选里侧那个。
        _up = min(9, ball + 1) if r1.merges > 0 else ball
        # 突破现有最高级：合出来的球比场上任何球都高一级，会抬高后续手牌
        # 的等级区间（滚雪球的关键，用户 m06639），长线收益远大于这一手分数。
        if r1.merges > 0 and _up > _top_now:
            _break = 800 * (_up - _top_now)

        else:
            _break = 0
        if r1.merges > 0:
            lay = 0.0
            # 合出来的球会一直留在这格，所以**落点本身的位置价值要压倒性优先**：
            # 用「到正中心的曼哈顿距离」做硬扣分（差一格 = 60 分），这样
            # 左右两边都能合时一定选靠里的那个，不会被别的软分值带偏。
            # 九级封顶，反过来往边上赶。
            # 只在「多个候选都能合」时用来分胜负，量级必须小于合成得分，
            # 否则会出现「为了靠中就放弃合成」。
            # 真正留在盘上的是**合成球**，它的位置不一定等于我们点的这一格：
            # 三连后新球落在「最靠左/最下」那颗上。所以位置分要按落点算，
            # 否则点 (2,4) 去合 (2,2)(2,3)，落点其实是 (2,2)，按 (2,4) 算就偏了。
            _land = None
            for _lp, _lv in r1.board.items():
                if _lv == _up and board.get(_lp) != _up:
                    _land = _lp
                    break
            _lp0 = _land if _land else pos
            _dist = abs(_lp0[0] - 2.5) + abs(_lp0[1] - 3.0)
            _merge_center = int(round((_dist if _up >= 9 else -_dist) * 3))
            # 合成球落点如果正好贴着**同级的另一颗**（也就是下一轮三连的同伴），
            # 价值远高于孤零零一颗：这就等于开局白送一条双连。用户 m06743
            # 「低等级的那个双连三连时，应该优先选择让合成出的 +1 级球可以跟
            # 另一个 +1 级球组成双连，而不是像这样斜着」——斜着不算连。
            _twin = 0
            _lr, _lc = _lp0
            for _dr2, _dc2 in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                if r1.board.get((_lr + _dr2, _lc + _dc2)) == _up:
                    _twin += 1
            _merge_center += _twin * 70
            # 九级是死子，合出来就永远占着那一格，所以宁可挤在边角也不要占中圈
            # 的黄金位（用户 m06704「截图里已经有好几个九级，没优先堆在边缘」）。
            if _up >= 9:
                # 九级封顶、永远占着那一格，所以要尽量合到边角去。这个分
                # 必须能压过两步前瞻（v2 是 1000+ 量级），否则完全没用
                # ——之前用 *40（最大 140 分）根本影响不了选择（用户
                # m06802「为什么这个九不落到边缘」）。
                _break += 200 + int(round(_dist * 400))
        else:
            _merge_center = 0
        if _ext == 0 and _pair == 0 and _ring_same(board, pos, ball) == 0:
            # 这一手跟任何同号都凑不上。
            #   × 别全往正中塞 —— 中间是排队合成的主战场（用户否掉过）
            #   × 也别全往最外圈塞 —— 外圈一样会被塞满（用户又否掉过）
            #   ✓ 均匀铺开：优先放在「周围空格最多」的位置，别挤成一堆
            lay = (_open_bonus(board, pos) * 3
                   - _foreign(board, pos, ball) * 2
                   + _center_weight(ball, pos) // 3)  # 同分时略偏中，但不压过开阔度
        else:
            # 七八级的 `_foreign` 只按一半扣：它们本来就是「等第三个」的
            # 球，周围有别的球是好事（将来的落点就在边上），不该因为「挨
            # 着异号」被赶到空旷的角落——之前八级总往 (2,5) 那种角落跑，
            # 就是因为那边周围空、`_foreign` 扣得少（用户 m07011）。
            _fw = 1 if ball >= 6 else 2
            lay = (_pair * 20
                   + _ext * 35
                   + _ring_same(board, pos, ball) * 8
                   + _near_same(board, pos, ball) * 4
                   - _foreign(board, pos, ball) * _fw
                   + _center_weight(ball, pos))
        # 死路惩罚：所在行/列排出不到 3 格的边缘位，放什么球都白搭
        # （连不上三连，还占着一个格子）。扣得比「抱团」那几项都重。
        _rm = _room(board, pos, ball)
        if _rm < 3 and not _merges:
            # 死路惩罚分两档：贴得上同号的扣轻一些（500），完全不挨着
            # 任何同号的重罚（2000）。之前「pair==0 才罚」漏掉了一种情况：
            # 新球贴着自己的同号，却把**旁边另一种球的生长方向**堵死了
            # （用户 m07284：七落在 (2,1)，正好占住 (3,1) 那颗八往上的路）。
            lay -= (500 if _pair > 0 else 2000)
        # 反向也成立：能排出很长一段的位置更值钱（将来放同号球有地方排）。
        # 七、八级尤其需要——它们本来就是「等第三个」，如果挑一条只剩两三格
        # 的边，等第三个来了也排不出三连（用户 m07142「这个 8 又跑右上角去了」）。
        if ball >= 6 and not _merges:
            lay += (_rm - 2) * 18
        # 一二级球单独加权：中后期基本不再出它们，留在中圈只会占死合成
        # 高级球的位置。`_center_weight` 里它们已经被打成本来就「越靠外分
        # 越高」（-d*6，范围 -10~-2），但那一项只有个位数，会被 v2（上千）
        # 完全淹没，所以这里放大到能和前瞻掰手腕的量级。
        if ball <= 5 and not _merges:
            lay += _center_weight(ball, pos) * 120
        # 九级是死子（三个九级也不会再合），堆在一起最好：既省下中圈的
        # 好位置，也避免它们散开各占一格（用户 m06611）。
        if ball >= 9 or _pair_bonus(board, pos, 9) > 0:
            lay += _pair_bonus(board, pos, 9) * 60 - _center_weight(ball, pos) * 3
        # 七、八级是「生成慢、合不掉就长期占格」的球：如果不抱团，三个散在
        # 三处就永远合不成，场上越积越多（用户 m06629）。给贴同号加倍权重，
        # 让它们宁可挨在一起等第三个，也不要各自挑风水位。
        # 5、6 也一起：五级要凑三颗才能合六，而它一局只出 3~6 颗，
        # 散开就是等死；六级同理。之前只有 7、8 有这条，所以 5 一直
        # 各挑各的风水位、三颗永远凑不到一起（用户 m09878「为什么只有5会乱放」）。
        # 「贴着一个长不大的两连」要扣分：旁边有同号、但两侧都被别的球或
        # 边界堵死，放下去只是白占一格、把这条两连彻底做废（用户 m06721
        # 「还是会把棋子放到双连但是没有机会三连的地方」）。
        # 注意只在**没合成**时扣，能合成的候选不参与这个判断。
        if r1.merges == 0 and _pair > 0 and _ext == 0:
            # 惩罚必须足够重：实测一局里两个 8 上下相邻、两头被 1 和 3 堵死，
            # 日志已标 ext=0，但 45 分这点扣分被 v2(937) 完全盖住，
            # 还是放上去了（用户 m11211）。提到 300，让它宁可去开阔处重排。
            lay -= _pair * 2000
        # 「把别人的两连堵死」单独扣分（用户 m08126：两个八竖在最左列，
        # 右边那格被一颗六占了，这条两连就永远没救了）。
        # 与上面那条不同——上面管的是「我放下去自己白占一格」，
        # 这里管的是「我放下去让别人白占两格」，后者损失更大。
        _kp = _kills_pair(board, pos, ball)
        if _kp:
            lay -= _kp * 260
        lay += _break
        val = v1 + v2 + lay
        _detail.append((val, pos, _rm, _pair, _ext,
                        _ring_same(board, pos, ball),
                        _near_same(board, pos, ball),
                        _center_weight(ball, pos), v1, v2, lay))
        if val > best_val:
            best_pos, best_val = pos, val
    _detail.sort(reverse=True)
    LAST_DETAIL[:] = _detail[:8]
    return best_pos, best_val


def describe(res: SimResult) -> str:
    return "得分 %.0f / 合成 %d 次 / 最高 %d 级 / 连锁 %d 轮" % (
        res.score, res.merges, res.top_level, res.chains,
    )


# ------------------------------------------------------------------ 自测
if __name__ == "__main__":
    # 用 f15 那一帧的真实盘面（空=0、渣=-1、数字=等级）
    demo: Board = {}
    for r, cols in GRID_ROWS.items():
        for c in cols:
            demo[(r, c)] = 0
    demo.update({
        (0, 3): 7,
        (1, 1): -1, (1, 3): -1,
        (2, 3): 9, (2, 4): 5, (2, 5): 5,
        (3, 0): 5, (3, 2): 5, (3, 3): 1, (3, 4): 5, (3, 5): 8, (3, 6): 8,
        (4, 1): 9, (4, 2): 7, (4, 3): 5, (4, 4): 5, (4, 5): 8,
        (5, 2): 7, (5, 3): -1, (5, 4): -1,
    })

    print("盘面：")
    for r in range(6):
        print("  " + "  ".join("%3d" % demo[(r, c)] if c in GRID_ROWS[r] else "  ." for c in range(7)))

    for ball in (1, 5, 7, 8, 9):
        pos, res = plan(demo, ball)
        if pos and res:
            print("下颗=%d → 放 %s  %s" % (ball, pos, describe(res)))
        else:
            print("下颗=%d → 无处可放" % ball)
