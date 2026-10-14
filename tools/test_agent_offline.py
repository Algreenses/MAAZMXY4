"""离线验证 Agent 逻辑：用 stub 顶替 maa 包，跑通 analyze 的完整决策链路。

不需要安装 MaaFw，也不需要游戏——只要能 import 本文件所在的 agent 目录即可。
验证点：题干识别 → 选项文本反查事件 → 属性读取 → 决策 → 返回目标选项行的 box。
"""

import json
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_DIR = os.path.join(ROOT, "agent")
UI_PATH = os.path.join(ROOT, "assets", "data", "adventure_ui.json")

ok = True


def check(label, passed, extra=""):
    global ok
    if not passed:
        ok = False
    print(f"{'OK  ' if passed else 'FAIL'} {label}{(' -> ' + str(extra)) if extra else ''}")


# --------------------------------------------------------------------------- #
# 1) 用 stub 顶替 maa 包（adventure.py 依赖它，但逻辑本身与框架无关）
# --------------------------------------------------------------------------- #
class _AgentServer:
    @staticmethod
    def custom_recognition(name):
        def deco(cls):
            cls._registered_name = name
            return cls

        return deco

    @staticmethod
    def custom_action(name):
        def deco(cls):
            cls._registered_name = name
            return cls

        return deco


class _CustomAction:
    class RunArg:
        pass


class _CustomRecognition:
    class AnalyzeArg:
        pass

    class AnalyzeResult:
        def __init__(self, box=None, detail=None):
            self.box = box
            self.detail = detail


def _mk(name):
    return types.ModuleType(name)


maa = _mk("maa")
maa_agent = _mk("maa.agent")
maa_agent_server = _mk("maa.agent.agent_server")
maa_agent_server.AgentServer = _AgentServer
maa_context = _mk("maa.context")
maa_context.Context = type("Context", (), {})
maa_ca = _mk("maa.custom_action")
maa_ca.CustomAction = _CustomAction
maa_cr = _mk("maa.custom_recognition")
maa_cr.CustomRecognition = _CustomRecognition

sys.modules.update(
    {
        "maa": maa,
        "maa.agent": maa_agent,
        "maa.agent.agent_server": maa_agent_server,
        "maa.context": maa_context,
        "maa.custom_action": maa_ca,
        "maa.custom_recognition": maa_cr,
    }
)

sys.path.insert(0, AGENT_DIR)
import adventure  # noqa: E402

# 把日志与采集输出重定向到临时目录，避免污染真实运行日志（debug/adventure/）
_TMP = os.environ.get("TEMP", ".")
adventure.LOG_PATH = os.path.join(_TMP, "adventure_offline_test.log")
adventure.COLLECTED_PATH = os.path.join(_TMP, "adventure_offline_test_collected.jsonl")

# --------------------------------------------------------------------------- #
# 2) 假的识别结果与 context（按节点名分派文本）
# --------------------------------------------------------------------------- #
class FakeResult:
    def __init__(self, text):
        self.text = text
        self.box = (0, 0, 1, 1)
        self.score = 0.99


class FakeDetail:
    def __init__(self, texts):
        self.hit = bool(texts)
        self.box = (0, 0, 1, 1)
        self.filtered_results = [FakeResult(t) for t in texts]
        self.all_results = self.filtered_results
        self.best_result = self.filtered_results[0] if self.filtered_results else None


class FakeContext:
    def __init__(self, mapping):
        self.mapping = mapping

    def run_recognition(self, node, image, pipeline_override=None):
        return FakeDetail(self.mapping.get(node, []))


class Argv:
    image = object()  # 非 None 即可，OCR 由 FakeContext 顶替


UI = json.load(open(UI_PATH, encoding="utf-8"))
BOXES = [tuple(b) for b in UI["layout"]["option_boxes"]]
ATTRS = ("健康", "战力", "智慧", "魅力", "运气", "灵巧")

DANGER_QUESTION = "在你准备离开村子去冒险时，族长想交给你一个很危险的任务，并许诺完成任务后给你奖励，你会_"
DANGER_OPTIONS = ["坚定不移的接受任务", "我拒绝接受任务，自己出去冒险", "默默不做声", "推荐你的仇敌"]


