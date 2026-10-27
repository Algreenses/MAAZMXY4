# 脚本工具前端

本目录为现有 MFAAvalonia 增加原生“脚本工具”页面，沿用 Avalonia、SukiUI 和任务前端。页面内采用两个子标签：“脚本库与录制”和“时间轴编辑”。录制和编辑规则由 ZaoMengScript 迁移，Python 服务不依赖 Qt。此次交互更新及验收记录见 [更新记录](UX-CHANGELOG.md)。

用户操作说明见 [脚本工具使用说明](../docs/zh_cn/脚本工具-使用说明.md)。

## 源码与构建

`upstream.json` 固定与本机 Core 2.14.0 对应的 MFAAvalonia 提交、源码归档 SHA-256 和构建入口。`ScriptStudio/` 内的文件按 MFAAvalonia Core 项目目录布局存放。构建脚本将其复制到下载源码的 `MFAAvalonia/` 下，纯 C# `UserControl` 也可使用，不要求额外的 `.axaml` 文件。

需要 .NET 10 SDK；仅安装 .NET Runtime 无法编译。脚本优先使用 `.runtime-cache/dotnet-sdk/dotnet.exe`，也可通过 `-DotnetPath` 指定。

```powershell
# 下载、校验固定源码，应用补丁并复制页面源码，不编译。
powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_frontend.ps1 -PrepareOnly

# 构建到 dist/script-studio，不修改正在使用的前端。
powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_frontend.ps1

# 首次还原成功后，重复编译可以省去依赖还原。
powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_frontend.ps1 -NoRestore

# 关闭本项目的 MFAAvalonia 后安装；脚本先备份将被覆盖的文件。
powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_frontend.ps1 -Install
```

源码归档和解压目录位于 `.runtime-cache/`，编译产物位于 `dist/script-studio/`，均不提交 Git。默认构建产物是前端，不包含游戏资源项目、录制文件和内嵌 Python；运行时应与项目发布目录组合。`-Install` 只安装前端程序、托管依赖和对应 Win32 原生依赖，不覆盖 `interface.json`、`resource/`、`agent/`、`config/`、`recordings/` 或 Python 目录。

`upstream.json` 的 `application_icon` 指向仓库根目录 `新图标.ico`。构建时统一替换宿主的 `Assets/logo.ico`，供 Windows EXE 和 Avalonia 内嵌窗口/托盘图标使用；构建记录包含图标 SHA-256，不需要手工修改缓存源码。

新前端启动未绑定的脚本任务时，只在日志提示绑定方式并走宿主 `STOPPED` 路径，结束本次队列；不弹选择框、不执行回放、不计成功次数或继续重复。已绑定但文件丢失/损坏仍作为实际错误处理。协议通过 `report_unbound` 显式请求正常停止结果，旧调用仍保留严格校验。

安装备份位于 `backup/frontend-script-studio-日期-随机后缀/`。其中 `installation.json` 记录覆盖前已存在和新建的文件；需要恢复时，关闭前端，将备份文件按相同相对路径复制回项目。脚本不会主动关闭任何现有 MFAAvalonia 进程。

## 发布与手动升级

本次保留 `tools/build_update.py` / `updater.py` 的三项资源更新协议：`resource/`、`agent/`、`interface.json`。新增工作台必须使用本 PR 构建的前端，发布者需要把 `dist/script-studio` 中的前端与项目资源、Python 运行环境组合成完整客户端，通知旧用户重新下载后手动升级。CI 提供 `script-studio-frontend-win-x64` 构建附件，附件本身不含项目资源或 Python。

完整客户端的标准入口为 `MAA造梦西游4.exe`：启动器调用 `runtimes/python/pythonw.exe` 运行 `updater.py`，再启动 MFA 前端。`MFAAvalonia.exe` 是直接启动前端的入口，不能用它启动成功代替标准入口验证。本轮首个候选整包曾漏掉官方启动器，包内服务及直接启动前端的检查没有发现此问题；最终包必须重新检查完整启动链路。

完整包保留空的 `runtimes/win-x64/native/plugins/` 目录，供 MaaFramework 按其原有机制查找可选插件；该目录可以为空，不需要添加假插件。缺少目录时框架可能把目录路径当作动态库并输出加载错误。

