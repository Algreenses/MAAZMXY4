# 造梦西游4 · MAA 自动化项目（MAAZMXY4）

> 基于 MaaFramework 的《造梦西游4》日常自动化项目，适配「造梦盒子」客户端。
> 后台截图 + 后台点击，不注入游戏进程、不改游戏文件。共 14 个任务。

---

本项目采用 **[PolyForm Noncommercial License 1.0.0](LICENSE)**（非商业许可），
**不是** OSI 认可的开源协议，属于 **source-available（源码可见）**。

| | |
| --- | --- |
| ✅ 允许 | 个人免费使用、学习研究、修改代码、免费分享（须随附 `LICENSE`） |
| ❌ 禁止 | **售卖**、任何**商业用途**、删除版权声明、伪装成原创、转授权 |

完整条款见 [`LICENSE`](LICENSE)，中文通俗说明见 [`许可说明.md`](许可说明.md)，
第三方组件许可见 [`NOTICE`](NOTICE)。

---

## 这是什么

《造梦西游4》的副本刷取与活动自动化项目，基于 **MaaFramework** 的 Project Interface V2。

本程序做的事：

1. **后台截图**：用 MaaFramework 的 `FramePool` 抓游戏窗口，**窗口被别的程序挡住也能抓**，
   统一缩放到短边 720，所有坐标按 720p 基准换算。
2. **后台点击/按键**：用 `SendMessage` 发消息，**不移动真实鼠标、不抢键盘焦点**，
   跑起来该干嘛干嘛。

## 任务列表

| 任务 | 说明 |
| --- | --- |
| 伽岚（万世冥阁） | 副本循环，可设刷取次数与回放文件 |
| 贪妄（金蟾宝宫） | 同上 |
| 无冥（孤魂岭） | 同上 |
| 摩多（七叶树窟） | 同上 |
| 摩多（幕山） | 同上 |
| 烛时（钟晷极地） | 同上 |
| 朱电（琉璃宫） | 同上 |
| 悬赏（遁地大盗） | 悬赏任务，需先接取该悬赏并回到主线地图 |
| 悬赏（守卫宝烛） | 同上 |
| 悬赏（守卫太宗） | 同上 |
| 悬赏（追捕飞贼） | 同上 |
| 生存大冒险·自动答题 | 活动答题，OCR 读题 + 期望值推演选最优选项（[详细说明](docs/zh_cn/生存大冒险-使用说明.md)） |
| 录制战斗过程 | 录制你自己的操作，供上面的副本任务回放 |
| 打开录制文件夹 | 打开 `recordings/` 目录，方便管理录制文件 |

## 主要特性

- **无痕后台**：`FramePool` 截图 + `SendMessage` 输入，窗口被遮挡照常运行。
- **任意分辨率**：不用校准坐标，720p 基准自动等比换算到你的实际画面。
- **录制回放**：把你的操作录成 JSON，副本任务里直接重放，不需要写任何代码。

## 环境要求

- Windows 10（1903 及以上）/ 11，64 位
- **无需安装 Python、不需要管理员权限**
- 游戏客户端「造梦盒子」
  （窗口类名 `ApolloRuntimeContentWindow`，会自动识别）
- **请勿最小化游戏窗口**：被其他窗口挡住没问题，最小化会抓不到画面

> ⚠️ 请把整个文件夹放在**普通目录**（桌面、D 盘都行）。
> 如果放在带「低完整性标签」的受限目录里，Windows 会禁止程序抓取其它窗口，
> 表现为启动后提示「抓不到游戏画面」。

## 使用（发布包版，推荐）

### 第 1 步：下载并解压