def build_mapping(question, options, attrs):
    mapping = {adventure.QUESTION_NODE: [question]}
    for i, text in enumerate(options, start=1):
        mapping[adventure.OPTION_NODE_FMT.format(i)] = [text]
    for name in ATTRS:
        mapping[adventure.ATTR_NODE_FMT.format(name)] = [str(attrs[name])]
    return mapping


def run(question, options, attrs, initial=None):
    adventure.STATE.attrs = dict(initial if initial is not None else attrs)
    ctx = FakeContext(build_mapping(question, options, attrs))
    return adventure.AdventureRead().analyze(ctx, Argv())


print("=== 场景 1：危险任务，战力 4（满足「接受」门槛 战力4）===")
# 本场景只校验「识别 → 决策 → 返回正确 box」这条链路。
# 不锁定具体选项：战力4 时「接受」(战力+1) 与「推荐叛徒」(智慧+1) 都能成功、
# 收益等价（都是距目标 6 点的 +1），由价值函数择一，选哪个都不构成正确性问题。
attrs = {"健康": 3, "战力": 4, "智慧": 4, "魅力": 5, "运气": 3, "灵巧": 6}
res = run(DANGER_QUESTION, DANGER_OPTIONS, attrs)
check("命中并返回结果", res is not None)
if res:
    check("识别为「危险任务」", res.detail.get("event") == "危险任务", res.detail.get("event"))
    check("选中一个必定成功的选项", res.detail.get("will_succeed") is True,
          f"选了「{res.detail.get('text')}」")
    _opt = res.detail.get("option")
    check("box 与选中项对应", tuple(res.box) == BOXES[_opt - 1],
          f"{res.box} vs {BOXES[_opt - 1]}")
    print(f"     选中第 {_opt} 项「{res.detail.get('text')}」")
    print(f"     决策理由：{res.detail.get('reason')}")

print("\n=== 场景 2：危险任务，战力 3（四项全不满足门槛）===")
attrs_low = {"健康": 3, "战力": 3, "智慧": 3, "魅力": 3, "运气": 3, "灵巧": 6}
res2 = run(DANGER_QUESTION, DANGER_OPTIONS, attrs_low)
check("命中并返回结果", res2 is not None)
if res2:
    check("仍在「危险任务」内决策", res2.detail.get("event") == "危险任务")
    check("box 是 4 行之一", tuple(res2.box) in BOXES, res2.box)
    check("判定为必然失败", res2.detail.get("will_succeed") is False)
    print(f"     选中第 {res2.detail.get('option')} 项，理由：{res2.detail.get('reason')}")

print("\n=== 场景 3：题干区无文字（已离开答题界面）===")
res3 = run("", DANGER_OPTIONS, attrs)
check("返回 None 表示未命中", res3 is None, res3)

print("\n=== 场景 4：未知事件（题干与选项都不匹配题库）===")
before = 0
if os.path.exists(adventure.COLLECTED_PATH):
    before = sum(1 for _ in open(adventure.COLLECTED_PATH, encoding="utf-8"))
res4 = run("这是一段题库里没有的题干内容_", ["选项甲", "选项乙", "选项丙", "选项丁"], attrs)
check("未知事件返回 None（为安全不点击）", res4 is None, res4)
after = 0
if os.path.exists(adventure.COLLECTED_PATH):
    after = sum(1 for _ in open(adventure.COLLECTED_PATH, encoding="utf-8"))
check("仍写入采集记录 collected.jsonl", after > before, f"{before} -> {after}")

print("\n=== 场景 5：属性 OCR 失败时回退到内部账本 ===")
adventure.STATE.attrs = {"健康": 9, "战力": 9, "智慧": 9, "魅力": 9, "运气": 9, "灵巧": 9}
ctx = FakeContext({adventure.QUESTION_NODE: [DANGER_QUESTION],
                   **{adventure.OPTION_NODE_FMT.format(i): [t] for i, t in enumerate(DANGER_OPTIONS, 1)}})
res5 = adventure.AdventureRead().analyze(ctx, Argv())
check("属性读不到仍能决策", res5 is not None)
if res5:
    check("用账本属性判定为成功", res5.detail.get("will_succeed") is True, res5.detail.get("reason"))

print("\n=== 场景 6：自适应题干定位（用模拟 720p 图的真实像素）===")
RAW_PATH = os.path.join(ROOT, "debug", "_sim_720p.raw")
if not os.path.exists(RAW_PATH):
    print("  跳过：缺少 debug/_sim_720p.raw")
