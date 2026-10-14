# -*- coding: utf-8 -*-
#
# 造梦西游4 · 生存大冒险 自动答题
# 作者：InkyuZero   (https://github.com/Inkyu-Zero/MAAZMXY4)
#
# 本脚本基于 MaaFramework 开发，完全免费。
# 如果你是付费获得的，请及时申请退款 —— 本脚本从未收费。
#
"""生存大冒险 —— 决策核心（纯逻辑，不依赖 maa，可独立测试）

机制（依据用户提供的攻略表转录，见 assets/data/adventure.json）：
  - 每个事件有 4 个选项，每个选项带「成功要求」属性门槛；
  - 当前属性 >= 全部门槛 → 必定成功，否则必定失败（硬门槛，已与用户确认）；
  - 成功/失败分别结算属性增减；每阶段按成败给积分。

决策目标：在"尽量成功（拿大分）"的前提下，让属性收益最大化；若无论如何都会失败，
则选损失最小的选项。
"""

from __future__ import annotations

import difflib
import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

ATTRS: Sequence[str] = ("战力", "智慧", "魅力", "灵巧", "运气", "健康")

# 七个可选角色（每局随机一位）及其初始六维，顺序同 ATTRS。
# 来源：debug/screencap 里「角色」行逐帧像素聚类（7 组，见 adventure_ui.json 的 character_roi）
#       + 开局面板逐格读出，并用 debug/adventure/adventure.log 的 24 局判定反推交叉验证
#       （7 个向量能复现 98% 的「必定成功/必然失败」判定）。
# 用途：开局先认角色，就能拿到**权威**的初始六维 —— 三级属性 OCR 全失败且账本为空时
#       （实测确实发生过），原来会退化成 attrs={}，整个价值函数的地基就错了。
CHARACTERS: Dict[str, Dict[str, int]] = {
    "小沙": {"战力": 3, "智慧": 4, "魅力": 3, "灵巧": 6, "运气": 3, "健康": 3},
    "小吒": {"战力": 4, "智慧": 4, "魅力": 4, "灵巧": 4, "运气": 3, "健康": 5},
    "小唐": {"战力": 4, "智慧": 5, "魅力": 5, "灵巧": 3, "运气": 4, "健康": 3},
    "小猪": {"战力": 4, "智慧": 3, "魅力": 3, "灵巧": 3, "运气": 3, "健康": 6},
    "小玉": {"战力": 3, "智慧": 4, "魅力": 5, "灵巧": 6, "运气": 3, "健康": 3},
    "小猴": {"战力": 5, "智慧": 4, "魅力": 3, "灵巧": 4, "运气": 4, "健康": 4},
    "小白": {"战力": 3, "智慧": 4, "魅力": 4, "灵巧": 4, "运气": 5, "健康": 4},
}

# 角色名只有 7 个、都是两个字且第二个字互不相同（沙/吒/唐/猪/玉/猴/白），
# 所以「末字」就是唯一键 —— 形近字 OCR 认错第一个字不影响判定。
_CHAR_TAIL: Dict[str, str] = {name[-1]: name for name in CHARACTERS}

# 实测见过的形近误认（我自己的转写和 OCR 都出现过）：小圤→小吒、小塘→小唐、小砂→小沙。
_CHAR_ALIAS: Dict[str, str] = {
    "小圤": "小吒", "小叱": "小吒", "小咤": "小吒",
    "小塘": "小唐", "小糖": "小唐",
    "小砂": "小沙", "小纱": "小沙",
    "小诸": "小猪", "小朱": "小猪",
    "小候": "小猴", "小侯": "小猴",
    "小王": "小玉", "小五": "小玉",
}


def character_attrs(text: str) -> Optional[Dict[str, int]]:
    """把 OCR 到的「角色」文本映射成初始六维；认不出来返回 None。

    三层判定，越靠前越可信：
      1. 去除非汉字后正好是角色名（或整个文本包含某个角色名）
      2. 已知形近误认别名
      3. 末字唯一命中（末字表 7 个字互不相同，所以这一层也很硬）
    认不准就返回 None，让调用方退回属性 OCR —— 宁可不用也不要猜错角色，
    因为角色一错，整局的属性基线就全错了。
    """
    if not text:
        return None
    chars = "".join(c for c in str(text) if "\u4e00" <= c <= "\u9fff")
    if not chars:
        return None
    for name in CHARACTERS:
        if name in chars:
            return dict(CHARACTERS[name])
    if chars in _CHAR_ALIAS:
        return dict(CHARACTERS[_CHAR_ALIAS[chars]])
    hit = _CHAR_TAIL.get(chars[-1])
    if hit:
        return dict(CHARACTERS[hit])
    return None

_DEFAULT_DATA = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "assets", "data", "adventure.json"
)

# 默认属性权重：健康归零即冒险结束，故给更高权重；战力/智慧是后续门槛的主力，略高。
DEFAULT_WEIGHTS: Dict[str, float] = {
    "健康": 1.5,
    "战力": 1.2,
    "智慧": 1.2,
    "魅力": 1.0,
    "灵巧": 1.0,
    "运气": 1.0,
}

# 成功一次的额外加分：积分只与成败相关（成功 6/9/12/19 vs 失败 3/4/6/9），
# 因此"能成功"本身就该压过普通属性收益差异。
DEFAULT_SUCCESS_BONUS = 8.0

