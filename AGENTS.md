# MAAZMXY4：工程地图与协作约束

适用范围：本仓库及其子目录。根据截至 2026-10-27 的实现和实际故障整理。
先用本文定位模块和验证入口，避免每次从头扫描全库；修改相关模块时仍须阅读对应实现。用户当次明确要求优先，历史报告中的旧计划、旧版本和旧 PR 状态不代表当前指令。

## 1. 开始工作前

- 确认真正的 Git 根目录。工作空间的父目录、`MAAZMXY4` 工程、相邻的 `ZaoMengScript`、`dist/` 预览和 worktree 是不同位置，不能混用。
- 先检查 `git status --short --branch`、`git remote -v`。当前协作关系是 `origin = Algreenses/MAAZMXY4`（fork）、`upstream = Inkyu-Zero/MAAZMXY4`（原仓库）；推送前重新核实，默认只向已授权的 fork 功能分支推送。
- 用户工程、正在运行的客户端和源码可能位于同一目录。编辑源码、构建产物、安装前端、发布资源是四个不同动作，完成状态必须分别说明。
- 优先复用现有前端、控制器、任务队列、脚本模型、迁移和安装机制。不要因为最先实现的是守卫宝烛，就在公共 UI 或服务中增加副本名称白名单和专用执行分支。
- `ZaoMengScript` 是录制数据和编辑规则的迁移来源，不是运行依赖。禁止重新引入原工程 Qt/Python 任务桥接、OCR 环境或外部助手启动要求。

## 2. 当前架构与模块入口

### 技术栈

- 桌面前端：MFAAvalonia，C# / .NET 10、Avalonia、SukiUI，沿用宿主 MVVM、依赖注入和页面导航。
- 前端以固定上游源码归档、SHA-256、精确文本补丁和源码覆盖层构建。唯一构建清单是 [`frontend/upstream.json`](frontend/upstream.json)，当前固定上游提交为 `1080d3e1500f3439ac896ab2eca9aeb8e885f1f4`，目标为 `win-x64`。依赖版本跟随清单及固定源码，不凭经验升级。
- 自动化底座：MaaFramework 的 Win32 控制器、Pipeline、模板识别、OCR、Agent 与任务队列。本项目公开任务目前面向 Windows 游戏窗口。
- 后端：Python。客户端通过 `runtimes/python/python.exe` 启动；CI 使用 Windows + Python 3.12，本地还验证过 Python 3.13。不要把本机默认 `python` 当作 CI 的相同解释器。
- 项目资源版本、MFAAvalonia 软件版本、MaaFramework 版本是不同版本号。界面显示差异不等于安装失败，应逐项核实。

| 模块 | 职责与定位 |
| --- | --- |
| `assets/interface.json` | 公共任务、选项、资源、控制器、Agent 启动方式及项目资源版本的源码定义 |
| `assets/resource/base/pipeline/`、`image/` | 任务流程、识别模板、场景确认、失败与返回路线 |
| `frontend/ScriptStudio/Views/Pages/`、`ViewModels/Pages/`、`Controls/` | 脚本库、录制、编辑页面、时间轴及交互；不承担独立的游戏任务调度 |
| `frontend/patches/` | 向固定宿主接入导航、注册、任务互斥、窗口复用、Agent 健康检查、选项默认值及手动升级策略 |
| `ScriptStudioClient.cs`、`agent/script_studio_service.py` | 前端拥有的脚本工作进程，使用 stdin/stdout JSON Lines 请求/响应，管理编辑会话与录制状态 |
| `agent/script_studio/model.py` | 无 GUI、Hook、Maa 依赖的纯录制模型：校验、导入、配对编辑、移动、拆分、序列化 |
| `agent/script_studio/capture.py` | Windows 录制监听、计时、焦点、暂停/继续倒计时、停止及快捷键状态机 |
| `agent/recording_store.py`、`script_library.py` | 默认模板首次入库、复制/删除、恢复副本、文件路径校验、绑定索引、锁和版本检查 |
| `agent/task_catalog.py` | 从 interface、导入定义及实例任务发现能力；统一解析 Pipeline 链接、参数和执行计划 |
| `ScriptTaskExecution.cs`、`TaskScriptPlan.cs` | 查询绑定及当前实例覆盖参数，构造 Maa 原生回放节点或覆盖；复用原任务执行入口 |
| `agent/main.py`、`battle_player.py`、`script_task.py` | Maa Agent 注册、自定义回放、原生任务阶段与状态文件；与编辑服务是不同的进程职责 |
| `TaskRecovery.cs`、`TaskRecoveryPolicy.cs` | 已声明任务的恢复预算、进程身份核验、重连、已有存档恢复和同轮重试 |
| `agent/adventure.py`、`adventure_logic.py` | 已有生存大冒险答题、事件匹配及决策；不是脚本工作台的重写目标 |
| `agent/task_asset_sync.py`、`legacy_script_bindings.py` | 公共任务及旧选项迁移、备份和回滚；`tools/sync_task_assets.py` 仅复用此实现作为源码入口 |
| `tools/migrate_zaomeng_scripts.py` | 只转换原工程录制数据，维护本地迁移清单，不迁移原任务运行环境 |