else:
    raw = open(RAW_PATH, "rb").read()
    _w, _h = 1147, 720
    _stride = len(raw) // _h

    class _Row:
        __slots__ = ("data", "base", "w")

        def __init__(self, data, base, w):
            self.data = data
            self.base = base
            self.w = w

        def __len__(self):
            return self.w

        def __getitem__(self, x):
            i = self.base + x * 3
            return (self.data[i], self.data[i + 1], self.data[i + 2])

    class RawImage:
        __slots__ = ("data", "stride", "w", "h")

        def __init__(self, data, stride, w, h):
            self.data = data
            self.stride = stride
            self.w = w
            self.h = h

        def __len__(self):
            return self.h

        def __getitem__(self, y):
            return _Row(self.data, y * self.stride, self.w)

    img = RawImage(raw, _stride, _w, _h)
    roi = adventure._locate_question(img)
    check("定位到题干区域", roi is not None, roi)
    if roi:
        x, y, w2, h2 = roi
        print(f"     自适应定位结果 {roi}；pipeline 里写死的是 [335, 232, 680, 66]")
        check("y 落在题干行（200~285）", 200 <= y <= 285, y)
        check("x 落在题干列（300~390）", 300 <= x <= 390, x)
        check("宽度合理（600~790）", 600 <= w2 <= 790, w2)
        check("高度合理（40~130）", 40 <= h2 <= 130, h2)
        check("未把顶部标题误判成题干（y > 150）", y > 150, y)

    print("\n=== 场景 7：自适应选项行定位（同样用真实像素）===")
    opt_boxes = adventure._locate_options(img)
    check("定位到 4 个选项行", bool(opt_boxes) and len(opt_boxes) == 4, opt_boxes)
    if opt_boxes and len(opt_boxes) == 4:
        fixed = UI["layout"]["option_boxes"]
        for i, (got, exp) in enumerate(zip(opt_boxes, fixed), start=1):
            got_cy = got[1] + got[3] // 2
            exp_cy = exp[1] + exp[3] // 2
            check(f"选项{i} 行中心与固定值相差 ≤12px", abs(got_cy - exp_cy) <= 12, f"自适应={got_cy} 固定={exp_cy}")
        print(f"     自适应: {opt_boxes}")
        print(f"     固定值: {fixed}")

    print("\n=== 场景 8：自适应属性行定位 ===")
    attr_map = adventure._locate_attrs(img)
    check("定位到 6 个属性行", bool(attr_map) and len(attr_map) == 6, list(attr_map) if attr_map else None)
    if attr_map and len(attr_map) == 6:
        fixed_attrs = UI["layout"]["attr_rows"]
        for name in ("健康", "战力", "智慧", "魅力", "运气", "灵巧"):
            got = attr_map[name]
            exp = fixed_attrs[name]
            got_cy = got[1] + got[3] // 2
            exp_cy = exp[1] + exp[3] // 2
            check(f"属性「{name}」行中心相差 ≤12px", abs(got_cy - exp_cy) <= 12, f"自适应={got_cy} 固定={exp_cy}")
        print(f"     自适应: {attr_map}")

print("\n=== 场景 9：逐行 OCR 全失败时的整块兜底（且账本为空）===")
adventure.STATE.attrs = {}
bulk_mapping = {adventure.QUESTION_NODE: [DANGER_QUESTION]}
for i, t in enumerate(DANGER_OPTIONS, start=1):
    bulk_mapping[adventure.OPTION_NODE_FMT.format(i)] = [t]
# 健康 战力 智慧 魅力 运气 灵巧（界面自上而下）
bulk_mapping[adventure.BULK_ATTR_NODE] = ["3", "3", "4", "5", "3", "6"]
res9 = adventure.AdventureRead().analyze(FakeContext(bulk_mapping), Argv())
check("整块兜底仍能完成决策", res9 is not None)
if res9:
    check("识别为「危险任务」", res9.detail.get("event") == "危险任务", res9.detail.get("event"))
    check("属性确实来自整块 OCR", isinstance(res9.detail.get("will_succeed"), bool), res9.detail)
    print(f"     选中第 {res9.detail.get('option')} 项，理由：{res9.detail.get('reason')}")