# 各属性在题库里出现过的**最高门槛**：决定"现在该补哪个属性最有用"。
# 依据是题库全量统计（不是拍脑袋），括号内为该属性被要求的总次数：
#   战力 10（36 处）  灵巧 10（37 处）  魅力 10（35 处）
#   智慧 10（33 处）  运气  9（35 处）  健康  6（**仅 3 处**）
#
# 注意健康：它的门槛只有 3/5/6 三处，target 取 3。
# 实测（1500 局成对比较，见 tools/simulate_adventure.py）：target 从 6 降到 3，
# 平均分 78.44 → 80.11。原因是 target=6 时健康在 3~5 区间每 +1 都落进 NEAR 档，
# 拿 6.0×1.5=9.0 分，压过了堆战力/智慧/灵巧的收益；而健康真正的风险只有
# 「归零提前结束」，那件事由 ZERO_PENALTY 单独兜底。
THRESHOLD_TARGETS: Dict[str, int] = {
    "战力": 10,
    "灵巧": 10,
    "魅力": 10,
    "智慧": 10,
    "运气": 9,
    "健康": 3,
}

# 阈值导向的打分参数：离门槛越近的属性，收益价值越高（"差一点就补上"）
NEAR_GAP, MID_GAP = 3, 5
NEAR_WEIGHT, MID_WEIGHT, FAR_WEIGHT = 6.0, 3.0, 1.0

# 健康属于"生死线"，扣血后剩余很少时的额外权重（剩余 1~2 点时随时可能暴毙）
HEALTH_CRITICAL_WEIGHT = 12.0

# 属性跌到 0 及以下的惩罚
ZERO_PENALTY = 1000.0


@dataclass
class Option:
    index: int
    text: str
    require: Optional[Dict[str, int]]
    success: Dict[str, int] = field(default_factory=dict)
    fail: Dict[str, int] = field(default_factory=dict)
    uncertain: bool = False
    always_fail: bool = False


@dataclass
class Decision:
    event: str
    index: int
    text: str
    will_succeed: bool
    gains: Dict[str, int]
    score: float
    reason: str
    value: float = 0.0
    all_scores: Sequence[Tuple[int, str, bool, float]] = field(default_factory=tuple)
    exact: bool = False

    def describe(self) -> str:
        tag = "［精确后向归纳］" if self.exact else ""
        return (
            f"[{self.event}] 选第 {self.index + 1} 项「{self.text}」"
            f"（{'必定成功' if self.will_succeed else '必然失败'}，"
            f"属性变化 {self.gains or '无'}，评分 {self.score:+.2f}，"
            f"后续期望 {self.value:+.2f}）{tag}"
        )


def load_data(path: Optional[str] = None) -> Dict[str, Any]:
    with open(path or _DEFAULT_DATA, "r", encoding="utf-8") as f:
        return json.load(f)


def load_events(path: Optional[str] = None) -> Dict[str, Any]:
    return load_data(path)["events"]


def get_options(event: Mapping[str, Any]) -> list:
    out = []
    for i, raw in enumerate(event["options"]):
        out.append(
            Option(
                index=i,
                text=raw["text"],
                require=raw.get("require"),
                success=raw.get("success") or {},
                fail=raw.get("fail") or {},
                uncertain=bool(raw.get("_uncertain")),
                always_fail=bool(raw.get("always_fail")),
            )
        )
    return out


def can_succeed(opt: Option, attrs: Mapping[str, int]) -> bool:
    """硬门槛判定。

    - always_fail：表格中该选项没有成功态，恒判失败；
    - require 为 None：表格写作「无要求」，必定成功；
    - 其余：当前属性 >= 全部门槛即成功。
    """
    if opt.always_fail:
        return False
    if not opt.require:
        return True
    return all(int(attrs.get(k, 0)) >= int(v) for k, v in opt.require.items())


# --------------------------------------------------------------------------- #
# 价值函数：把「该堆哪项属性」从阈值分档升级为「后续关键阶段的期望得分」
# --------------------------------------------------------------------------- #
# 决策的真实处境：每阶段的事件是从该阶段题库里**随机抽**的（同一事件可能重复
# 出现，日志里 唐僧受伤 一局内出现过两次），所以脚本无法预知这一局会碰到哪些
# 事件，只能按题库分布做期望最优。
#
# 原做法（_gain_score 的阈值分档）在 target=10 时所有属性 gap 都 > MID_GAP，
# 权重全部落进 FAR_WEIGHT=1.0，退化成「谁的总增益数值大」，与"能不能过关"脱钩。
#
# 现做法：直接估算「带着当前属性去打后面几个关键阶段，期望能拿多少分」。
# 因为过关是硬门槛，这个期望天然奖励「把某项属性顶过门槛」，也天然抑制
# 「每项都涨一点、结果一项都过不了」。
#
# 量级参考（4000 条相同题序成对比较，见 tools/simulate_adventure.py、
# tools/adventure_upper_bound.py）：
#   改造前 78.59  →  当前 80.57  →  理论最优（不知题序）81.33
#   →  上帝视角（预知题序）87.19 —— 后者不可达。
# 满分率 5.25% → 7.28%；阶段 4/5 逐题成功率 Q7 37→43% Q8 38→46% Q9 40→46%
# Q10 16→20%。