上述 C# 服务位于 `frontend/ScriptStudio/Services/`。Maa Agent 在导入 `maa` 前设置原生库位置；不要把编辑服务与 Maa Agent 的启动参数或通信协议混为一谈。

### 三条主要链路

```text
编辑/录制：ScriptStudioView → ViewModel → ScriptStudioClient → JSONL 服务 → 模型/录制器/文件库
任务执行：宿主队列 → ScriptTaskExecution → execution_plan（当前实例 overrides）→ TaskScriptPlan → Maa Pipeline/Agent
任务恢复：声明的 execution profile → TaskRecovery → 身份核验/重连 → 恢复入口 → 原轮次重试
```

## 3. 已有功能及必须保持的行为

- 工作台有“脚本库与录制”“时间轴编辑”两个子标签；时间轴可缩放、适合窗口、最大化，参数区宽度可调。不要再把所有面板挤回同一个页面。
- 脚本支持新建、导入、编辑、首次保存命名、另存为、复制、删除、导出、撤销重做、标记和拆分。取消命名或弹窗不得丢失草稿，关闭弹窗后快捷键焦点应恢复。
- 默认包当前含 7 份迁移录制和 1 份格式示例，清单为 `agent/recordings/manifest.json`。入库后与用户脚本使用同一编辑模型；不得因更新而覆盖编辑或复活已删除脚本。
- 一个脚本可绑定多个任务。`pipeline` 模式保留任务流程，只替换回放节点；`replace` 模式明确替代整个任务，不应混淆这两种语义。
- 任务能力由配置和 Pipeline 发现，库中可汇总已保存实例的能力；真正执行时必须使用当前实例实际传入的覆盖参数，不能借用其他实例的参数。
- 录制开始可倒计时，失焦自动暂停，点击继续后有 3 秒倒计时；暂停和继续倒计时不计入有效录制时长。F8/F9/F10 的具体行为以录制状态机及使用说明为准。
- 任务运行时允许首次打开脚本库、刷新列表和枚举窗口；编辑、绑定、文件修改和录制遵守现有互斥。任务开始入口与录制端共用 `ScriptStudioGuard`，包括定时和多实例入口。
- 录制器忽略自动化注入的键鼠事件。模拟按键没有被录入，不能直接判定实体快捷键坏了，也不能据此宣称人工快捷键已通过验收。
- 守卫宝烛是原生流程：确认目标、进关、确认就绪、回放、确认返回后才完成一轮。重复次数由宿主队列控制，避免再叠加一层任务内部重复循环。
- “录制内容是否包含返回地图”决定使用录制的返回动作还是任务自身退关；必须保留返回后的确认，不得重复退关或仅凭回放结束判定任务成功。
- 自动恢复默认关闭，只处理已确认的故障、核验过的目标进程与已配置的已有存档。验证码、未知场景、空存档、共享进程冲突、身份不匹配或取消都应停止，不能盲目重启或无限重试。