print("\n=== 场景 10：属性完全读不到 + 账本为空（保守决策）===")
adventure.STATE.attrs = {}
res10 = adventure.AdventureRead().analyze(
    FakeContext({adventure.QUESTION_NODE: [DANGER_QUESTION],
                 **{adventure.OPTION_NODE_FMT.format(i): [t] for i, t in enumerate(DANGER_OPTIONS, 1)}}),
    Argv(),
)
check("无属性信息时仍返回结果（不崩）", res10 is not None)
if res10:
    check("判定为必然失败（保守）", res10.detail.get("will_succeed") is False, res10.detail)
    print(f"     选中第 {res10.detail.get('option')} 项，理由：{res10.detail.get('reason')}")

print("\n=== 场景 11：不在答题界面时绝不点击（防止乱点扣属性）===")
# 这串文字来自真实日志：当时界面是某个挑战/积分面板，不是答题界面
bad_question = "挑战不满意，可花费一定点券"
bad_options = ["挑战不满意，可花费一定点券", "", "本周生友积分", ""]
res11 = adventure.AdventureRead().analyze(
    FakeContext(build_mapping(bad_question, bad_options, attrs)), Argv()
)
check("非答题界面返回 None（不点击）", res11 is None, res11)

# 题干是答题界面的（含「你会」）时，即使选项没读全也应当继续——避免误伤正常题目
res12 = adventure.AdventureRead().analyze(
    FakeContext(build_mapping(DANGER_QUESTION, ["坚定不移的接受任务", "", "", ""], attrs)), Argv()
)
check("题干达标时即使选项不全也继续决策", res12 is not None, res12)

# 正常答题界面仍然照常决策
res13 = adventure.AdventureRead().analyze(
    FakeContext(build_mapping(DANGER_QUESTION, DANGER_OPTIONS, attrs)), Argv()
)
check("正常答题界面仍能决策", res13 is not None and res13.detail.get("event") == "危险任务")

print("\n=== 场景 12：战力接近门槛时，不该为了加健康而牺牲战力 ===")
# 登山黑影第2项「给他一拳」：成功收益 健康+1 / 战力-1（该事件门槛是战力6）
# 战力贴近上限门槛(10)时减 1 会失守，必须避开 —— 这是本场景要守住的性质。
#
# 注：战力富余(12/15)时本策略**也不换健康**，因为软边距 sigmoid 不饱和，
# 门槛之上的属性仍有边际价值。这一取舍是实测定的：给 margin 加上限
# （cap 0~3 × scale 0.7/1.0 × 2 个独立题序池，共 12 组）全部变差
# 0.01~0.13 分，因此保留不饱和版本。
_events = adventure.logic.load_events()
for _power in (9, 10):
    _attrs = {"健康": 4, "战力": _power, "智慧": 5, "魅力": 5, "运气": 5, "灵巧": 5}
    _d = adventure.logic.choose(_events, "登山黑影", _attrs)
    check(f"战力{_power} 时不牺牲战力（避开第2项）", _d.index != 1, f"选了「{_d.text}」")
    for _i, _t, _ok2, _sc in _d.all_scores:
        print(f"       第{_i}项 {_t:<6} 可成功={str(_ok2):<5} 评分={_sc:+7.2f}")

print("\n=== 场景 13：减少接近门槛的属性必须被重罚（正负对称）===")
_base = {"健康": 4, "战力": 9, "智慧": 5, "魅力": 5, "运气": 5, "灵巧": 5}
_w9 = adventure.logic._attr_weight("战力", -1, _base)
_w12 = adventure.logic._attr_weight("战力", -1, {"健康": 4, "战力": 12})
_wup = adventure.logic._attr_weight("战力", 1, _base)
check("战力9 减1（会跌破门槛10）按高权重计", _w9 > _w12, f"战力9时={_w9} 战力12时={_w12}")
check("战力12 减1（仍达标）按低权重计", _w12 < _wup, f"战力12减1={_w12} 战力9加1={_wup}")

print("\n=== 场景 14：健康扣血权重必须随剩余血量单调递增 ===")
# 早先的缺陷：扣血用「距门槛的差距」计权，导致剩 2 点血(gap=4)比剩 3 点(gap=3)罚得更轻，恰好反了
_prev, _mono, _wseq = None, True, []
for _hp in (12, 8, 6, 5, 4, 3, 2, 1):
    _w = adventure.logic._attr_weight("健康", -1, {"健康": _hp})
    _wseq.append(f"血{_hp}={_w:g}")
    if _prev is not None and _w < _prev:
        _mono = False
    _prev = _w