到 [Releases](https://github.com/Inkyu-Zero/MAAZMXY4/releases) 下载
`MAAZMXY4 v2.0 自解压.exe`（约 103 MB）。

双击它，在弹出的「解压到」里点**解压**（默认就是它自己所在的目录），
会自动生成 `MAA造梦西游4` 文件夹（解压后约 328 MB）。

解压后目录长这样：

```
MAA造梦西游4/
├─ MAA造梦西游4.exe      ← 双击这个
├─ DependencySetup_依赖库安装_win.bat   ← 第一次用先跑它装 .NET
├─ interface.json
├─ agent/                 ← Python Agent（答题/录制/回放）
├─ resource/base/         ← Pipeline、图片、OCR 模型
└─ runtimes/              ← 客户端依赖 + 内嵌 Python 运行时
```

### 第 2 步：运行

1. 先启动游戏「造梦盒子」，进入游戏画面。
2. 双击 `MAA造梦西游4.exe`。
3. 左侧「任务列表」里勾选要跑的任务，在右侧面板设置任务参数
   （如「刷取次数」「回放文件」）。
4. 点「开始任务」。

## 录制回放怎么用

以「伽岚（万世冥阁）」为例：

1. **先录一遍**：启动游戏，手动点进副本，然后运行「录制战斗过程」任务。
2. 进入副本后按 **F9** 开始录制。
3. 正常打完这一波（放技能、跑位、按什么键都行）。
4. 走到副本出口前按 **F10**，弹框里给这次录制起个名字（如 `伽岚材料`）。
5. 之后运行「伽岚（万世冥阁）」，在「回放文件」下拉里选 `伽岚材料`，
   设好「刷取次数」，点开始即可。

> 只需要录 **进入副本后到退出副本前** 的操作，进出副本由本项目自己处理。
> 想调整回放速度，可以在任务的 `custom_action_param` 里改 `speed`（默认 1.0）。

## 使用（源码版）

在 Windows x64 上安装 Python 3.12 或更新版本用于引导，然后在仓库根目录执行：

```powershell
py -3.12 tools/setup_runtime.py
```

脚本下载固定版本的官方客户端、独立 Python 和完整依赖，校验 SHA256，
先在暂存目录验证 Agent 与资源，再生成根目录的运行布局。
不会向系统 Python 安装包，也不要求安装 uv 或 7-Zip。

| 组件 | 固定版本 |
| --- | --- |
| MFAAvalonia | 2.16.2（官方 Windows x64 ZIP） |
| 独立 CPython | 3.12.13，python-build-standalone 20260510，包含 Tk/Tcl |
| MaaFw / 原生 MaaFramework | 5.12.3 / 5.12.3 |
| MaaAgentBinary / numpy | 1.0.1 / 2.2.6 |
| StrEnum / pynput / six | 0.4.15 / 1.8.1 / 1.17.0 |

来源、版本和归档散列见 `tools/runtime.lock.json`；全部 Python wheel 的版本与散列
见 `tools/requirements-runtime.lock`。桌面客户端另需 .NET 10 Desktop Runtime 和
VC++ x64 运行库，安装脚本会检查现有组件；缺少时按下方运行库说明安装后重试。

完成后双击根目录 `MFAAvalonia.exe`，或执行 `./Start-MAA.ps1`。
客户端会读取根目录 `interface.json` 与 `resource/base/`；
规范源码是 `assets/interface.json` 与 `assets/resource/base/`，题库保留在 `assets/data/`。

```powershell
# 完整自检：依赖、Tcl、原生库、OCR/模板/Pipeline、Agent IPC 启停；不操作游戏
py -3.12 tools/setup_runtime.py --check

# 关闭客户端后重建，之前的运行时移到 .runtime-cache/backups/，不删除录制和配置
py -3.12 tools/setup_runtime.py --rebuild

# 首次联网安装后，可用缓存中的归档和 wheel 离线重建
py -3.12 tools/setup_runtime.py --rebuild --offline

# 修改 assets 后同步运行资源；覆盖前备份 interface/resources
py -3.12 tools/setup_runtime.py --sync-resources
```

普通重复执行会保留已有运行版 interface（包括录制文件下拉）和资源。
`--sync-resources` 会用规范配置覆盖它们，录制文件本身仍保留在 `recordings/`。
运行时、下载缓存和备份不入 Git；请使用外部 Python 执行重建，避免替换正在运行的解释器。

改完 Pipeline 后记得校验：

```bash
runtimes\python\python.exe tools/check_adventure.py     # 生存大冒险题库与事件自检
```

## 常见问题

**Q：双击 `MAA造梦西游4.exe` 没反应，或者提示缺少 .NET？**

客户端是 .NET 10 应用，第一次在新电脑上用需要先装运行库：

1. 双击同目录的 **`DependencySetup_依赖库安装_win.bat`**
2. 它会自动装 **.NET 10 桌面运行时** 和 **VC++ 2015-2022 运行库**（优先用 `winget`，没有就自己下载安装包）
3. 装完再双击 `MAA造梦西游4.exe`

如果脚本提示没有 `winget`，它会走下载安装；下载失败就手动装：
<https://aka.ms/dotnet/10.0/windowsdesktop-runtime-win-x64.exe>

**Q：提示「抓不到游戏画面」？**

1. 确认游戏窗口标题含「造梦盒子」，且没有最小化。
2. 确认整个文件夹不在带「低完整性标签」的受限目录里
   （管理员 PowerShell 执行 `icacls "<目录>" /setintegritylevel (OI)(CI)Medium /T /C` 可修复）。
3. 如果游戏以**管理员权限**运行，本项目也要用管理员权限运行。

**Q：点关卡没反应，或者点进了别的关卡？**

鼠标悬停在关卡上会触发高亮贴图。本项目已经用「点到就移开」的方式规避，
如果仍然偶发，把「刷取次数」设小一点先观察，或换用录制回放版本。

**Q：回放和录制时的手感不一样？**

回放按时间轴对齐，误差在 ±25ms 以内。如果游戏卡顿，可以把 `speed` 调到 `0.8` 放慢。

**Q：「回放文件」下拉里没有我刚录的？**

下拉在**保存时**自动刷新，但客户端缓存了界面，需要**重启客户端**才能看到新条目。
或者直接选「（手动选择文件…）」弹框选文件。

## 文件说明

| 路径 | 用途 |
| --- | --- |
| `interface.json` | Project Interface V2 定义：任务、controller、option |
| `DependencySetup_依赖库安装_win.bat` | 一键安装 .NET 10 桌面运行时与 VC++ 运行库 |
| `agent/` | Python Agent：`adventure*`（答题）、`battle_record`（录制）、`battle_player`（回放）、`toast`（通知） |
| `resource/base/pipeline/` | 所有 Pipeline 节点 |
| `resource/base/image/` | 模板匹配用的图片 |
| `resource/base/model/ocr/` | OCR 模型（`det.onnx` / `rec.onnx` / `keys.txt`） |
| `assets/` | 数据表（`adventure.json` 题库、`adventure_ui.json` 界面识别）与资源镜像 |
| `recordings/` | 录制文件（运行时生成） |
| `tools/` | 开发用：离线模拟器、上界求解、题库自检 |

## 免责声明

- 本项目仅供**学习与个人使用**，使用风险自负。
- 本项目**不对游戏进行任何注入和反编译**，仅通过截图与窗口消息模拟操作，
  但仍有可能被游戏判定为第三方工具，**存在封号风险**。
- 请遵守游戏的用户协议。因使用本项目导致的账号处罚、数据损失，作者不承担责任。
- 本项目**完全免费**。如果你是从任何渠道**付费**获得的，请立即申请退款，
  并向作者举报。

## 致谢

- [MaaFramework](https://github.com/MaaXYZ/MaaFramework) —— 本项目的基础框架，
  后台截图与窗口消息输入都来自它。
- [MFAAvalonia](https://github.com/SweetSmellFox/MFAAvalonia) —— 分发包使用的客户端。
- [造梦盒子（zmBox）](https://gitee.com/duskeye/zmBox) —— 本项目运行所依附的游戏客户端环境。
- [RapidOCR](https://github.com/RapidAI/RapidOCR)、[numpy](https://numpy.org/)、
  [pywin32](https://github.com/mhammond/pywin32)、[pynput](https://github.com/moses-palmer/pynput)
  —— 识别与输入所依赖的库。
- 以及造梦西游4 的玩家社区，攻略与题库整理都离不开大家。