## 4. 源码、发布资源与用户数据必须分开

| 位置 | 约定 |
| --- | --- |
| `assets/interface.json`、`assets/resource/` | 公共源码；发布到根目录 `interface.json`、`resource/` |
| `agent/` | 随资源包发布的代码和只读模板；更新器会整体替换，禁止在此存用户录制和绑定 |
| `agent/recordings/`、`agent/task-migrations.json` | 随包模板和迁移声明，不能移回仅源码可见的目录 |
| 根目录 `recordings/` | 用户库，含 `.bindings.json`、`.defaults.json`、选择信息、草稿/恢复副本等；不提交或打包 |
| `config/`、`appsettings.json`、`.local-migrations/` | 用户配置、实例和数据迁移状态；保留，不作为公共默认配置分发 |
| 根目录 `interface.json`、`resource/` | 当前运行副本；可能含用户自定义内容，不得直接当作公共源码提交 |
| `.runtime-cache/`、`dist/` | 上游源码缓存、构建和预览产物；缓存中修改不构成源码修复，预览目录可能带测试或个人数据 |
| `backup/`、`debug/`、`logs/`、日志 ZIP | 本地备份和诊断；发布前不能误打包或 `git add` |

已有冒险功能仍可能使用 `assets/data/`。新录制功能不依赖 `assets/`，不代表可以一概删除发布目录的所有 `assets` 内容；逐个核对既有读取者和打包约定。

### 当前发布决定

- `tools/build_update.py` 与 `updater.py` 的资源更新协议仅处理 `resource/`、`agent/`、`interface.json`。
- 本项目目前通过重新下载完整客户端手动升级前端。未经新的明确要求，不顺手设计新的更新协议、启动器或自动前端升级机制。
- 把 EXE/DLL 或新版 `updater.py` 塞进现有 ZIP，不会让旧更新器安装它们。资源升级成功不能宣称前端升级成功。
- `dist/script-studio` 和 CI 的前端附件不是完整客户端，发布者还需组合项目资源与 Python 等运行依赖。打包使用明确文件范围，不能直接压缩曾用于预览的整个目录。
- 保留 `FrontendUpdatePolicy` 及对应补丁，防止通用 MFAAvalonia 更新覆盖定制页面；“前端升级说明”复用现有命令。
- `version.json` 描述已发布产物，必须来自真实包的 URL、大小和 SHA-256。源码版本递增不意味着可以编造新发布清单或沿用旧包哈希。

## 5. 历史故障对应的硬约束

### 发布包缺文件、用户打开仍是旧界面

- 修改资源路径时同时核对读取者、`build_update.py`、安装/同步工具、预览和 CI。
- 使用真实打包文件列表生成 ZIP，在隔离目录解压并运行包内服务。默认脚本检查必须在没有源码 `assets/recordings` 的目录通过。
- 预览脚本额外复制文件曾掩盖正式包漏包，禁止只凭源码运行或预览成功确认发布有效。
- 源码修改、编译成功和安装成功分别取证。安装后核对实际 EXE/DLL 路径、构建记录及文件哈希；不能把 worktree 中的结果当成用户原目录已更新。

### 任务标识、旧选择丢失和非通用绑定