check("扣血代价随剩余血量减少而单调增加", _mono, " ".join(_wseq))
check("剩 1 点血的代价高于剩 4 点",
      adventure.logic._attr_weight("健康", -1, {"健康": 2})
      > adventure.logic._attr_weight("健康", -1, {"健康": 5}))
check("血量充足（≥7）扣 1 点代价很低",
      adventure.logic._attr_weight("健康", -1, {"健康": 9}) <= 1.0)

print("\n=== 场景 15：精确策略在血量贴近门槛时优先补血 ===")
# 冒险前神秘礼物 4 个选项都能成功，其中「绝世秘籍」是 健康+1/战力+1。
# 线上主路径是精确后向归纳，所以这里断言精确策略的行为。
_a2 = {"健康": 2, "战力": 4, "智慧": 4, "魅力": 4, "运气": 4, "灵巧": 4}
_d = adventure.logic.choose(_events, "冒险前神秘礼物", _a2, question=0)
check("健康 2 时选到补血项「绝世秘籍」", _d.index == 1, f"选了「{_d.text}」")
check("该决策确实走精确后向归纳", _d.exact is True)

print("\n=== 场景 16：健康目标取题库常见门槛，而不是最高门槛 ===")
# 健康门槛最高只到 6，且仅出现 3 次（战力/灵巧/魅力/智慧都有 33~37 处、最高 10）。
# 目标设 6 会让健康在 3~5 区间每 +1 都按 NEAR 档计权，压过堆战力/智慧/灵巧的收益。
# 实测（tools/simulate_adventure.py，3 个种子 × 1500 局）：
#   目标 2/3 → 80.23，目标 4 → 79.31，目标 5 → 79.01，目标 6 → 78.97
check("健康目标 = 3（题库常见门槛）", adventure.logic.THRESHOLD_TARGETS.get("健康") == 3,
      adventure.logic.THRESHOLD_TARGETS.get("健康"))
for _k, _v in (("战力", 10), ("灵巧", 10), ("魅力", 10), ("智慧", 10), ("运气", 9)):
    check(f"{_k}目标 = {_v}", adventure.logic.THRESHOLD_TARGETS.get(_k) == _v,
          adventure.logic.THRESHOLD_TARGETS.get(_k))

print("\n=== 场景 17：精确策略为「跨过后 4 题门槛」让步于眼前的小收益 ===")
# 真实回归样本「飞天大盗」，第 7 题（属阶段 4，门槛 8~10）：
#   布置陷阱 评分最高，但只加运气/智慧；
#   寻踪作战 加战力但扣 1 血；联合盗宝 加战力 2 且不额外扣血。
# 朝阶段 4/5 的战力门槛推进才是正解 —— 精确策略选「联合盗宝」。
_fd = {"健康": 4, "战力": 6, "智慧": 6, "魅力": 6, "运气": 5, "灵巧": 6}
_d = adventure.logic.choose(_events, "飞天大盗", _fd, question=6)
check("选「联合盗宝」（战力+2）而不是评分更高的「布置陷阱」",
      _d.index == 0, f"选了「{_d.text}」")
check("第 7 题的精确期望 = 38.14", abs(_d.value - 38.1429) < 0.01, f"{_d.value:.4f}")
# 启发式回退路径（0.125/0.7）在同样输入下选「寻踪作战」：同样推进战力、
# 但不扣血，步数上略保守。它只在精确归纳不可用时兜底，所以这点差异可接受。
_dh = adventure.logic.choose(_events, "飞天大盗", _fd)
check("启发式回退路径也选到推进战力的项", _dh.index == 1, f"选了「{_dh.text}」")

print("\n=== 场景 18：价值函数的基本性质 ===")
_v9 = adventure.logic.future_value({**_fd, "战力": 9}, _events)
_v10 = adventure.logic.future_value({**_fd, "战力": 10}, _events)
check("战力 9→10 跨过阶段5门槛，后续期望上升", _v10 > _v9, f"{_v9:.2f} -> {_v10:.2f}")
check("健康归零时后续期望为 0",
      adventure.logic.future_value({**_fd, "健康": 0}, _events) == 0.0)