# 参与估值的关键阶段：3+4+5 合计 82 分（满分 100），是分数的决定性来源。
# 包含阶段 3（不只是 4+5）是为了让「前 6 题该往哪个属性投资」更早拿到信号，
# 成对比较实测优于只用 4+5（+0.49 vs +0.17）。
VALUE_STAGES: Sequence[int] = (3, 4, 5)

# 价值项与原评分的混合比例。价值项量级在几十、原评分在个位到十几。
#
# 定值过程（成对比较：同一批题序跑两种参数，比配对差与 t 值）：
#   阶段 3+4+5 上扫描，4000 条 × 2 个独立池 → blend 0.25 最好（+0.49 / +0.44）；
#   但那批池是用**单一初始六维（小玉）**跑的。换成真实初始分布（7 个角色等权）
#   重新扫，0.25 反而偏高：0.05~0.20 整片高原都优于它，最终在 4 个**全新**池上
#   定案 0.125（+0.100 / +0.080 / +0.101 / +0.055，合并 +0.084，t = +4.38）。
# 教训：用一组初始值调出来的参数会过拟合那一组；真实角色强弱差很多
# （理论上界从 66.2 到 83.3），必须在真实角色分布上重调。
VALUE_BLEND = 0.125

# 软边距尺度：把「还差几点达标」换成 sigmoid((属性 - 门槛) / VALUE_SCALE)。
#
# 为什么不能用 0/1 硬判定：过关确实是硬门槛，但**决策需要梯度**。硬判定下
# 「灵巧 7 → 8」这类变化只要没跨过门槛就完全不改变价值，启发式因此看不见
# 「再堆 1 点灵巧就能吃下阶段 4 的两个 12 分」，也就不会集中投资——实测个案里
# 这正是后 4 题全灭的原因。换成软边距后任何位置的属性增长都有梯度。
#
# 0.7 由真实初始分布上的 4 个全新池定案（+0.100 / +0.080 / +0.101 / +0.055）；
# 在旧的单角色池上选出的是 1.0 —— 同样是过拟合单角色的表现。
VALUE_SCALE = 0.7

# 无门槛选项在软边距里的「超标余量」，等效于「肯定能过」。
VALUE_FREE_MARGIN = 8.0

# 生存因子：健康 >= 该值时不打折，只剩更少血时价值按比例缩水。
#
# 为什么要单独处理健康：健康见底会让剩余题目**全部作废**（丢分最狠的一种失败），
# 但纯期望分对「健康从 3 掉到 2」毫无感觉（未来没有任何选项要求健康 >= 4）。
# 打折后决策愿意为「别掉到 1 点血」付一点代价。
#
# 实测（4000 局成对比较 × 2 个独立题序池）：
#   关闭        池A 80.482 / 池B 80.504，第 9 题前早死 186 / 178 局
#   2.0         池A 80.573 / 池B 80.588，早死 100 / 101 局（+0.091 / +0.083）
#   3.0         池A 80.251 / 池B 80.275，早死 71 / 75 局（-0.231 / -0.229）
# 3.0 虽然早死更少，但过度保守反而丢分，故取 2.0。
VALUE_HEALTH_FULL = 2.0

_stage_meta: Dict[int, Dict[str, Any]] = {}
_stage_meta_ready = False
_stage_entries: Dict[int, list] = {}
_stage_questions: Dict[int, int] = {}
_stage_scores: Dict[int, Tuple[int, int]] = {}
_value_cache: Dict[Tuple[int, ...], float] = {}
_value_ready = False
_value_src: Optional[int] = None


def _ensure_stage_meta() -> None:
    """从题库 `_meta.stages` 读每个阶段的题数与成败得分（只读一次）。"""
    global _stage_meta_ready
    if _stage_meta_ready:
        return
    for s in (load_data().get("_meta") or {}).get("stages") or []:
        _stage_meta[int(s["stage"])] = s
    _stage_meta_ready = True


def _ensure_value_index(events: Mapping[str, Mapping[str, Any]]) -> None:
    """按传入的 events 建「阶段 → 事件与选项 / 题数 / 分数」索引。

    用 `id(events)` 做缓存键：调用方换了题库（换文件或换字典）会自动重建，
    而正常运行时每道题都命中缓存，不会重复解析。
    """
    global _value_ready, _value_src
    key = id(events)
    if _value_ready and _value_src == key:
        return
    _ensure_stage_meta()
    _stage_entries.clear()
    _stage_questions.clear()
    _stage_scores.clear()
    _value_cache.clear()
    for st, s in _stage_meta.items():
        _stage_questions[st] = len(s.get("questions") or [])
        _stage_scores[st] = (
            int(s.get("score_success", 0)),
            int(s.get("score_fail", 0)),
        )
    for name, body in events.items():
        st = int(body.get("stage") or 0)
        _stage_entries.setdefault(st, []).append((name, get_options(body)))
    _value_ready = True
    _value_src = key


def apply_delta(attrs: Mapping[str, int], delta: Mapping[str, int]) -> Dict[str, int]:
    """返回施加属性变化后的新属性字典。"""
    out = dict(attrs)
    for k, v in (delta or {}).items():
        out[k] = int(out.get(k, 0)) + int(v)
    return out