启动器从[官方 v2.3.1 发布包](https://github.com/Inkyu-Zero/MAAZMXY4/releases/tag/v2.3.1)提取。原始发布文件 `MAAZMXY4.v2.3.1.exe` 的 SHA-256 为 `cf59d9470dd71cb2f0a4ad21cddcede25b55ca3fafde6d653f6475fdcf22a351`；其中 `MAA造梦西游4.exe` 的固定 SHA-256 为 `a0367870662916dc6abcaadf76b8321e3979a82b7c894d9d2004e3eb90197e46`。完整包通过 `--launcher` 显式指定这份启动器，不从个人运行目录猜测来源。

完整客户端还应包含 `script-studio-build.json`、`LICENSE-MFAAvalonia.txt`、`agent/recordings/` 和 `agent/task_asset_sync.py`，不得夹带发布者的 `recordings/`、`config/`、个人绑定、日志或缓存。根目录 `recordings/` 是用户库，与 `agent/recordings/` 发布模板分开；默认模板首次入库后不覆盖用户编辑，不恢复用户删除。

整包使用现有发布工具的新入口，统一从规范源码取资源，避免混入旧的根目录 `resource/`：

```powershell
./tools/build_frontend.ps1
python tools/build_release.py --client --launcher "<官方解压目录>/MAA造梦西游4.exe" --frontend "<编译输出目录>" --python-runtime "<完整内嵌Python目录>"
python tools/build_release.py --verify-client <完整包解压目录>
```

输出 `dist/MAAZMXY4-v<资源版本>-win-x64.zip`；不会覆盖已有同名包。`--frontend` 可指定 CI 前端附件的解压目录。启动器、前端、完整内嵌 Python、项目资源、依赖安装 BAT、更新器均被显式收集；预览目录中的配置、录制和日志不会进入整包。`--verify-client` 核对解压后的启动器、必需依赖、公共任务、选项及全部普通刷关任务的录制返回确认节点；BAT 保持 v2.3.1 的 CRLF 和首次 `/install` 行为。文件校验不能代替实际启动与游戏验收。禁止直接压缩个人运行目录代替这一步。

若发布单文件 EXE，可将上述完整 ZIP 包装成与官方 2.3.1 同类的 7-Zip 自解压包：

```powershell
python -B tools/build_sfx.py --client-zip "<完整客户端.zip>" --official-sfx "<官方v2.3.1自解压包.exe>" --seven-zr "<已核实的7zr.exe>" --seven-zip-license "<官方7-Zip license.txt>" --lgpl-license "<LGPL-2.1全文.txt>" --out "<新输出.exe>"
```

工具核对官方自解压包、前缀与 7zr 的固定 SHA-256，检查 ZIP 中的客户端和路径，附带 7-Zip、LGPL 许可与来源声明，再生成不覆盖已有文件的 EXE。官方许可来源为 [7-Zip](https://www.7-zip.org/license.txt) 和 [GNU LGPL 2.1](https://www.gnu.org/licenses/old-licenses/lgpl-2.1.txt)。该 EXE 是自解压包，不创建卸载项或快捷方式；实际发行前仍须从发布地址重新下载并验证图形解压和运行。

设置中的“前端升级说明”复用现有版本检测命令，提示下载本项目完整客户端；内部“更新软件”调用也会提示，防止通用 MFAAvalonia 更新覆盖定制页面。另须区分两条资源更新链路：外部 `updater.py` 只替换三项，不安装前端，也不执行旧脚本选择迁移；MFA 宿主内建资源更新通过 GitHub Release 选择资产，检测到核心程序文件后会尝试安装程序文件。后者没有被上述手动分发策略禁用，不能把它概括为“三项更新器”，也不能在尚未验证时宣称它已支持本次完整升级。首次引入工作台的版本按手动完整升级验收。

手动升级的迁移命令与文件覆盖顺序见[使用说明](../docs/zh_cn/脚本工具-使用说明.md#手动升级客户端)。必须在替换旧 `interface.json` 前迁移，单纯保留 `config/` 和录制文件不等于保留旧任务的脚本选择。

发布前应在隔离目录验证完整客户端；fork 试发布后重新下载实际 Release 资产，检查首次安装、旧版手动升级、启动器及用户数据。最终包的哈希、实际安装和远端 CI 各自取证，不以前一版本候选包的测试结果代替。是否提交上游 PR 由发布者决定。

当前 `tools/build_update.py` 的默认 Release 仓库、`updater.py` 的版本源及公共 `interface.json.github` 仍指向原仓库。fork 发布前逐项核实下载地址；不能直接把默认生成的上游 URL 当作 fork 下载地址，也不要在 fork 试发布时更新上游或共享 R2 清单。发布 `version.json` 时只填写实际生成包的地址、体积和 SHA-256；本地测试版本不能写成已经上线的发布清单。MFA 内建更新可能选中完整 `win-x64` ZIP，其升级能力需要在发布后的独立验证中另行确认。

## 宿主接入

`patches/` 使用精确文本补丁，每个补丁包含目标路径、旧文本和新文本。脚本要求旧文本只出现一次；补丁已应用时跳过，锚点变化时停止，不尝试模糊修改其它版本源码。

- `01-script-studio-navigation.json`：在共享 `RootViewContent.axaml` 的现有侧栏加入页面。
- `02-script-studio-registration.json`：接入现有 DI 和 ViewLocator 映射。
- `03-script-studio-task-guard.json`：在两个任务启动入口共用的 `TryBeginTaskRun()` 内，与录制端使用同一把锁，防止录制与界面、定时或其它实例启动任务并行。冲突通过原有 Toast 与日志反馈。
- `04-selected-win32-controller.json`、`05-selected-controller-resource.json`：有多个 Win32 配置时，窗口、资源及任务使用当前命名控制器的筛选规则，支持同类型控制器使用不同窗口与资源设置。
- `06-task-script-execution.json`：在现有 MFA 任务队列入口查询通用任务绑定，保留任务内回放或明确替代整个任务；支持配置声明的有界恢复，失败与取消停止后续重复。`07-win32-recovery-rebind.json` 在重启后绑定经过校验的唯一窗口，避免旧设备变更回调与显式重连竞态。
- `08-task-stop-race.json`：停止操作捕获同一执行器引用，避免恢复释放执行器时出现空引用竞态。
- `09-agent-callbacks-only.json`、`10-agent-connection-health.json`：实时预览使用仅资源控制器绑定，并在运行前检查闲置 Agent 连接，避免截图事件堵塞任务启动。
- `11-script-studio-icons.json`：通过现有 Avalonia 资源系统加载 imagegen 生成的工具栏图集，提示词与 PNG 同目录保存。
- `12-option-default-array.json`、`13-option-default-string.json`：字符串形式的选项先保留未选择状态，由现有 TaskLoader 应用 `default_case`，修复默认值总落到第一项的问题。已有配置对象的明确索引保持不变。
- `14-shared-recording-windows.json`：录制页复用当前任务页的窗口发现、控制器筛选与已选窗口顺序，适配通用 Win32 控制器；刷新只读窗口信息，不连接或切换设备。
- `15-manual-frontend-version-check.json`、`16-manual-frontend-download.json`：前端按项目完整客户端手动分发，阻止通用前端更新覆盖工作台；资源更新逻辑保持原样。
- `17-manual-frontend-help.json`：在软件版本旁提供“前端升级说明”，复用已有命令展示升级方式。

默认录制包位于 `agent/recordings`。`recording_store.py` 负责首次入库、默认标记、复制、带恢复副本的删除与绑定原子更新；编辑和回放仍使用原有录制模型及文件。`agent/task-migrations.json` 声明退役入口，完整任务同步只移除名称和入口均匹配的旧条目，无关自定义任务保留。

任务目录由 `agent/task_catalog.py` 从现有 `interface.json`、导入定义与实例任务发现。普通任务标识由名称和入口稳定生成，支持 Pipeline v1/v2 的 `battle_play` 节点；旧绑定标识可在任务初始化参数的 `execution.binding_id` 中声明兼容。UI 和 C# 不内置副本白名单。

复杂任务通过 `script_task_state`、`execution.resume_entry`、`execution.settings_node`、`playback_node`、`routes` 和恢复入口的 `resume_slots` 声明运行与恢复能力。识别节点仍由各任务 Pipeline 定义。任务查询复用现有脚本服务协议，执行仍使用 Maa 控制器和任务队列。任务资源安装使用 `tools/sync_task_assets.py --task <任务名> --apply`，默认只预览。

宿主互斥接口由页面源码中的 `MFAAvalonia.Services.ScriptStudioGuard` 提供：`SyncRoot`、`IsCapturing` 和 `ThrowIfCapturing()`。录制端必须在同一把锁内检查所有实例的任务状态并取得录制所有权，停止及异常时释放。

上游源自 [MaaXYZ/MFAAvalonia](https://github.com/MaaXYZ/MFAAvalonia/tree/1080d3e1500f3439ac896ab2eca9aeb8e885f1f4)，保留上游依赖版本，未对其进行无关升级。构建产物附带 `LICENSE-MFAAvalonia.txt` 和 `script-studio-build.json`，用于记录上游许可与源码版本；本目录的补丁与页面源码随本工程提供。