print("\n=== 场景 19：启发式回退路径的参数（软边距 + 生存因子） ===")
# 注意：线上主路径是精确后向归纳，下面这些 VALUE_* 常量**只影响回退路径**。
# 它们的值是在真实 7 角色初始分布上重调出来的（4 个全新池合并 +0.084）。
check("价值函数只看阶段 3+4+5", tuple(adventure.logic.VALUE_STAGES) == (3, 4, 5),
      adventure.logic.VALUE_STAGES)
check("混合比例 = 0.125", adventure.logic.VALUE_BLEND == 0.125, adventure.logic.VALUE_BLEND)
check("软边距尺度 = 0.7", adventure.logic.VALUE_SCALE == 0.7, adventure.logic.VALUE_SCALE)
check("生存因子阈值 = 2.0", adventure.logic.VALUE_HEALTH_FULL == 2.0,
      adventure.logic.VALUE_HEALTH_FULL)

# 软边距的核心性质：门槛**之下**的属性增长必须有梯度。
# 硬判定版本下「灵巧 7 → 8」（阶段4 的门槛是 8）若不跨过阈值就毫无变化，
# 启发式于是看不见「再堆 1 点就能吃下阶段 4 的两个 12 分」，后 4 题会全灭。
_va = adventure.logic.future_value({**_fd, "灵巧": 7}, _events)
_vb = adventure.logic.future_value({**_fd, "灵巧": 8}, _events)
check("门槛之下（灵巧 7→8）有梯度", _vb > _va, f"{_va:.2f} -> {_vb:.2f}")

# 生存因子：健康 >= 2 不打折，只剩 1 点血折半。
# 早死会让剩余题目全部作废，所以「血从 3 掉到 1」必须比纯期望分更值钱。
_h2 = adventure.logic.future_value({**_fd, "健康": 2}, _events)
_h1 = adventure.logic.future_value({**_fd, "健康": 1}, _events)
check("只剩 1 点血时后续期望被打折", _h1 < _h2, f"血2={_h2:.2f} 血1={_h1:.2f}")

print("\n=== 场景 20：精确后向归纳（线上主路径） ===")
# 这一节的价值不只是「能跑」：它盯住的是与 tools/adventure_upper_bound.py
# （独立实现）逐决策对齐后的定值。曾因为状态键里题号字段与「健康」字段重叠，
# 同一局面算出 53.15 而不是 78.83；靠这里的定值才能立刻发现。
check("EXACT_ENABLED 打开", adventure.logic.EXACT_ENABLED is True)
_x = adventure.logic.choose(_events, "冒险前没好武器", dict(adventure.DEFAULT_ATTRS),
                            question=0)
check("小玉初始六维下第 1 题选「找族长借一件」", _x.index == 1, f"选了「{_x.text}」")
check("第 1 题确实走精确后向归纳", _x.exact is True)
check("第 1 题精确期望 = 78.83", abs(_x.value - 78.8336) < 0.01, f"{_x.value:.4f}")
check("归纳表已建立", adventure.logic.exact_stats()[0] > 100000,
      f"{adventure.logic.exact_stats()[0]:,} 状态")
_r = adventure.logic.exact_choice(_events, "飞天大盗", _fd, 6)
check("第 7 题「飞天大盗」精确期望 = 38.1429", abs(_r[1] - 38.1429) < 0.01,
      f"{_r[1]:.4f}")
adventure.logic.reset_exact_memo()
check("强制清空归纳表后状态数归零", adventure.logic.exact_stats()[0] == 0,
      adventure.logic.exact_stats()[0])

print("\n=== 场景 21：精确归纳表整表常驻（不在局间重建） ===")
# 旧的「累计超过 40 万状态就清空」几乎等于**每局都重建**：单个角色就要 38~48 万状态
# （实测 tools/measure_table_warm.py）。现在整表常驻，7 个角色各付一次即可，
# 所以这里盯住三件事：上限够装下并集、换角色不清表、裁剪只失效缓存而不改判定。
check("EXACT_MAX_STATES 能装下 7 角色并集（实测 112~150 万）",
      adventure.logic.EXACT_MAX_STATES >= 1_500_000, adventure.logic.EXACT_MAX_STATES)
adventure.logic.choose(_events, "冒险前没好武器", dict(adventure.DEFAULT_ATTRS),
                       question=0)