def future_value(
    attrs: Mapping[str, int], events: Mapping[str, Mapping[str, Any]]
) -> float:
    """按当前属性估算「后面几个关键阶段的期望得分」。

    对每个事件取「4 个选项里最可能通过的那一个」，通过概率用软边距
    (sigmoid) 估计而不是 0/1 硬判定；再对该阶段题库求平均。

    为什么要软边距：过关本身是硬门槛，但**决策需要梯度**。硬判定下
    「灵巧 7 → 8」只要没跨过门槛就完全不改变价值，启发式因此看不见
    「再堆 1 点灵巧就能吃下阶段 4 的两个 12 分」这件事，于是不会集中投资。
    """
    _ensure_value_index(events)
    key = tuple(int(attrs.get(a, 0)) for a in ATTRS)
    got = _value_cache.get(key)
    if got is not None:
        return got
    if key[ATTRS.index("健康")] <= 0:
        _value_cache[key] = 0.0
        return 0.0
    total = 0.0
    for st in VALUE_STAGES:
        entries = _stage_entries.get(st) or []
        if not entries:
            continue
        win, lose = _stage_scores.get(st, (0, 0))
        acc = 0.0
        for _name, opts in entries:
            best_p = 0.0
            for o in opts:
                if o.always_fail:
                    continue
                req = o.require or {}
                if not req:
                    margin = VALUE_FREE_MARGIN
                else:
                    margin = min(
                        float(attrs.get(k, 0)) - float(v) for k, v in req.items()
                    )
                p = 1.0 / (1.0 + math.exp(-margin / VALUE_SCALE))
                if p > best_p:
                    best_p = p
            acc += win * best_p + lose * (1.0 - best_p)
        total += acc / len(entries) * _stage_questions.get(st, 1)
    if VALUE_HEALTH_FULL > 0:
        total *= min(1.0, int(attrs.get("健康", 0)) / VALUE_HEALTH_FULL)
    _value_cache[key] = total
    return total


# --------------------------------------------------------------------------- #
# 精确后向归纳：真正的最优策略
# --------------------------------------------------------------------------- #
# 上面的 future_value 是**启发式**：它只看剩余阶段的题库分布，不展开后面的分支。
# 真正的优解可以精确算出来 —— 记忆化后向归纳：
#
#     V(q, attrs) = E_{事件 e ~ 第 q 题所在阶段的题库} [ max_{选项 o}
#                     ( 本题得分(o 是否通过) + V(q+1, attrs ⊕ Δ(o)) ) ]
#
# 出题就是从阶段题库里抽，所以这个期望正是「不知道本局题序」前提下的最优值，
# 也就是 tools/adventure_upper_bound.py 算的那个上界；直接对每个选项取 argmax
# 就是最优策略。
#
# 代价（单角色，实测 tools/_report_final.py 的「建表成本」一节）：
#   把阶段 1 的 4 个事件各决策一次（比真局更狠）最坏 77 万状态 / 27 秒；
#   真局首题只抽到其中 1 个事件，所以线上首题通常 10~20 秒，
#   之后每道题都命中缓存，增量几乎为零。
#   为了不每局都重付这笔钱，表**跨局保留**，只在状态数超过
#   EXACT_RESET_THRESHOLD 时才清空（见 maybe_reset_exact_memo）——
#   同一次会话里第 2 局起基本不额外花钱。
#   （pipeline 的「生存大冒险_读题」节点因此把 timeout 放宽到 90 秒，
#   默认 20 秒在慢机器上会因为识别超时直接走 on_error 结束整局。）
# 收益（4 个全新题序池 × 400 条 × 7 个真实角色 = 11,200 对，成对比较）：
#   改造前 73.21 → 启发式 75.00 → 精确 75.88；理论上界（不知题序）76.32。
#   即 +2.67（t = +32.1），把可抢的 +3.11 里拿掉了 86%。
# 同一批数据里调参最多只值 +0.08，所以这里直接把启发式换成精确策略；
# 启发式保留为「题库元信息不完整 / 状态数异常」时的兜底。
EXACT_ENABLED = True

# 状态数硬上限。实测（tools/measure_table_warm.py）：
#   单角色单事件（= 线上首题）最坏约 38~48 万状态，10~20 秒；
#   7 个角色的并集约 112 万~150 万状态。
# 上限取 180 万，正好能**把 7 个角色的并集整表常驻**：于是每个角色只付一次建表钱，
# 之后不管随机抽到谁都命中缓存，拉表工作量为零。
# 内存代价约 150 MB（CPython 每条约 100 B），这是「不再重建」的对价。
EXACT_MAX_STATES = 1_800_000

# 到上限时在半数处裁剪，而不是把精确策略整体关掉（旧行为）。
# 归纳表是**纯缓存**：丢条目只会重算，不会改变任何判定结果。
_EXACT_MAX_TRIMS = 3  # 单次决策内最多裁几次；再多说明工作集本身就超上限，只能降级

_EXACT_BITS = 8  # 每个属性占 8 bit（不截断时上百题累积也不越界，见 _eval_options）
_EXACT_QSHIFT = _EXACT_BITS * len(ATTRS)  # 题号必须整体挪到 6 个属性字段之上

