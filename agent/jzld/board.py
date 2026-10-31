# -*- coding: utf-8 -*-
"""九转炼丹 —— 棋盘识别（只读，不操作游戏）

用法：
    python jzld_board.py              # 截图 + 识别，保存 debug 截图
    python jzld_board.py --file x.png # 直接识别已有图片

输出：
    30 格矩阵（0=空, -1=丹渣, 1~9=数字球）
    棋盘网格坐标（列/行中心），用于后面点击
"""
from __future__ import annotations

import argparse
import colorsys
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
DEBUG_DIR = os.path.join(os.path.dirname(os.path.dirname(HERE)), "debug", "jzld")

# 棋盘形状：每行取哪几列（列号 0~6）
# 基准取自一张真实窗口截图 1404×882（7 列 6 行，格子 87.5px）
# 基准取自 1408×888 的真实满盘截图（从格子暗缝量出来的）：
#   列中心 452,536,620,704,788,872,956（步长 84）
#   行中心 231,313,394,478,564,648（步长 83.4）
# 棋盘是菱形、在画面里**居中**：格子边长 fixed 为画面高度的 84/882，
# 7 列正好水平居中、6 行竖直居中。这样不依赖具体窗口尺寸，
# 940×590 / 1147×720 / 1408×888 都能算对。
CELL_RATIO = 84.0 / 882.0
COLS_CENTER = 3.0      # 7 列 → 中间是第 3 列
ROWS_CENTER = 2.5      # 6 行 → 中心在 2、3 行之间

GRID_ROWS = {
    0: (2, 3, 4),
    1: (1, 2, 3, 4, 5),
    2: (0, 1, 2, 3, 4, 5, 6),
    3: (0, 1, 2, 3, 4, 5, 6),
    4: (1, 2, 3, 4, 5),
    5: (2, 3, 4),
}
COLS = 7
ROW_COUNT = 6
CELL_MIN, CELL_MAX = 60, 100          # 格子边长的合理区间


# ---------------------------------------------------------------- 网格检测
def _peaks(proj: np.ndarray, thr_ratio: float = 0.2) -> list[int]:
    """把一维投影里连续的亮段压成峰值坐标。"""
    if proj.size == 0:
        return []
    thr = proj.max() * thr_ratio
    idx = np.flatnonzero(proj > thr)
    if idx.size == 0:
        return []
    groups: list[list[int]] = []
    for i in idx:
        if groups and i - groups[-1][-1] <= 3:
            groups[-1].append(int(i))
        else:
            groups.append([int(i)])
    return [sum(g) // len(g) for g in groups]


def _cell_centers(pk: list[int], lo: int = CELL_MIN, hi: int = CELL_MAX) -> list[int]:
    """相邻两峰间距落在格子宽度区间内，就认为是一格的左右(上下)边。"""
    out = []
    for i in range(len(pk) - 1):
        w = pk[i + 1] - pk[i]
        if lo <= w <= hi:
            c = (pk[i] + pk[i + 1]) // 2
            if not out or c - out[-1] > w // 2:
                out.append(c)
    return out


def _snap_step(step: float) -> float:
    """把量出来的格子边长吸附到已知的几档（84 / 68 / 56 …），其它尺寸原样返回。"""
    for cand in (84.0, 78.0, 72.0, 68.0, 64.0, 60.0, 56.0, 52.0, 48.0):
        if abs(step - cand) <= 3.0:
            return cand
    return round(step, 1)


def _regularize(centers: list[int], want: int) -> list[int]:
    """从检出的中心里挑出等间距、数量正好 want 的一串。"""
    if len(centers) < want:
        return []
    diffs = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]
    if not diffs:
        return []
    step = int(round(float(np.median(diffs))))
    best: list[int] = []
    for start in range(len(centers) - want + 1):
        seg = centers[start:start + want]
        ok = all(abs((seg[i + 1] - seg[i]) - step) <= 4 for i in range(len(seg) - 1))
        if ok and (not best or seg[0] < best[0]):
            best = seg
    return best