_n1 = adventure.logic.exact_stats()[0]
check("建表后状态数 > 10 万", _n1 > 100000, f"{_n1:,}")
check("新一局不清表", adventure.logic.maybe_reset_exact_memo() is False)
check("状态数保持不变", adventure.logic.exact_stats()[0] == _n1,
      f"{adventure.logic.exact_stats()[0]:,}")
_d1 = adventure.logic.choose(_events, "冒险前没好武器",
                             dict(adventure.logic.CHARACTERS["小唐"]), question=0)
_n2 = adventure.logic.exact_stats()[0]
check("换角色后表只增不减（跨角色复用）", _n2 >= _n1, f"{_n1:,} → {_n2:,}")
_n3 = adventure.logic.exact_stats()[0]
adventure.logic._trim_exact_memo()
check("到上限时裁剪掉一半", adventure.logic.exact_stats()[0] <= _n3 // 2 + 1,
      f"{_n3:,} → {adventure.logic.exact_stats()[0]:,}")
_d2 = adventure.logic.choose(_events, "冒险前没好武器",
                             dict(adventure.logic.CHARACTERS["小唐"]), question=0)
check("裁剪只是缓存失效，决策与期望值完全不变",
      _d2.index == _d1.index and abs(_d2.value - _d1.value) < 1e-9,
      f"idx {_d1.index} vs {_d2.index}，期望 {_d1.value:.4f} vs {_d2.value:.4f}")

print("\n=== 场景 22：事件正则必须只命中自己 ===")
# 「危险任务」原本配了过宽的「族长」，于是题干「冒险前，族长准备送你一件礼物」
# 也被判成危险任务：代理按危险任务的四个选项评估、再把「第几项」用到礼物事件上，
# 选项顺序对得上但 require / 收益全错位。日志里该题干出现 6 次，全部判错，
# 实测代价 -0.24 分/局（tools/_gap_mismatch.py）。
_gift = "冒险前，族长准备送你一件礼物，你会选择"
_task = "在你准备离开村子去冒险时，族长想交给你一个很危险的任务，你会接受吗"
_weapon = "在冒险准备前，突然发现自己没有一把称手的好武器，你会"
_shop = "冒险前，你决定到商店买些必需品，但是钱不太够，你会"
check("礼物题干 →「冒险前神秘礼物」", adventure._match_event(_gift) == "冒险前神秘礼物",
      adventure._match_event(_gift))
check("危险任务题干 →「危险任务」", adventure._match_event(_task) == "危险任务",
      adventure._match_event(_task))
check("没好武器题干 →「冒险前没好武器」",
      adventure._match_event(_weapon) == "冒险前没好武器", adventure._match_event(_weapon))
check("商品买卖题干 →「商品买卖必备」",
      adventure._match_event(_shop) == "商品买卖必备", adventure._match_event(_shop))

print("\n=== 场景 23：题号由事件反推（同一题被重复读取时不推进） ===")
# 线上原本拿「决策次数」当题号。点击落不到新题上时同一题会被再答一次，
# 决策次数就比真实进度多，之后每一题的价值评估都偏后一个阶段 ——
# 实测第 1 题后多一次重复就掉约 2 分（tools/_gap_drift2.py）。
# 事件名唯一确定了阶段，同阶段题号连续且事件不会重复抽到，所以按事件反推才可靠。
_logic = adventure.logic
_logic.exact_choice(_events, "冒险前没好武器", dict(adventure.DEFAULT_ATTRS), 0)  # 确保表已建
_st2 = sorted(n for n, s in _logic._exact_event_stage.items() if s == 2)
_st4 = sorted(n for n, s in _logic._exact_event_stage.items() if s == 4)
check("每个阶段都能反查事件", len(_st2) >= 3 and len(_st4) >= 1,
      f"阶段2 {len(_st2)} 个事件 / 阶段4 {len(_st4)} 个事件")
_ans: dict = {}
check("阶段 1 事件 → 第 1 题（0 基 0）",
      _logic.question_for_event("冒险前没好武器", _ans) == 0,
      _logic.question_for_event("冒险前没好武器", _ans))
check("同一题重复读取 → 题号不变",
      _logic.question_for_event("冒险前没好武器", _ans) == 0,
      _logic.question_for_event("冒险前没好武器", _ans))