_exact_memo: Dict[int, float] = {}
_exact_src: Optional[int] = None
_exact_qstage: list = []  # 题号（0 基）→ 阶段
_exact_stages: list = []  # 参与归纳的阶段（按题号顺序去重）
_exact_compiled: Dict[int, list] = {}  # 阶段 → 该阶段每个事件的编译选项表
_exact_event_opts: Dict[str, list] = {}  # 事件名 → 编译后的选项表
_exact_event_stage: Dict[str, int] = {}  # 事件名 → 所属阶段（用于反推题号）
_exact_stage_qs: Dict[int, list] = {}  # 阶段 → 该阶段的题号列表（0 基）
_exact_cap: list = []  # 属性截断上限（与参考实现 adventure_upper_bound.CAP 同步）
_exact_ai: Dict[str, int] = {}
_exact_hidx = ATTRS.index("健康")
_exact_states = 0
_exact_trims = 0
_exact_off_reason: Optional[str] = None


class _ExactOverflow(Exception):
    """状态数超过上限（正常不可达，纯防呆）。"""


def _exact_disable(reason: str) -> None:
    global EXACT_ENABLED, _exact_off_reason
    EXACT_ENABLED = False
    _exact_off_reason = reason
    _exact_memo.clear()


def reset_exact_memo() -> None:
    """强制清空记忆化表（测试与调试用）。"""
    global _exact_states, _exact_trims
    _exact_memo.clear()
    _exact_states = 0
    _exact_trims = 0