- `task_key(name, entry)` 生成的是名称和入口的稳定哈希，不是硬件/机器 ID；不能仅看到 `task-…` 就下结论，也不能把个人索引放进包里“修好”它。
- 任务重命名或入口变化可能改变 ID，必须考虑迁移/稳定别名。现有 `execution.binding_id` 是声明式兼容入口，不在 UI 中硬编码具体任务。
- `@task:` 引用要求明确有效的绑定；新前端用 `report_unbound` 请求结构化停止结果，未绑定只记录提示并通过宿主 `STOPPED` 结束队列，不弹选择框、不计成功、不继续重复。底层严格解析和已绑定文件的损坏检查仍保留。已有明确文件引用的自定义任务应保留原行为，不能强制全部重新绑定。
- 删除旧选项前迁移其含义：保留选中的录制、已有绑定、重复次数、勾选状态、控制器及无关自定义字段。不能只按新选项名称筛选后把旧选择丢弃。
- 明确文件丢失不得偷偷换成最新录制；不同实例存在不同旧选择时不得任意选一个共享绑定。旧 `@latest` 需要在默认模板入库前解析，避免误选刚入库的模板。
- 迁移先规划和校验，再备份、写入、回滚。保留旧 `interface.json` 到旧选项解析完成；默认不覆盖控制器等元数据，完整升级通过 `--update-version` 明确同步资源版本。

### 文件安全与默认脚本初始化

- 复用已有路径校验、锁、原子写入和修订哈希。拒绝越界路径、危险文件名、非有限数值及超限数据，不通过放松校验让测试变绿。
- 同名用户文件即使是坏 JSON、非对象或异常编辑元数据也应保留；默认模板另取不冲突的文件名。
- 损坏的默认登记信息应在库中给出诊断，不得让整个编辑服务退出、隐藏全部个人脚本或阻止无关原生任务。禁止静默重置坏索引。
- 已有绑定没有变化就不重写索引。删除默认脚本保留删除状态和恢复副本；回滚只清理本次创建且未被他人改写的内容，不能删除并发写入者的文件。
- 录制磁盘格式保持版本 1、相对 `actions/t` 和 `base`；编辑模型内部使用绝对时间。保留未知字段、动作配对和时间语义。原尺寸录制通过模型转换为短边 720，不能只修改 `base` 数字。
- 单轮执行及恢复重试固定脚本文件和修订哈希；下一轮才可采用新版。另存为不能悄悄改变原任务绑定。

### UI、Pipeline 与服务生命周期

- 读操作不能复用写操作互斥；页面仅在加载成功后标记初始化完成，失败后必须可重试。空列表要先区分磁盘数据、服务响应、读取被拦截和界面未更新。
- 复用任务页的窗口发现、当前命名控制器筛选及选择顺序。不要另写一套旧窗口标题/类名匹配，也不能取第一个同类型控制器。
- 倒计时显示、暂停时长和继续行为以状态机快照为准，至少检查开始倒计时、失焦、点击继续、再次失焦、停止和异常退出。
- 库列表、时间轴、参数区、保存弹窗、图标和快捷键需要实际页面检查，包含窄窗口；编译成功或图标文件生成不等于 UI 正确。
- Pipeline 发现和依赖复制复用 `node_links()`；兼容普通字符串、`[JumpBack]`、声明的 `[Anchor]` 及对象链接，不能只处理裸节点名。
- 复用 `custom_parameters()` 处理 v1/v2 及文件名/JSON 字符串参数；不能无条件对 `custom_action_param` 调用 `.get()`。选项字符串交给宿主按 `default_case` 初始化，不默认选第 0 项覆盖用户选择。
- JSONL stdout 只写完整协议帧，诊断走 stderr。保留请求 ID、顺序和超时检查。
- EOF、断管、损坏响应、超时和工作进程退出必须统一断线并释放录制互斥；不能悄悄启动空服务，让 UI 仍显示旧草稿或正在录制。业务校验错误不应销毁整个会话。
- 临时任务查询客户端不拥有录制会话，退出时不能清除另一个录制客户端的互斥；保留 `releaseCaptureOnDisconnect` 的所有权区分。

### 启动一直转圈、首帧警告和鼠标迟滞