def _border_lines(profile_gold: np.ndarray, lo: int, hi: int) -> list[int]:
    """从一维金色掩码里提取细线段（格子边框）的中心位置。"""
    idx = np.nonzero(profile_gold[lo:hi])[0] + lo
    out, st, pv = [], None, None
    for i in idx:
        if st is None:
            st = pv = int(i)
            continue
        if i - pv > 4:
            if pv - st < 14:
                out.append((st + pv) // 2)
            st = int(i)
        pv = int(i)
    if st is not None and pv - st < 14:
        out.append((st + pv) // 2)
    return out


def _fit_step(lines: list[int]) -> float:
    """从边框线里估格子边长：取相邻差值的中位数（隔一条的 272 也要能滤掉）。"""
    if len(lines) < 2:
        return 0.0
    ds = sorted(lines[i + 1] - lines[i] for i in range(len(lines) - 1))
    ds = [d for d in ds if d >= 40]
    if not ds:
        return 0.0
    return float(ds[len(ds) // 2])


_GRID_CACHE: dict = {}
_CALIB: list = []      # 开局校准一次的结果 [(xs, ys)]，整局复用


def _brown_runs(mask: np.ndarray, minlen: int = 30, maxlen: int = 72) -> list:
    """一维掩码里长度在 [minlen, maxlen] 的连续段中心。"""
    out, st = [], None
    for i, v in enumerate(mask):
        if v and st is None:
            st = i
        elif not v and st is not None:
            if minlen <= i - st <= maxlen:
                out.append((st + i) // 2)
            st = None
    if st is not None and minlen <= len(mask) - st <= maxlen:
        out.append((st + len(mask)) // 2)
    return out


def calibrate(im: np.ndarray) -> tuple[list[int], list[int]]:
    """开局空盘时量一次格子位置，整局复用。

    用户 m08780：「每局刚开局校准一次，然后不重新校准」。

    量的是**格子本身**（褐色底板）而不是金色边框——空格底板的颜色很稳定
    （R130~200 / G65~125 / B15~85），一段正好一个格子，比找边框线可靠得多：
    边框会被球压住、会被外圈八卦纹干扰，底板不会。
    """
    global _CALIB
    rgb = im[:, :, ::-1].astype(np.int16)          # 内部是 BGR
    R, G, Bl = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    brown = (R > 130) & (R < 200) & (G > 65) & (G < 125) & (Bl > 15) & (Bl < 85)
    h, w = rgb.shape[:2]

    def _best(axis: str, want: int) -> list:
        best, best_score = [], -1
        if axis == "v":      # 横扫找列
            lo, hi = int(w * 0.25), int(w * 0.80)
            cands = [int(h * f) for f in (0.44, 0.48, 0.52, 0.56, 0.60)]
            for y in cands:
                ln = _brown_runs(brown[y, lo:hi])
                ln = [x + lo for x in ln]
                sc = -abs(len(ln) - want)
                if sc > best_score:
                    best, best_score = ln, sc
        else:                # 竖扫找行
            lo, hi = int(h * 0.22), int(h * 0.86)
            cands = [int(w * f) for f in (0.42, 0.46, 0.50, 0.54, 0.58)]
            for x in cands:
                ln = _brown_runs(brown[lo:hi, x])
                ln = [y + lo for y in ln]
                sc = -abs(len(ln) - want)
                if sc > best_score:
                    best, best_score = ln, sc
        return best

    xs = _best("v", 7)
    ys = _best("h", 6)
    # 用中位间距把缺的补齐、多的砍掉，保证正好 7 / 6 个
    def _fix(cen: list, want: int) -> list:
        if len(cen) < 2:
            return []
        ds = sorted(cen[i + 1] - cen[i] for i in range(len(cen) - 1))
        step = ds[len(ds) // 2]
        cen = list(cen)
        while len(cen) < want:
            cen.insert(0, int(cen[0] - step))
        if len(cen) > want:
            ex = len(cen) - want
            cen = cen[ex // 2: len(cen) - (ex - ex // 2)]
        return cen

    xs, ys = _fix(xs, 7), _fix(ys, 6)
    if len(xs) == 7 and len(ys) == 6:
        _CALIB = [list(xs), list(ys)]
        _GRID_CACHE.clear()
        _GRID_CACHE[(h, w)] = (list(xs), list(ys))
        return list(xs), list(ys)
    _GRID_CACHE.clear()
    return _find_grid_impl(im)


def clear_calibration() -> None:
    global _CALIB
    _CALIB = []
    _GRID_CACHE.clear()


def find_grid(im: np.ndarray) -> tuple[list[int], list[int]]:
    """带缓存：同一窗口尺寸下格心不会变，没必要每帧重算。

    每帧都跑一遍金色掩码 + 5 条扫描线 + 滑移匹配，实测能把单步拖到
    2 秒以上（用户 m07487「点的非常慢」）。尺寸变了才重算。
    """
    _key = (im.shape[0], im.shape[1])
    if _key in _GRID_CACHE:
        _xs, _ys = _GRID_CACHE[_key]
        return list(_xs), list(_ys)
    _xs, _ys = _find_grid_impl(im)
    _GRID_CACHE[_key] = (list(_xs), list(_ys))
    return _xs, _ys


def _find_grid_impl(im: np.ndarray, detect: bool = False) -> tuple[list[int], list[int]]:
    """在截图上**直接量**格子边框，推出 7 列 6 行的中心。

    写死比例这条路走过两次都翻车：84/882 在 2294 宽下步长偏大、6 列累
    到最右偏 40+ 像素，十字画上去肉眼就是歪的，最右两格的采样框压到边
    框外 → 永远判不出「空格」（用户 m07385「你的十字根本没在正中心」）。
    所以改成量真实的金色边框线。
    """
    h, w = im.shape[:2]
    rgb = im[:, :, ::-1].astype(np.float32)          # BGR -> RGB
    gold = ((rgb[:, :, 0] > 175) & (rgb[:, :, 1] > 135) & (rgb[:, :, 2] < 165))
    # 单独扫一行/一列很容易正好压在边框上、或者被球挡住。多扫几条取
    # 「线最多、间距最规整」的那条。
    # 必须把扫描窗口限制在棋盘内部：棋盘外圈还有八卦纹、红色边框等一堆
    # 金色装饰线，之前没限制，顶部那些纹路把 ys 整体带偏了 80+ 像素
    # （用户 m07445 现场截图：十字整片落在棋盘左上方）。
    def _best_lines(axis: str) -> list:
        best, best_n = [], -1
        if axis == "v":
            lo, hi = int(w * 0.277), int(w * 0.748)
            for frac in (0.47, 0.50, 0.53, 0.56, 0.59):
                ln = _border_lines(gold[int(h * frac), :], lo, hi)
                if len(ln) > best_n:
                    best, best_n = ln, len(ln)
        else:
            lo, hi = int(h * 0.237), int(h * 0.915)
            for frac in (0.46, 0.50, 0.54, 0.58, 0.62):
                ln = _border_lines(gold[:, int(w * frac)], lo, hi)
                if len(ln) > best_n:
                    best, best_n = ln, len(ln)
        return best

    cols = _best_lines("v")
    rows = _best_lines("h")
    step_x = _fit_step(cols)
    step_y = _fit_step(rows)

    def _centers(lines, step, total):
        if len(lines) >= 2 and step > 40:
            # 相邻两条线之间是一个格子 → 取中点
            cen = [(lines[i] + lines[i + 1]) // 2 for i in range(len(lines) - 1)]
            # 用中位间距把缺的补齐到 total 个（从中间往两边长）
            while len(cen) < total:
                cen.insert(0, int(cen[0] - step))
                if len(cen) < total:
                    cen.append(int(cen[-1] + step))
            if len(cen) >= total:
                # 多出来的（棋盘左右边缘的装饰线）从两边对称砍掉
                _ex = len(cen) - total
                cen = cen[_ex // 2: len(cen) - (_ex - _ex // 2)]
            return cen
        return []

    xs = _centers(cols, step_x, 7)
    ys = _centers(rows, step_y, 6)
    # 边框检测在「棋盘外圈装饰线」和「行数不够」两种情况下会栽跟头，
    # 而这两件事我已经反复踩了四轮（m07385/m07445）。以 940×590 的真实
    # 窗口实测为准写死比例，检测结果只在完全吻合时才采用。
    # 横坐标取自 PrintWindow 客户区实测；纵坐标要再 +8px。
    # MFA 走的是 FramePool 截图（post_screencap），和 PrintWindow 差 8 像素，
    # 用 186 起步时整盘采样点全部落在格子金边上，std 从 8 涨到 47。
    _HX0, _HX1 = 308.0 / 940.0, 642.0 / 940.0
    _HY0, _HY1 = 156.0 / 590.0, 436.0 / 590.0
    _hx = [int(round(w * (_HX0 + (_HX1 - _HX0) * i / 6.0))) for i in range(7)]
    _hy = [int(round(h * (_HY0 + (_HY1 - _HY0) * i / 5.0))) for i in range(6)]
    # 满盘时球会干扰边框检测（金球本身就贴着金边），检测结果不可信。
    # 所以只在「开局校准」时（detect=True）才相信量出来的线；
    # 平时一律用开局校准的结果，没有校准过才退回写死比例。
    if detect and xs and ys and len(xs) == 7 and len(ys) == 6:
        return xs, ys
    if _CALIB and len(_CALIB) == 2:
        return list(_CALIB[0]), list(_CALIB[1])
    return _hx, _hy


def _circ_mask(g: np.ndarray, r: float = 0.40) -> np.ndarray:
    """只保留中心圆形区域，屏蔽格子背景和球外的高光/暗角。

    球是圆的，模板和实拍里「球外的褐色格子」占比各不相同；不做遮罩时
    NCC 会被背景主导，五和六（字形差别小、球体一样）就分不开。
    """
    h, w = g.shape
    yy, xx = np.mgrid[0:h, 0:w]
    m = ((yy - (h - 1) / 2.0) ** 2 + (xx - (w - 1) / 2.0) ** 2) <= (min(h, w) * r) ** 2
    out = np.zeros_like(g)
    if m.sum() < 8:
        return g
    vals = g[m]
    out[m] = vals - vals.mean()
    n = np.sqrt((out * out).sum()) or 1.0
    return out / n


def _to_gray32(img: Image.Image) -> np.ndarray:
    """统一裁到球体大小再缩成 32×32 灰度（去均值归一化）。"""
    g = np.array(img.convert("L"), dtype=np.float32)
    g = np.array(Image.fromarray(g.astype(np.uint8)).resize((32, 32), Image.LANCZOS),
                 dtype=np.float32)
    return _circ_mask(g)


def _hsv_color(im: np.ndarray, x: int, y: int, half: int = 20) -> str:
    """按 HSV 判颜色系：绿/蓝/紫/金/橙/红/渣/空/?。"""
    patch = im[y - half:y + half, x - half:x + half].reshape(-1, 3).astype(float)
    if patch.size == 0:
        return '?'
    mx = patch.max(axis=1)
    mn = patch.min(axis=1)
    sat = (mx - mn) / np.maximum(mx, 1.0)
    sel = patch[sat > np.percentile(sat, 75)].mean(axis=0)
    # patch 来自 BGR 图，colorsys 要 RGB，先反转
    h, s, v = colorsys.rgb_to_hsv(*(sel[::-1] / 255.0))
    h *= 360

    if 160 <= h <= 240:
        return '蓝'
    if 90 <= h <= 150:
        return '绿'
    if 265 <= h <= 310:
        return '紫'
    if h < 14:
        return '红'
    if 26 <= h <= 40 and s > 0.90 and v > 0.55:
        return '金'
    if 14 <= h <= 26 and s > 0.90 and v >= 0.58:
        return '橙'
    if s > 0.85 and v < 0.55:
        return '渣'
    if s < 0.90 and v < 0.60:
        return '空'
    return '?'


SIMPLE = {'金': 7, '橙': 8, '红': 9, '渣': -1, '空': 0}
# 同色多数字的颜色系才需要比字形：
#   绿 = 一 / 二      蓝 = 三 / 四      紫 = 五 / 六
MULTI = {'绿': [1, 2], '蓝': [3, 4], '紫': [5, 6]}


_SLAG: list = []


def _slag_template():
    """templates/tslag.png：丹渣（棕色三角）。丹渣不能落子，必须单独认出来。"""
    if _SLAG:
        return _SLAG[0]
    p = os.path.join(HERE, "templates_small", "tslag.png")
    _SLAG.append(np.array(Image.open(p).convert("L"), dtype=np.float32)
                 if os.path.isfile(p) else None)
    return _SLAG[0]


_EMPTY: list = []
_TEMPLATES: dict = {}    # {1..9: 原始尺寸灰度模板}，懒加载


def _empty_template():
    """templates/t0.png：空格子本体（褐色底 + 火焰暗纹），用来判空。"""
    if _EMPTY:
        return _EMPTY[0]
    p = os.path.join(HERE, "templates_small", "t0.png")
    if os.path.isfile(p):
        a = np.array(Image.open(p).convert("L"), dtype=np.float32)
        _EMPTY.append(a)
    else:
        _EMPTY.append(None)
    return _EMPTY[0]


def _templates() -> dict[int, np.ndarray]:
    """懒加载 templates/t1..t9.png，保持原始尺寸的灰度图（不缩放）。

    关键教训：**模板千万别缩放**。之前把模板和采样区都缩到 32×32，
    两者缩放比例不同（模板 45px→32，采样 60px→32），球体大小就不一致了，
    NCC 全部掉到 0.25 上下、五和六互相串。保持原尺寸 + 小窗口滑移搜索
    才能拿到 MPE 里那种 0.98 的水准。
    """
    if _TEMPLATES:
        return _TEMPLATES
    for n in range(1, 10):
        p = os.path.join(HERE, "templates_small", "t%d.png" % n)
        if os.path.isfile(p):
            g = np.array(Image.open(p).convert("L"), dtype=np.float32)
            _TEMPLATES[n] = g
    return _TEMPLATES


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    return float((a * b).sum() / (np.sqrt((a * a).sum()) * np.sqrt((b * b).sum()) + 1e-9))


def _best_ncc(big: np.ndarray, tpl: np.ndarray, rng: int = 8) -> float:
    """在 big 的中心附近滑移 tpl，取最高 NCC。

    用 cv2.matchTemplate(TM_CCOEFF_NORMED)，与 MPE 里验证模板用的
    是同一个函数——纯 numpy 手搓的 NCC 在同一批图上只能到 0.6，
    而 OpenCV 能到 0.98，差别就在它做了积分图 + 浮点累加，精度够。
    """
    import cv2
    H, W = big.shape
    th, tw = tpl.shape
    if th > H or tw > W:
        return -1.0
    res = cv2.matchTemplate(big.astype(np.float32), tpl.astype(np.float32),
                            cv2.TM_CCOEFF_NORMED)
    # 只取中心 ±rng 范围内的最优，避免匹配到格子外的背景
    h, w = res.shape
    cy, cx = h // 2, w // 2
    y0, y1 = max(0, cy - rng), min(h, cy + rng + 1)
    x0, x1 = max(0, cx - rng), min(w, cx + rng + 1)
    sub = res[y0:y1, x0:x1]
    return float(np.nanmax(sub)) if sub.size else -1.0


def _patch_mask(im: np.ndarray, x: int, y: int, half: int = 30) -> np.ndarray:
    p = im[y - half:y + half, x - half:x + half]
    rgb = p[:, :, ::-1]  # BGR -> RGB
    return np.array(Image.fromarray(rgb.astype(np.uint8)).convert("L"), dtype=np.float32)


def _stroke_rows(im: np.ndarray, x: int, y: int, half: int) -> int:
    """数球面数字有几道横笔画（一=1、二=2、三=3）。

    绿球里「一」和「二」都是绿的，NCC 常把二认成一；
    但它们的横笔画数是唯一且稳定的差别，直接数投影里的「横亮带」更靠谱。
    """
    p = im[y - half:y + half, x - half:x + half]
    if p.size == 0:
        return 0
    g = p[:, :, ::-1].astype(np.float32).mean(axis=2)
    h, w = g.shape
    # 只取正中 30%：取到 50% 时球的下缘暗影会额外贡献一道「亮带」，
    # 一（一条横线）就会被数成 2。
    c = g[int(h * 0.35):int(h * 0.65), int(w * 0.35):int(w * 0.65)]
    if c.size == 0:
        return 0
    thr = float(np.percentile(c, 10))                # 最暗的 10% 当笔画
    dark = c <= thr
    rows = dark.sum(axis=1).astype(np.float32)
    if rows.max() < 2:
        return 0
    rows = rows / rows.max()
    n, in_peak = 0, False
    for v in rows:
        if v > 0.45 and not in_peak:
            n += 1
            in_peak = True
        elif v < 0.30:
            in_peak = False
    return n


def _stroke_cols(im: np.ndarray, x: int, y: int, half: int) -> int:
    """数竖笔画（列投影峰数）：蓝球三/四靠它分——三有 1 道，四像「目」有 2 道。"""
    p = im[y - half:y + half, x - half:x + half]
    mx = p.max(axis=2).astype(np.float32)
    dark = (mx < np.percentile(mx, 35)).astype(np.float32)
    prof = dark.mean(axis=0)
    on = prof > 0.12
    n = 0
    for i in range(1, len(on)):
        if on[i] and not on[i - 1]:
            n += 1
    return n


_TEMPLATE_HIST: dict = {}


def _template_hists() -> dict:
    """每个模板的「梯度方向直方图」（6 bin，幅值加权归一化）。

    为什么需要：七（金）和八（橙）**颜色几乎一样**——球心 B-G/G-R 只差
    4~23，而且真实样本相对模板整体偏 10~15，颜色先验会把七判成八。
    但字形结构差很多（七是一斜一横，八是两撇），梯度方向直方图的 L1
    距离实测 0.44，是同色对里最大的，足够分开（用户 m08408「7和8好像
    认错的很多」）。
    """
    if _TEMPLATE_HIST:
        return _TEMPLATE_HIST
    import cv2
    for n, g in _templates().items():
        h, w = g.shape
        cy, cx = h // 2, w // 2
        core = g[max(0, cy - 9):cy + 9, max(0, cx - 9):cx + 9]
        gx = cv2.Sobel(core, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(core, cv2.CV_32F, 0, 1, ksize=3)
        mag = np.hypot(gx, gy)
        ang = np.degrees(np.arctan2(gy, gx)) % 180.0
        hist, _ = np.histogram(ang, bins=6, range=(0, 180), weights=mag)
        tot = float(hist.sum())
        _TEMPLATE_HIST[n] = (hist / tot if tot > 0 else hist).astype(np.float32)
    return _TEMPLATE_HIST


def _hist_of(im_gray: np.ndarray, x: int, y: int):
    import cv2
    h, w = im_gray.shape
    y0, y1 = int(y) - 9, int(y) + 9
    x0, x1 = int(x) - 9, int(x) + 9
    if y0 < 0 or x0 < 0 or y1 > h or x1 > w:
        return None
    core = im_gray[y0:y1, x0:x1]
    gx = cv2.Sobel(core, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(core, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.hypot(gx, gy)
    ang = np.degrees(np.arctan2(gy, gx)) % 180.0
    hist, _ = np.histogram(ang, bins=6, range=(0, 180), weights=mag)
    tot = float(hist.sum())
    return (hist / tot if tot > 0 else hist).astype(np.float32)


def classify_cell(im_gray: np.ndarray, im_v: np.ndarray, x: int, y: int, rng: int,
                  im_bgr: np.ndarray | None = None) -> int:
    """返回 0=空 / -1=丹渣 / 1~9=数字球 / -2=无法判断。

    分两步，顺序很关键：

    1. **判空不能用颜色**——空格子的火焰暗纹是橙黄的，HSV 上跟"金球"一个色系。
       真正靠得住的是**球心高光**：球顶那个白点 V≈0.97，空格最多 0.9。
       所以先在 ±rng 里找 V 最大值，低于 VTHR 就当空。
    2. **认数字用 cv2.matchTemplate(TM_CCOEFF_NORMED)**，并且在 ±rng 里滑移。
       球在格子里的位置并不完全居中（实测能偏 20 多像素），固定采样点
       一定会歪；滑移搜索能把这个偏移吃掉。这也是 MPE 里能到 0.98 的原因。
    """
    h, w = im_v.shape
    ys0, ys1 = max(0, y - rng), min(h, y + rng)
    xs0, xs1 = max(0, x - rng), min(w, x + rng)
    if ys1 <= ys0 or xs1 <= xs0:
        return -2

    import cv2
    # 丹渣要**最先**认：它底色和空格子一模一样，只有中间多了个棕色三角。
    # 之前漏了这一步，丹渣被判成空格，脚本对着它一直点，盘面永远不变。
    # 丹渣靠"暗度"认：棕色三角实测暗像素(V<0.35)占比 0.19，
    # 而正常空格子是 0.00、带光效的空格也才 0.09——比模板匹配稳得多
    # （tslag.png 是从整格里裁的，跟空格的底色几乎一样，匹配分只有 0.588 vs 0.58，分不开）。
    # 丹渣改用模板匹配。原来的「暗像素占比」判据是按 1404 尺度调的，
    # 换到 940×590（球 40px）后同样的球暗像素占比翻倍，整盘球都被判成渣
    # （用户 m07590 那局盘面全是 X）。模板是按当前尺度裁的，直接比更稳。
    ts = _slag_template()
    if ts is not None:
        ths, tws = ts.shape
        _half = max(ths, tws) // 2 + rng + 2
        _big = im_gray[max(0, y - _half):y + _half, max(0, x - _half):x + _half]
        if ths <= _big.shape[0] and tws <= _big.shape[1]:
            sv = float(cv2.matchTemplate(_big, ts, cv2.TM_CCOEFF_NORMED).max())
            if sv >= SLAG_THR:
                return -1

    # 判空改成「和空格子本体模板 t0 比」：空格稳定 0.72，真球最高只到 0.66，
    # 比原来靠球心高光(VTHR)稳得多——空格子的火焰纹在某些分辨率下会爆到 0.99。
    # 判空改成「格心 20×20 的灰度标准差」。
    # 940×590 实测（30 格空盘）：空格 std 7.7~22.6；有球的格 45.9~63.0；
    # 丹渣 46.3（已在上一步被模板拦掉，走不到这里）。阈值 30 能干净切开。
    # 原来拿空格本体模板 t0 去比不行：40px 尺度下整格几乎全是火焰暗纹，
    # 每个格子的纹路位置都不同，同为空格的匹配分只有 0.12~0.24（阈值 0.70），
    # 于是每一格都判不出空、全盘 30 格返回 -2（用户 m07705「又开始乱下」）。
    _cy0, _cy1 = int(y) - 10, int(y) + 10
    _cx0, _cx1 = int(x) - 10, int(x) + 10
    if 0 <= _cy0 and _cy1 <= h and 0 <= _cx0 and _cx1 <= w:
        # 判空只看**颜色**，不能看方差：紫球中心是均匀的紫黑（std 25.5），
        # 空格底是均匀的褐色（std 8~26），两者的 std 区间重合，用 std 判空
        # 会把整片紫球全判成「空」（用户 m08077「这里全是紫6，为什么你老看错」）。
        # 空格底实测 [158,91,45]（BGR），球要么 R<130 要么 B>100。
        _p = im_bgr[int(y), int(x)]
        _is_brown = (_p[2] > 140 and _p[2] < 180 and _p[1] > 70 and _p[1] < 115 and _p[0] < 70)
        if _is_brown:
            # 再确认一次：真空格子中心一定是「亮且偏灰」的褐色；
            # 但金球/橙球中心也可能是亮橙，靠 B 通道区分（球的 B 明显更低或更高）。
            _pb, _pg, _pr = int(_p[0]), int(_p[1]), int(_p[2])
            _spread = max(_pb, _pg, _pr) - min(_pb, _pg, _pr)
            # 空格底 max-min 实测 113（BGR 45/91/158）；球实测 144~178
            # （绿二 144、紫 158、金 178）。阈值 128 卡在两者中间。
            if _spread < 128:
                return 0

    tpl = _templates()
    hists = _template_hists()
    inp_hist = _hist_of(im_gray, x, y)
    # 先按球心颜色判色系，只在同色系模板里比。判不出才退回全量。
    _col = _ball_color(im_bgr, x, y, rng) if im_bgr is not None else None
    _grp = _hue_group_of(_col) if _col is not None else None
    _cands = [n for n in tpl if _grp is None or HUE_GROUP.get(n) == _grp]
    if not _cands:
        _cands = list(tpl)
    best_n, best_v = -2, -1.0
    for n in _cands:
        t = tpl[n]
        th, tw = t.shape
        half = max(th, tw) // 2 + rng + 2
        big = im_gray[max(0, y - half):y + half, max(0, x - half):x + half]
        if th > big.shape[0] or tw > big.shape[1]:
            continue
        v = float(cv2.matchTemplate(big, t, cv2.TM_CCOEFF_NORMED).max())
        th_hist = hists.get(n)
        if inp_hist is not None and th_hist is not None:
            dist = float(np.abs(inp_hist - th_hist).sum())
            v = (1.0 - HIST_W) * v + HIST_W * (1.0 - min(dist, 2.0) / 2.0)
        # 同色系内部靠颜色细差拉一档（绿看 S/V，金橙也看 S/V）。
        # 权重只能给到 0.20：试过 0.45，颜色压过字形后大片格子掉到
        # 匹配阈值以下，整盘变问号（实测 step_05~25 全崩）。
        if im_bgr is not None:
            # 第二层颜色特征暂时关闭：模板与实际球的 S/V 偏差过大（绿二模板 S=215，实际 154），
            # 加了反而把 1↔2、7↔8 系统性对调。等重裁一套同源模板再开。
            pass
        if v > best_v:
            best_n, best_v = n, v
    need = MATCH_THR_BY_NUM.get(best_n, MATCH_THR)
    return best_n if best_v >= need else -2

# 色相分组：先按球的颜色把候选缩到同色系，再做模板匹配。
# 不这么做的话「橙八」会被判成「蓝三」——灰度匹配下两者的字形轮廓太像，
# 实测橙球对 t3 的匹配分高达 0.91（用户 m11076 案）。
# 模板实测色相（OpenCV H，0~180）：
#   t1/t2 绿 57 | t3/t4 蓝 101~104 | t5/t6 紫 142~143
#   t7 金 17 | t8 橙 14 | t9 红 5
HUE_GROUP = {1: 'g', 2: 'g', 3: 'b', 4: 'b', 5: 'p', 6: 'p', 7: 'w', 8: 'w', 9: 'r'}
HUE_RANGE = {'g': (45, 75), 'b': (88, 120), 'p': (128, 158), 'w': (11, 30), 'r': (0, 10)}


# 同色系内部的第二层特征（40px 下字形只差一两条横线，光靠模板分不开）：
#   绿一 t1: S≈117 V≈192（浅）  / 绿二 t2: S≈172 V≈169（深）
#   金七 t7: R-B≈122            / 橙八 t8: R-B≈140
# 实测「暗行段」两者都是 2，没用；S/V 和 R-B 才有区分度。
# 参考值用「球心 16×16 中位数」量（与 _ball_color 同一算法），
# 模板实测：绿一 S142/V201、绿二 S215/V171；金七 R-B159、橙八 R-B201。
# 金/橙改用饱和度 S 而不是 R-B：实测金八那颗 R-B=178，夹在 t7(159) 与
# t8(201) 中间偏 t7，判错；而它的 S=216，明显落在 t8(232) 一侧。
_HSV_REF = {
    1: (142, 201), 2: (215, 171),
    7: (187, 217), 8: (232, 221),
}


def _ball_color(im_bgr, x: int, y: int, rng: int = 12):
    """取球心颜色：在 ±rng 内挑最亮的 15% 像素求均值。

    直接取格心单像素不行——球常偏 1/3 格，量到的是暗边，
    实测金八的 R-B 会从 140 掉到 70（模板 140），绿二 V 从 169 掉到 126，
    于是「绿一/绿二」「金七/橙八」两对全判错（用户 m11093）。
    """
    h, w = im_bgr.shape[:2]
    y0, y1 = max(0, y - rng), min(h, y + rng)
    x0, x1 = max(0, x - rng), min(w, x + rng)
    z = im_bgr[y0:y1, x0:x1].astype(np.int16)
    if z.size == 0:
        return im_bgr[y, x]
    # 不能取最亮的那批——球顶的高光是接近纯白（V 247+、S 40），
    # 颜色信息全丢（实测绿二被量成 S=43）。取整个搜索区的中位数最稳：
    # 球占区域一半以上，中位数就落在球面上。
    return np.median(z.reshape(-1, 3), axis=0)


def _fine_score(n: int, bgr) -> float:
    """同色系内部加一个颜色距离分（0~1，越像越高）。"""
    ref = _HSV_REF.get(n)
    if ref is None:
        return 1.0
    import cv2
    rgb = np.uint8([[[int(bgr[2]), int(bgr[1]), int(bgr[0])]]])
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[0, 0]
    # 绿和金/橙都按 S/V 比
    d = abs(int(hsv[1]) - ref[0]) / 255.0 + abs(int(hsv[2]) - ref[1]) / 255.0
    return max(0.0, 1.0 - d * 3.0)


def _hue_group_of(bgr) -> str | None:
    """由球心颜色判色系；判不出返回 None（退回全模板匹配）。"""
    import cv2
    rgb = np.uint8([[[int(bgr[2]), int(bgr[1]), int(bgr[0])]]])
    h = int(cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[0, 0, 0])
    for g, (lo, hi) in HUE_RANGE.items():
        if lo <= h <= hi:
            return g
    return None


VTHR = 0.96       # 球心高光的亮度下限，低于它判空
HIST_W = 0.35      # 梯度直方图在综合分里的权重（七/八靠这个分开）
MATCH_THR = 0.70   # 模板匹配分数下限（0.80 时有 3 个格子卡在 0.72~0.77 被判 ?）
# 「八」的模板最泛（空格子的火焰纹都能蹭到 0.80），单独抬高一档
MATCH_THR_BY_NUM = {8: 0.78}
EMPTY_THR = 0.70   # 旧判据，已停用（40px 尺度下空格匹配分只有 0.12~0.24）
EMPTY_STD = 30.0   # 格心 20×20 灰度标准差上限：空格 <23，球/丹渣 >45
SLAG_THR = 0.55    # 与丹渣模板 tslag.png 的相似度下限
SLAG_DARK = 0.15   # 旧判据，已停用


def is_board_screen(im: np.ndarray) -> bool:
    """看顶部「当前总分」那行白字在不在，粗略判断是不是棋盘界面。"""
    h, w = im.shape[:2]
    band = im[int(h * 0.115):int(h * 0.20), int(w * 0.14):int(w * 0.60)]
    if band.size == 0:
        return False
    v = band.max(axis=2).astype(np.int16)
    bright = float((v > 205).mean())
    return bright > 0.012


def _normalize(im: np.ndarray) -> np.ndarray:
    """所有输入统一缩放到基准分辨率再识别（模板是按这个尺度裁的）。"""
    h, w = im.shape[:2]
    # 模板已换成 940×590 原生尺度（球 40px），不再缩到 1404×882。
    # 缩放会把 40px 的球插值放大又缩回，糊成一团，匹配分掉到 0.5 以下
    # （用户 m07550 选择「就在 940×590 下做」）。
    return im
    if abs(w - 1404) <= 2 and abs(h - 882) <= 2:
        return im
    return np.array(Image.fromarray(im[:, :, ::-1]).resize((1404, 882), Image.LANCZOS))[:, :, ::-1]


def empty_scores(im_gray: np.ndarray, xs, ys, rng: int) -> dict:
    """每个有效格跟「空格子本体模板 t0」的相似度（越高越像空格）。

    给「明明还有空格、却被判成了球」的情况兜底用：填满阶段如果既没有
    空格、又没看到结算画面，就挑这里分数最高的那一格再点一次
    （用户 m07229 选的方案 2）。
    """
    import cv2
    t0 = _empty_template()
    out: dict = {}
    if t0 is None:
        return out
    th0, tw0 = t0.shape
    h, w = im_gray.shape
    for r, cols in GRID_ROWS.items():
        for c in cols:
            x, y = xs[c], ys[r]
            half = max(th0, tw0) // 2 + rng + 2
            big = im_gray[max(0, y - half):y + half, max(0, x - half):x + half]
            if th0 > big.shape[0] or tw0 > big.shape[1]:
                continue
            out[(r, c)] = float(cv2.matchTemplate(big, t0, cv2.TM_CCOEFF_NORMED).max())
    return out


def recognize(im: np.ndarray) -> dict:
    oh, ow = im.shape[:2]
    # 网格必须在**原图**上量：find_grid 是照着 940×590 的真实窗口标定的，
    # 先缩到 1404×882 再量就全歪了（这轮「点了没反应、盘面不变」就是这么
    # 来的——坐标算出来是 1404 尺度却当成 940 尺度在用）。
    xs, ys = find_grid(im)
    nh, nw = im.shape[:2]
    # _normalize 现在不缩放（模板已换成原生尺度），所以换算系数恒为 1。
    # 之前这里按 ow/1404 算，等于把已经正确的坐标又缩小了 0.67 倍，
    # 分类全落到格子外面（用户 m07590 那局盘面全是 ?）。
    fx, fy = float(nw) / float(ow), float(nh) / float(oh)

    rgb = im[:, :, ::-1]
    im_v = rgb.max(axis=2).astype(np.float32) / 255.0
    im_gray = np.array(Image.fromarray(rgb).convert("L"), dtype=np.float32)

    step = (xs[-1] - xs[0]) / float(len(xs) - 1) if len(xs) > 1 else 87.5
    # 采样半径不能太大：窗口一旦吃到格子金边，空格的高光会顶到 1.0，判空就失效了
    # 滑移搜索半径要有上限：格子放大到 1440 高时 step 约 143px，按 0.22
    # 算会到 31px，加上模板半径就是 47px 的采样框，会吃到格子金边（V=1.0）
    # 导致判空失效、匹配分数乱跳。实测 20px 封顶最稳。
    rng = min(20, max(6, int(step * 0.22)))

    cells: dict[tuple[int, int], int] = {}
    for r, cols in GRID_ROWS.items():
        for c in cols:
            cells[(r, c)] = classify_cell(im_gray, im_v, xs[c], ys[r], rng, im)

    # 返回原图坐标（点击用），分类用的是归一化图坐标
    return {"ok": True,
            "xs": [int(round(v * fx)) for v in xs],
            "ys": [int(round(v * fy)) for v in ys],
            "cells": cells}


def render(im: np.ndarray, res: dict, path: str | None = None) -> Image.Image:
    """把识别结果画到图上，核对用。"""
    img = Image.fromarray(im[:, :, ::-1]).convert("RGB")
    d = ImageDraw.Draw(img)
    names = {0: "空", -1: "渣", -2: "?"}
    xs, ys = res.get("xs") or [], res.get("ys") or []
    # recognize 返回的就是传入图的坐标（不再缩放），这里不能再按 1404 换算，
    # 否则十字会整体放大 1.49 倍往右下飘，看着像「坐标偏了」其实是画错了。
    for (r, c), v in (res.get("cells") or {}).items():
        if r >= len(ys) or c >= len(xs):
            continue
        x, y = xs[c], ys[r]
        color = (0, 255, 255) if v > 0 else ((255, 0, 255) if v == -2 else (0, 255, 0))
        d.ellipse([x - 20, y - 20, x + 20, y + 20], outline=color, width=2)
        d.text((x - 4, y - 6), names.get(v, str(v)), fill=color)
    if path:
        img.save(path)
    return img