def _trim_exact_memo() -> None:
    """缓存到上限时丢掉最早插入的一半。

    表是纯缓存，丢条目只会让后续重算，**不会改变任何判定结果** ——
    所以这里选择「重算」而不是旧实现的「把精确策略永久关掉」
    （旧行为代价 1.05 分/局，而且是静默的）。
    """
    global _exact_states, _exact_trims
    n = len(_exact_memo)
    if not n:
        return
    for k in list(_exact_memo)[: n // 2]:
        del _exact_memo[k]
    _exact_states = len(_exact_memo)
    _exact_trims += 1


def maybe_reset_exact_memo() -> bool:
    """新的一局开始时调用；现在**不再按局清空**，永远返回 False。

    旧实现按 EXACT_RESET_THRESHOLD=40 万清表，而单个角色就要 38~48 万状态，
    结果是**每一局都在重建**（日志里每局都是「拉表耗时 10~16s」）。
    现在改成整表常驻 + 到上限时半数裁剪，7 个角色各付一次即可。
    函数名保留是为了不打断既有调用方。
    """
    return False


def exact_stats() -> Tuple[int, int]:
    """返回 (记忆化状态数, 已展开节点数)，供日志使用。"""
    return len(_exact_memo), _exact_states


def question_for_event(event: str, answered: Dict[int, list]) -> Optional[int]:
    """由「这一题是什么事件」反推它是本局第几题（0 基），推不出返回 None。

    题号**不能**用「决策次数」推。点击有时落不到新题上（截图还是上一题），
    同一题于是被再答一次，决策次数就比真实进度多；一旦多算，
    之后每一题的价值评估都会偏后一个阶段（实测第 1 题后多一次就掉 2.0 分）。
    而事件名唯一确定了阶段，同一阶段里的题号是连续的、事件不会重复抽到，
    所以「阶段 + 该阶段内已答过几个不同事件」就是可靠题号。

    answered 由调用方（agent 的局内状态）持有，形如 {阶段: [事件名, ...]}；
    本函数会就地把新事件登记进去。事件已在列表里 = 同一题被重复读取，
    返回同一题号、不推进。
    """
    st = _exact_event_stage.get(event)
    if st is None:
        return None
    qs = _exact_stage_qs.get(st) or []
    if not qs:
        return None
    seen = answered.setdefault(int(st), [])
    if event in seen:
        idx = seen.index(event)
    else:
        idx = len(seen)
        seen.append(event)
    return qs[min(idx, len(qs) - 1)]


def _exact_pairs(delta: Optional[Mapping[str, int]]) -> tuple:
    """把属性增减字典编译成 ((属性下标, 整数增量), ...)，省掉归纳内层的字典开销。"""
    if not delta:
        return ()
    out = []
    for k, v in delta.items():
        i = _exact_ai.get(k)
        if i is None or not v:
            continue
        out.append((i, int(v)))
    return tuple(out)


def _build_exact_tables(events: Mapping[str, Mapping[str, Any]]) -> bool:
    """按题库建「题号→阶段、阶段→编译后的选项」索引（只建一次）。"""
    global _exact_src, _exact_qstage, _exact_stages, _exact_compiled, _exact_cap, _exact_ai
    global _exact_event_opts
    global _exact_event_stage, _exact_stage_qs
    if not EXACT_ENABLED:
        return False
    key = id(events)
    if _exact_src == key:
        return True
    _ensure_value_index(events)
    _ensure_stage_meta()
    _exact_ai = {a: i for i, a in enumerate(ATTRS)}

    # 题号 → 阶段：题库 `_meta.stages[].questions` 直接给出了题号列表（1 基）
    qstage: list = []
    for st in sorted(_stage_meta):
        for num in _stage_meta[st].get("questions") or []:
            n = int(num) - 1
            while len(qstage) <= n:
                qstage.append(None)
            qstage[n] = st
    if not qstage or any(s is None for s in qstage):
        _exact_disable("题库 _meta.stages 的题号不完整")
        return False

    compiled: Dict[int, list] = {}
    event_opts: Dict[str, list] = {}
    for st, entries in _stage_entries.items():
        pool = []
        for name, opts in entries:
            one = [
                (
                    o.index,
                    _exact_pairs(o.require),
                    _exact_pairs(o.success),
                    _exact_pairs(o.fail),
                    bool(o.always_fail),
                )
                for o in opts
            ]
            pool.append(one)
            event_opts[name] = one
        if pool:
            compiled[st] = pool
    if not compiled:
        _exact_disable("题库里没有可归纳的事件")
        return False

    # 属性截断上限：超过「该属性在题库里的最高门槛」后不再改变任何判定，
    # 因此可以截断——这是把状态数从 65 万压到 53 万的关键。健康不截断
    # （它没有门槛意义上的天花板，且负增益会一路累积）。
    #
    # 注意这与 `tools/adventure_upper_bound.py` 的 `add()` 必须完全一致：
    # 生产实现的每一步都要和量出收益的那份参考实现逐决策对齐，否则
    # 「调参得到的 +0.xx」根本不能保证会在线下复现。曾经漏掉这层截断，
    # 结果同一局面算出 78.88 而参考是 78.83，299 个决策里 136 个对不上。
    cap = []
    for a in ATTRS:
        mx = 0
        for pool in compiled.values():
            for _i, req, _s, _f, _af in (o for ev in pool for o in ev):
                for i, v in req:
                    if ATTRS[i] == a:
                        mx = max(mx, v)
        cap.append(mx if a != "健康" else 99)

    _exact_qstage = qstage
    _exact_stages = sorted({s for s in qstage})
    _exact_compiled = compiled
    _exact_event_opts = event_opts
    _exact_stage_qs = {st: [n for n, s in enumerate(qstage) if s == st] for st in set(qstage)}
    _exact_event_stage = {
        name: st for st, entries in _stage_entries.items() for name, _opts in entries
    }
    _exact_cap = cap
    _exact_src = key
    reset_exact_memo()
    return True


def _pack(attrs: Tuple[int, ...]) -> int:
    v = 0
    for i, x in enumerate(attrs):
        v |= (int(x) & ((1 << _EXACT_BITS) - 1)) << (_EXACT_BITS * i)
    return v


def _eval_options(
    opts: list, attrs: Tuple[int, ...], q: int, win: int, lose: int
) -> Tuple[Optional[int], float]:
    """在一组（编译后的）选项里按精确期望取最优，返回 (选项下标, 期望值)。"""
    best_i, best_v = None, -1e18
    for idx, req, succ, fail, af in opts:
        ok = not af
        if ok:
            for i, v in req:
                if attrs[i] < v:
                    ok = False
                    break
        pairs = succ if ok else fail
        if pairs:
            nxt = list(attrs)
            for i, v in pairs:
                nxt[i] = min(nxt[i] + v, _exact_cap[i])
            nxt = tuple(nxt)
        else:
            nxt = attrs
        val = (win if ok else lose) + _exact_value(q + 1, nxt)
        if val > best_v:
            best_v, best_i = val, idx
    return best_i, (best_v if best_i is not None else 0.0)


def _exact_value(q: int, attrs: Tuple[int, ...]) -> float:
    """从第 q 题（0 基）起、属性为 attrs 时的最优期望剩余得分。"""
    global _exact_states
    if q >= len(_exact_qstage) or attrs[_exact_hidx] <= 0:
        return 0.0
    key = (q << _EXACT_QSHIFT) | _pack(attrs)
    got = _exact_memo.get(key)
    if got is not None:
        return got
    if _exact_states >= EXACT_MAX_STATES:
        if _exact_trims >= _EXACT_MAX_TRIMS:
            raise _ExactOverflow()
        _trim_exact_memo()
    _exact_states += 1
    st = _exact_qstage[q]
    win, lose = _stage_scores.get(st, (0, 0))
    pools = _exact_compiled[st]
    tot = 0.0
    for opts in pools:
        tot += _eval_options(opts, attrs, q, win, lose)[1]
    res = tot / len(pools)
    _exact_memo[key] = res
    return res


def exact_choice(
    events: Mapping[str, Mapping[str, Any]],
    event_name: str,
    attrs: Mapping[str, int],
    question: int,
) -> Optional[Tuple[int, float]]:
    """精确最优选项，返回 (选项下标, 期望剩余得分)；不可用时返回 None。"""
    global _exact_trims
    if not _build_exact_tables(events):
        return None
    if not (0 <= question < len(_exact_qstage)):
        return None
    opts = _exact_event_opts.get(event_name)
    if not opts:
        return None
    st = _exact_qstage[question]
    win, lose = _stage_scores.get(st, (0, 0))
    t = tuple(int(attrs.get(a, 0)) for a in ATTRS)
    _exact_trims = 0  # 单次决策内的裁剪次数预算（见 _trim_exact_memo）
    try:
        return _eval_options(opts, t, question, win, lose)
    except _ExactOverflow:
        _exact_disable("状态数超过上限")
        return None


def _attr_weight(key: str, value: int, attrs: Mapping[str, int]) -> float:
    """给单个属性的变化量定权重（阈值导向，正负对称）。

    规则：
      - **增加**：只对「还没达标」的属性有价值，越接近门槛越值钱；已达标则价值很低
      - **减少**：看变化后是否还达标——跌破门槛就按缺口重罚，仍高于门槛则损失很小

    为什么要分正负两套判断：早先的实现对负收益一律按 1.0 计权，导致
    「+1 健康 / -1 战力」这类选项被严重低估损失——战力已经堆到 9（门槛 10）时，
    减 1 点只扣 1 分，而加 1 健康却按 3~6 分算，于是会做出牺牲战力的选择。
    """
    target = THRESHOLD_TARGETS.get(key, 8)
    cur = int(attrs.get(key, 0))
    after = cur + value

    # 健康特殊处理：它不只是门槛（最高只到 6），更是**生死线**——归零会让本轮提前结束。
    # 所以扣血要按「扣完还剩多少」加权，剩余越少罚得越狠。
    # 不能沿用「距门槛的差距」：那样剩 2 点血(gap=4)会比剩 3 点血(gap=3)罚得更轻，恰好反了。
    if key == "健康" and value < 0:
        if after <= 0:
            # 归零：权重最高（另有 ZERO_PENALTY 兜底，这里保证「剩余越少罚越重」的单调性）
            return HEALTH_CRITICAL_WEIGHT * 2
        if after <= 2:
            return HEALTH_CRITICAL_WEIGHT  # 剩 1~2 点：随时暴毙
        if after <= 3:
            return NEAR_WEIGHT  # 剩 3 点：危险
        # 剩余充足，继续走下面的门槛逻辑

    if value > 0:
        if cur >= target:
            return FAR_WEIGHT  # 已达标，继续堆收益递减
        gap = target - cur
    else:
        if after >= target:
            return FAR_WEIGHT  # 减了仍然达标，损失很小
        gap = target - after  # 减少后形成的缺口

    if gap <= NEAR_GAP:
        return NEAR_WEIGHT
    if gap <= MID_GAP:
        return MID_WEIGHT
    return FAR_WEIGHT


def _gain_score(
    gains: Mapping[str, int],
    attrs: Mapping[str, int],
    weights: Mapping[str, float],
) -> float:
    """按「阈值导向」给属性收益打分（正负对称）。

    依据：阶段的成败是**硬门槛**（属性 >= 要求即成功），而门槛集中在战力/灵巧/魅力
    8~10。实测模拟（各有 4000 局）表明：
      - 每题的收益摊平到六个属性 → 没有一项能长到 8~10 → 阶段4/5 失败率 62%/70%
      - 优先补「离门槛最近」的属性 → 阶段5 成功率 30%→38%，平均分 79.5→82.3
    因此：越接近门槛的属性收益，价值越高（差一点就补上）；
    反过来，**把接近门槛的属性减下去，代价同样高**。
    """
    score = 0.0
    for k, v in gains.items():
        v = int(v)
        if k == "健康" and int(attrs.get("健康", 0)) + v <= 0:
            score -= ZERO_PENALTY  # 健康归零直接判死，权重拉满
        score += float(v) * _attr_weight(k, v, attrs)
    return score


def evaluate(
    opt: Option,
    attrs: Mapping[str, int],
    weights: Mapping[str, float] = DEFAULT_WEIGHTS,
    success_bonus: float = DEFAULT_SUCCESS_BONUS,
) -> Tuple[bool, float, Dict[str, int]]:
    ok = can_succeed(opt, attrs)
    gains = dict(opt.success if ok else opt.fail)
    score = _gain_score(gains, attrs, weights)
    if ok:
        score += success_bonus
    return ok, score, gains


DEFAULT_OPTION_MATCH_THRESHOLD = 0.55


def _lcs_length(a: str, b: str) -> int:
    """最长公共子序列长度（动态规划 + 滚动数组）。"""
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b, 1):
            if ca == cb:
                cur.append(prev[j - 1] + 1)
            else:
                cur.append(max(prev[j], cur[-1]))
        prev = cur
    return prev[-1]