- “进程存在”不等于 Agent 通道可用。历史故障是高频预览事件响应积压，`alive()` 失效，重启仅清空队列。
- 复用 Resource 绑定自定义动作/识别回调，保持 IPC/TCP 和反向调用；没有需求不要重新订阅全部控制器与任务事件。
- 仅在空闲执行器复用前检查 Agent 健康并走既有重建流程，不能重建正在执行/停止的任务。跨进程长预览后调用动作的检查不能被同进程 Mock 替代。
- 首帧缓存为空的历史 FramePool 警告不能证明当前仍截图失败；先查看当前画面与后续帧。持续为空时重新排查，不能用首帧解释掩盖真实故障。
- 真实鼠标偶发迟滞仍未定位唯一根因。不要把队列修复、CPU 采样或一次正常运行写成“鼠标卡顿已解决”，也不要擅自改系统鼠标速度、抢占输入模式或删录制动作。

### CI 的 Windows 短路径问题

- CI 曾因 `TEMP` 中的 8.3 别名导致 5 项测试失败。程序已解析完整路径，而测试使用原始路径比较，故障注入条件未命中。
- 与解析后路径比较的测试夹具，应先对临时根目录调用 `Path(...).resolve()`。不要用字符串替换猜测别名，不要为了匹配测试取消生产路径保护。
- 故障注入必须断言它确实触发，例如注入次数、命中的文件和第几次写入；不能只断言最终错误或成功状态。
- 保留 `tests/test_windows_temp_paths.py`：真实短路径作为子进程 TEMP/TMP，运行对应故障注入，不更改系统环境，不依赖符号链接权限。
- 不把 CI 失败直接归因于 Python 版本或 GitHub 环境。先检查准确提交与失败堆栈，再复现环境差异；不要删测试、跳过失败断言或只重跑碰运气。

## 6. 按改动范围选择验证

| 改动 | 首选回归入口 |
| --- | --- |
| 默认脚本、文件库、发布布局 | `tests/test_recording_store.py`、`test_release_compatibility.py`、`test_release_package.py` |
| 绑定、任务能力、旧实例迁移 | `tests/test_task_catalog.py`、`test_script_bindings.py`、`test_legacy_script_bindings.py`、`test_task_asset_sync.py` |
| 原录制格式迁移、回滚、并发写入 | `tests/test_script_migration.py`、`test_windows_temp_paths.py` |
| 录制、编辑与草稿 | `tests/test_script_studio_capture.py`、`test_script_studio_model.py`、`test_script_studio_service.py` |
| 回放、原生任务 | `tests/test_battle_player_studio.py`、`test_bounty_candle.py` |
| 页面生命周期与控制器复用 | `frontend/tests/StudioPage/`，加相关实际页面操作 |
| 工作进程及 JSONL 异常 | `frontend/tests/StudioClient/` 的真实测试子进程故障注入 |
| 任务规划与恢复策略 | `frontend/tests/NativeRecovery/` |
| 原生通信或识别改动 | 按需启用 `tests/test_agent_preview_transport.py`、`test_bounty_candle_native.py` 的原生检查 |

所有 Python 测试路径均相对 `tests/`。常用命令从仓库根目录执行，先核对解释器版本：

```powershell
python --version
python -X utf8 -m unittest discover -s tests -p "test_*.py" -v
python -X utf8 -m unittest discover -s tests -p test_windows_temp_paths.py -v
dotnet run --project frontend/tests/NativeRecovery/NativeRecovery.csproj -c Release
dotnet run --project frontend/tests/StudioPage/StudioPage.csproj -c Release
dotnet run --project frontend/tests/StudioClient/StudioClient.csproj -c Release
./tools/build_frontend.ps1
```