check("阶段 2 第 1 个事件 → 第 2 题（0 基 1）",
      _logic.question_for_event(_st2[0], _ans) == 1, _logic.question_for_event(_st2[0], _ans))
check("阶段 2 第 2 个事件 → 第 3 题（0 基 2）",
      _logic.question_for_event(_st2[1], _ans) == 2, _logic.question_for_event(_st2[1], _ans))
check("阶段 2 再多的事件被夹在该阶段题号上界",
      _logic.question_for_event(_st2[2], _ans) == 2, _logic.question_for_event(_st2[2], _ans))
check("阶段 4 第 1 个事件 → 第 7 题（0 基 6）",
      _logic.question_for_event(_st4[0], _ans) == 6, _logic.question_for_event(_st4[0], _ans))
check("未知事件 → None", _logic.question_for_event("不存在的世界", {}) is None)

print("\n=== 场景 24：开局先识别角色，用角色锁死初始六维 ===")
# 三级属性 OCR 全失败且账本为空时（实测发生过），原来会退化成 attrs={}，
# 整个价值函数的地基就错了。左侧木牌第一行的「角色」不依赖数值 OCR，
# 是同一时刻可用的权威初值来源。
_C = _logic.CHARACTERS
check("7 个角色齐备且都是六维", len(_C) == 7 and all(len(v) == 6 for v in _C.values()),
      sorted(_C))
check("小玉向量与 adventure.DEFAULT_ATTRS 一致",
      dict(_C["小玉"]) == dict(adventure.DEFAULT_ATTRS))
check("角色名末字互不相同（可作唯一键）", len({n[-1] for n in _C}) == 7,
      "".join(sorted(n[-1] for n in _C)))
check("精确名可解析", _logic.character_attrs("小玉") == dict(_C["小玉"]))
check("带前后噪声文本仍可解析", _logic.character_attrs("角色 小唐 ") == dict(_C["小唐"]))
check("已知形近误认别名可解析（小圤→小吒）",
      _logic.character_attrs("小圤") == dict(_C["小吒"]))
check("末字唯一命中（小猴被认成小侯）",
      _logic.character_attrs("小侯") == dict(_C["小猴"]), _logic.character_attrs("小侯"))
check("空文本/查无此人返回 None",
      _logic.character_attrs("") is None and _logic.character_attrs("查无此人") is None)

_pipe_path = os.path.join(ROOT, "assets", "resource", "base", "pipeline", "生存大冒险.json")
_pipe = json.load(open(_pipe_path, encoding="utf-8"))
check("管线里有「角色」OCR 节点", adventure.CHARACTER_NODE in _pipe, adventure.CHARACTER_NODE)
_pipe_roi = _pipe.get(adventure.CHARACTER_NODE, {}).get("recognition", {}).get("param", {}).get("roi")
check("节点 ROI 与 adventure_ui.json 的 character_roi 一致",
      _pipe_roi == UI["layout"]["character_roi"],
      f"{_pipe_roi} vs {UI['layout']['character_roi']}")


def _opening(char, ocr_attrs, q=0):
    adventure.STATE.char_checked = False
    ctx = FakeContext({adventure.CHARACTER_NODE: [char] if char else []})
    return adventure._opening_attrs(ctx, Argv.image, dict(ocr_attrs), q)


check("属性 OCR 全失败 → 用角色初值", _opening("小玉", {}) == dict(_C["小玉"]))
check("OCR 与角色完全一致 → 保持 OCR", _opening("小玉", dict(_C["小玉"])) == dict(_C["小玉"]))
check("OCR 只差一处 → 判为读错一位，按角色纠正",
      _opening("小玉", {**_C["小玉"], "战力": 5}) == dict(_C["小玉"]))
check("OCR 差两处以上 → 判为中途接手，保留 OCR",
      _opening("小玉", {**_C["小玉"], "战力": 5, "智慧": 6})
      == {**_C["小玉"], "战力": 5, "智慧": 6})
check("非第 1 题不做角色覆盖", _opening("小玉", {"战力": 9}, q=3) == {"战力": 9})
check("角色认不出 → 保留 OCR", _opening("查无此人", {"战力": 9}) == {"战力": 9})
check("重复读取第 1 题只认一次角色",
      (_opening("小玉", {}), adventure.STATE.char_checked)[1] is True)

print("\n" + ("全部通过" if ok else "存在失败项"))
sys.exit(0 if ok else 1)