def text_similarity(lib_text: str, ocr_text: str) -> float:
    """题库里是选项简称、界面上是完整句，衡量两者有多像。

    以**题库简称的长度**为基准计算最长公共子序列占比：
    界面文案常比简称长得多（如「偷走坐骑」vs「偷偷靠近偷走圣诞老公公的坐骑」），
    直接用序列相似度会被长度差稀释，LCS 占比更能反映"简称是否出现在界面里"。
    """
    a = (lib_text or "").strip()
    b = (ocr_text or "").strip()
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    return _lcs_length(a, b) / len(a)


def match_event_by_options(
    events: Mapping[str, Mapping[str, Any]],
    option_texts: Sequence[str],
    threshold: float = DEFAULT_OPTION_MATCH_THRESHOLD,
) -> Optional[str]:
    """按 4 行选项文本反查事件。

    界面选项顺序与题库顺序一致（已用「危险任务」验证），故逐行配对求平均相似度，
    取最高分的事件；低于阈值则返回 None（交给采集分支）。
    """
    if not option_texts or not any(option_texts):
        return None
    scored = []
    for name, body in events.items():
        lib_options = body.get("options") or []
        if len(lib_options) != len(option_texts):
            continue
        total = sum(
            text_similarity(opt.get("text", ""), ocr)
            for opt, ocr in zip(lib_options, option_texts)
        )
        scored.append((total / len(option_texts), name))
    if not scored:
        return None
    best_score, best_name = max(scored)
    return best_name if best_score >= threshold else None