- `dotnet` 不在 PATH 时可使用 `.runtime-cache/dotnet-sdk/dotnet.exe`；构建支持 `-DotnetPath`。首次必须还原依赖，只有还原成功后才使用 `-NoRestore`。
- 应用图标由 `frontend/upstream.json` 的 `application_icon` 指定，当前为根目录 `新图标.ico`；构建统一应用到 EXE 和内嵌窗口/托盘图标。验证编译资源及构建记录，不凭资源管理器的旧图标缓存判断失败。
- 原生测试默认按环境跳过。需要时用内嵌 Python，并在测试子进程启用 `MAA_AGENT_NATIVE_TESTS=1` 或 `CANDLE_NATIVE_TESTS=1`；记录实际运行及跳过项目。
- 修复有稳定复现的回归时，先看到针对性检查在旧实现失败，再验证修复通过。用户要求手动测试时，仍做必要的相关检查，并明确未自动验证的部分。
- 文档修改无需跑整套应用测试。相关检查通过后，除非有新改动、失败或未解决的疑点，不重复扩大测试。
- 实际游戏验证使用隔离配置和明确的单轮，不能改用户的长期重复次数。启动应用前检查自动启动设置，不能为了检查页面就意外运行已勾选的长任务。

## 7. 安装、Git、PR 和结果汇报

- 合并上游按任务节点和字段检查等待、超时、错误回退及循环语义，不能整文件选择 ours/theirs；保留上游有效改动与本分支能力，检查有无重复循环和退役入口复活。
- 已获授权的本地安装先确认正确目录的助手已停止，再备份将覆盖的程序及用户数据。保留脚本、绑定、次数、勾选及控制器；安装后比较构建哈希和受保护文件。不能把关闭用户任务作为默认清理动作。
- 使用 `tools/build_frontend.ps1 -Install` 安装前端，任务同步使用已有迁移工具。前端安装不会自动同步资源，资源同步不会安装前端，两步不能漏报。
- worktree 清理前确认归属、未提交/未推送内容、被忽略的个人文件和恢复备份；目录移除前核实绝对路径。Git 注销成功不代表长路径目录已删除。遇到删除失败或审批拒绝应如实报告，不改换危险命令绕过。
- 提交前审阅暂存文件，特别防止个人日志 ZIP、录制、配置、私有插件和构建产物进入提交。不要用无审阅的全量暂存代替检查。
- PR 标题和说明描述最终实现、发布方式、验证和边界，不堆积对话历史或把未执行的检查写成已通过。模板引用文件不存在时明确说明，不能虚假勾选已阅读。
- 用户要求提交前通知时先给出准备完成的结果；已有授权继续有效，不重复请求同一许可。创建或更新 PR 后核实目标仓库、分支及提交；不因截图中的他人发言擅自给维护者发消息。
- CI 必须检查当前 PR 的准确 head。使用界面指定的账号读取检查，后续日志读取保持对应 `headRevision`，不要拿旧提交结果证明新提交。
- 分开报告本地测试、构建、预览、原生测试、实机运行、安装核对和远端 CI。`action_required`、没有检查或跳过都不是通过；`failure` 要看具体失败步骤。静态截图/内存控制器通过不代表真实关卡或自动重启全部通过。
- 只有实际实现、相关验证和已授权交付完成后才报告完成；使用“确认的问题／已复现／待验证”区分证据，不承诺“所有可能的 bug 已清除”。

## 8. 持续维护入口

- 构建、补丁、发布约定：[frontend/README.md](frontend/README.md)
- 功能、录制和手动升级：[脚本工具使用说明](docs/zh_cn/脚本工具-使用说明.md)
- 发布目录、迁移、进程断线、CI 短路径：[兼容性审计](docs/zh_cn/PR撤回后兼容性审计.md)
- Agent 积压、鼠标迟滞、首帧日志：[启动卡住与鼠标迟滞排查报告](docs/zh_cn/启动卡住与鼠标迟滞排查报告.md)
- 上游字段合并和真实单轮验证：[上游合并说明](docs/zh_cn/上游2.2冲突处理说明.md)
- UI 行为和历史验收：[frontend/UX-CHANGELOG.md](frontend/UX-CHANGELOG.md)

修复新的同类故障时，更新对应回归和本文件的规则/入口。不要把不断变化的测试数量、最新 PR 状态或安装备份路径写成永久保证；以当次工具结果为准。