def choose(
    events: Mapping[str, Mapping[str, Any]],
    event_name: str,
    attrs: Mapping[str, int],
    weights: Mapping[str, float] = DEFAULT_WEIGHTS,
    success_bonus: float = DEFAULT_SUCCESS_BONUS,
    question: Optional[int] = None,
) -> Decision:
    """返回该事件的最优选项。

    `question` 是本题的题号（0 基）。传了就启用精确后向归纳（EXACT_ENABLED 为真
    且题库元信息完整时），不传则只用启发式 —— 这样既保持旧调用方式的行为不变，
    也让单元测试可以单独验证启发式那条路径。
    """
    if event_name not in events:
        raise KeyError(f"题库中没有事件「{event_name}」")
    opts = get_options(events[event_name])
    if not opts:
        raise ValueError(f"事件「{event_name}」没有任何选项")

    scored = []
    for opt in opts:
        ok, score, gains = evaluate(opt, attrs, weights, success_bonus)
        nxt = apply_delta(attrs, gains)
        # 排序主键：选完之后「带着新属性去打后面关键阶段的期望得分」。
        # 原评分只作次级项，用于近似平局时细化（实测 0.5 最优，2.0/8.0 差 0.1~0.3）。
        value = future_value(nxt, events) + VALUE_BLEND * score
        # 末位平局键：前面全相同时，留更多血更安全（血是唯一能让本局提前结束的资源）
        scored.append((opt, ok, score, gains, value, int(nxt.get("健康", 0))))

    # 排序键：先「能否成功」（拿阶段大分的前提），再「后续期望得分」，
    # 再原评分，最后剩余健康
    best = max(scored, key=lambda x: (x[1], x[4], x[2], x[5]))

    # 精确策略：用后向归纳的期望直接取 argmax（见本文件「精确后向归纳」一节）。
    # 它比启发式多 1.05 分，是线上默认路径；失败/不可用时安静地退回启发式。
    exact_note = ""
    if question is not None:
        picked = exact_choice(events, event_name, attrs, int(question))
        if picked is not None and picked[0] is not None:
            for cand in scored:
                if cand[0].index == picked[0]:
                    best = cand
                    exact_note = (
                        f"精确后向归纳（第 {int(question) + 1} 题，"
                        f"期望 {picked[1]:+.2f}，已展开 {exact_stats()[0]:,} 状态）"
                    )
                    break

    opt, ok, score, gains, value, _hp_after = best
    if exact_note:
        value = picked[1]

    if ok:
        reason = "满足门槛，必定成功" if opt.require else "无要求，必定成功"
        if any(v < 0 for v in gains.values()):
            reason += "；成功收益含负项，但在可成功项中评分最高"
    else:
        short = []
        for k, v in (opt.require or {}).items():
            short.append(f"{k}需{v}（当前{attrs.get(k, 0)}）")
        reason = "所有选项都不满足门槛，此为该情况下损失最小项：" + "、".join(short)
    if exact_note:
        reason = exact_note + "｜" + reason

    return Decision(
        event=event_name,
        index=opt.index,
        text=opt.text,
        will_succeed=ok,
        gains=gains,
        score=score,
        reason=reason,
        value=value,
        all_scores=tuple(
            (o.index, o.text, ok_, sc) for o, ok_, sc, _, _, _ in scored
        ),
        exact=bool(exact_note),
    )


if __name__ == "__main__":  # 自测：不依赖 maa，可直接 `py agent/adventure_logic.py`
    events = load_events()
    print(f"题库事件数：{len(events)}")
    total_opts = sum(len(e["options"]) for e in events.values())
    print(f"选项总数：{total_opts}")
    uncertain = [
        (name, o["text"])
        for name, e in events.items()
        for o in e["options"]
        if o.get("_uncertain")
    ]
    print(f"待核对条目：{len(uncertain)} -> {uncertain}")
    print()

    scenarios = [
        ("初始属性（各 3）", dict.fromkeys(ATTRS, 3)),
        ("均衡成长（各 6）", dict.fromkeys(ATTRS, 6)),
        ("高战力（战力10 其余5）", {**dict.fromkeys(ATTRS, 5), "战力": 10}),
        ("残血（健康1 其余8）", {**dict.fromkeys(ATTRS, 8), "健康": 1}),
    ]
    for label, attrs in scenarios:
        print(f"=== {label}：{attrs} ===")
        for name in ("危险任务", "烤机外挂", "螺旋地狱", "金之祖巫", "木之祖巫"):
            d = choose(events, name, attrs)
            print("  " + d.describe())
        print()

    print("=== 全事件决策（属性各 6，检查是否有异常）===")
    attrs = dict.fromkeys(ATTRS, 6)
    for name in events:
        d = choose(events, name, attrs)
        flag = " ⚠待核对" if any(o["text"] == d.text and o.get("_uncertain") for o in events[name]["options"]) else ""
        print(f"  {d.describe()}{flag}")
