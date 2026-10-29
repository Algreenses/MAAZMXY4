# 脚本工具前端

本目录为现有 MFAAvalonia 增加原生“脚本工具”页面，沿用 Avalonia、SukiUI 和任务前端。页面内采用两个子标签：“脚本库与录制”和“时间轴编辑”。录制和编辑规则由 ZaoMengScript 迁移，Python 服务不依赖 Qt。此次交互更新及验收记录见 [更新记录](UX-CHANGELOG.md)。

用户操作说明见 [脚本工具使用说明](../docs/zh_cn/脚本工具-使用说明.md)。

下一版本另有原生“游戏数据库”侧栏页，数据从 `agent/data/game_database/` 离线读取。相关来源、布局、开发工具与验收边界见[游戏数据库与导航素材](../docs/zh_cn/游戏数据库与导航素材.md)。它需要本轮定制前端与相应资源一起安装，单独更新资源不会给旧前端增加页签。

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

## 发布与统一更新

2.3.8 正在引入统一更新器，发布与兼容边界以[更新协议与发布规范](../docs/zh_cn/更新协议与发布规范.md)为准。小更新仍只修改 `resource/`、`agent/`、`interface.json`；完整更新使用与 EXE 同源的客户端 ZIP。旧客户端必须先手动安装一次新版完整客户端，旧更新器不能安装新的前端和更新引擎。CI 提供的 `script-studio-frontend-win-x64` 仍只是前端附件，不含完整运行依赖；源码构建通过不等于已完成实机验收。

完整客户端的标准入口为 `MAA造梦西游4.exe`：启动器调用 `runtimes/python/pythonw.exe` 运行 `updater.py`，再启动 MFA 前端。`MFAAvalonia.exe` 是直接启动前端的入口，不能用它启动成功代替标准入口验证。本轮首个候选整包曾漏掉官方启动器，包内服务及直接启动前端的检查没有发现此问题；最终包必须重新检查完整启动链路。

完整包保留空的 `runtimes/win-x64/native/plugins/` 目录，供 MaaFramework 按其原有机制查找可选插件；该目录可以为空，不需要添加假插件。缺少目录时框架可能把目录路径当作动态库并输出加载错误。

启动器现在从 `tools/launcher/Program.cs` 构建。正常入口仍调用内嵌 Python 和 `updater.py`；若存在待恢复事务，则先启动独立复制的恢复运行时，避免依赖正在替换的 Python DLL。`tools/build_launcher.ps1` 使用 Windows .NET Framework 编译器及项目图标，生成 EXE 和 `launcher-build.json`，打包核对源码、图标及二进制摘要。不能再把旧 v2.3.1 启动器混入统一更新客户端。手动自解压 EXE 的 7-Zip 外壳继续复用原有固定来源和校验规则。

完整客户端还应包含 `script-studio-build.json`、`LICENSE-MFAAvalonia.txt`、`agent/recordings/` 和 `agent/task_asset_sync.py`，不得夹带发布者的 `recordings/`、`config/`、个人绑定、日志或缓存。根目录 `recordings/` 是用户库，与 `agent/recordings/` 发布模板分开；默认模板首次入库后不覆盖用户编辑，不恢复用户删除。

整包使用现有发布工具的新入口，统一从规范源码取资源，避免混入旧的根目录 `resource/`：

```powershell
./tools/build_frontend.ps1
./tools/build_launcher.ps1 -Version 2.3.8
python tools/build_release.py --client --launcher "dist/launcher/MAA造梦西游4.exe" --frontend "<编译输出目录>" --python-runtime "<完整内嵌Python目录>" --repository "<发布者>/<仓库>"
python tools/build_release.py --verify-client <完整包解压目录>
```

输出 `dist/MAAZMXY4-v<资源版本>-win-x64.zip`；不会覆盖已有同名包。`--frontend` 可指定 CI 前端附件的解压目录。启动器、前端、完整内嵌 Python、项目资源、依赖安装 BAT、更新器均被显式收集；预览目录中的配置、录制和日志不会进入整包。`--verify-client` 核对解压后的启动器、必需依赖、公共任务、选项及全部普通刷关任务的录制返回确认节点；BAT 保持 v2.3.1 的 CRLF 和首次 `/install` 行为。文件校验不能代替实际启动与游戏验收。禁止直接压缩个人运行目录代替这一步。

若发布单文件 EXE，可将上述完整 ZIP 包装成与官方 2.3.1 同类的 7-Zip 自解压包：

```powershell
python -B tools/build_sfx.py --client-zip "<完整客户端.zip>" --official-sfx "<官方v2.3.1自解压包.exe>" --seven-zr "<已核实的7zr.exe>" --seven-zip-license "<官方7-Zip license.txt>" --lgpl-license "<LGPL-2.1全文.txt>" --out "<新输出.exe>"
```

工具核对官方自解压包、前缀与 7zr 的固定 SHA-256，检查 ZIP 中的客户端和路径，附带 7-Zip、LGPL 许可与来源声明，再生成不覆盖已有文件的 EXE。官方许可来源为 [7-Zip](https://www.7-zip.org/license.txt) 和 [GNU LGPL 2.1](https://www.gnu.org/licenses/old-licenses/lgpl-2.1.txt)。该 EXE 是自解压包，不创建卸载项或快捷方式；实际发行前仍须从发布地址重新下载并验证图形解压和运行。

现有版本更新页改为项目统一更新页，展示客户端/资源版本、渠道、来源和更新说明。检查、下载与安装分开；任务或录制运行时不能安装。MFA 的软件、资源、框架更新及本地包入口均转到项目流程，禁止通用 MFA 包覆盖定制前端。完整包导入只接受当前发布清单匹配的 ZIP，支持显式检查同版本完整修复包。启动器与前端使用同一 `update_engine`；旧 `updater.py` 中保留的兼容函数不再作为新版入口。

手动升级的迁移命令与文件覆盖顺序见[使用说明](../docs/zh_cn/脚本工具-使用说明.md#手动升级客户端)。必须在替换旧 `interface.json` 前迁移，单纯保留 `config/` 和录制文件不等于保留旧任务的脚本选择。

发布前应在隔离目录验证完整客户端；fork 试发布后重新下载实际 Release 资产，检查首次安装、旧版手动升级、启动器及用户数据。最终包的哈希、实际安装和远端 CI 各自取证，不以前一版本候选包的测试结果代替。是否提交上游 PR 由发布者决定。

新版更新源在打包时生成的 `update-config.json` 中，默认正式源为所选仓库 Release 的 `client-version.json` 附件；R2/CDN 可通过重复 `--manifest-url` 配置，但必须发布相同协议和序号的清单。fork 候选必须显式传 `--repository Algreenses/MAAZMXY4 --channel preview --manifest-url <独立候选清单地址>`，不修改上游或共享 R2。资源包由 `build_update.py` 默认生成协议 2 的清单内置包；旧 `version.json` 仅在显式 `--legacy` 时生成，不将新资源推给不兼容旧客户端。

用 `tools/build_update_manifest.py --client <完整ZIP> --resource <资源ZIP> --repository <发布仓库> --tag <实际标签> --sequence <递增序号> --channel <stable或preview> --out <新清单路径>` 从最终资产生成候选清单。它校验包内摘要，只写本地文件，不上传；正式启用前先完成审核、授权实机、真实资产下载复验。旧清单与新清单相互独立，构建版本增加不能视为线上已发布。

## 宿主接入

`patches/` 使用精确文本补丁，每个补丁包含目标路径、旧文本和新文本。脚本要求旧文本只出现一次；补丁已应用时跳过，锚点变化时停止，不尝试模糊修改其它版本源码。

- `01-script-studio-navigation.json`：在共享 `RootViewContent.axaml` 的现有侧栏加入页面。
- `02-script-studio-registration.json`：接入现有 DI 和 ViewLocator 映射。
- `03-script-studio-task-guard.json`：在两个任务启动入口共用的 `TryBeginTaskRun()` 内，与录制端使用同一把锁，防止录制、盒子重连与界面、定时或其它实例启动任务并行。冲突通过现有日志反馈。
- `04-selected-win32-controller.json`、`05-selected-controller-resource.json`：有多个 Win32 配置时，窗口、资源及任务使用当前命名控制器的筛选规则，支持同类型控制器使用不同窗口与资源设置。
- `06-task-script-execution.json`：在现有 MFA 任务队列入口查询通用任务绑定，保留任务内回放或明确替代整个任务；支持配置声明的有界恢复，失败与取消停止后续重复。`07-win32-recovery-rebind.json` 在重启后绑定经过校验的唯一窗口，避免旧设备变更回调与显式重连竞态。
- `08-task-stop-race.json`：停止操作捕获同一执行器引用，避免恢复释放执行器时出现空引用竞态。
- `09-agent-callbacks-only.json`、`10-agent-connection-health.json`：实时预览使用仅资源控制器绑定，并在运行前检查闲置 Agent 连接，避免截图事件堵塞任务启动。
- `11-script-studio-icons.json`：通过现有 Avalonia 资源系统加载 imagegen 生成的工具栏图集，提示词与 PNG 同目录保存。
- `12-option-default-array.json`、`13-option-default-string.json`：字符串形式的选项先保留未选择状态，由现有 TaskLoader 应用 `default_case`，修复默认值总落到第一项的问题。已有配置对象的明确索引保持不变。
- `14-shared-recording-windows.json`：录制页复用当前任务页的窗口发现、控制器筛选与已选窗口顺序，适配通用 Win32 控制器；刷新只读窗口信息，不连接或切换设备。
- `15-manual-frontend-version-check.json`、`16-manual-frontend-download.json`：前端按项目完整客户端手动分发，阻止通用前端更新覆盖工作台；资源更新逻辑保持原样。
- `17-manual-frontend-help.json`：在软件版本旁提供“前端升级说明”，复用已有命令展示升级方式。
- `36-game-management-card.json`、`37-task-description-in-settings.json`：游戏管理替换原任务说明卡片，原 Markdown 说明控件移入任务设置，保留布局标识和绑定。
- `38-disable-first-use-tutorial.json`：以功能开关暂时跳过首次指引，不改写用户的完成标记。
- `39-game-management-lifetime.json`、`40-game-reconnect-preview.json`：实例释放时结束监测，重连期间沿用实时预览的连接暂停机制。

游戏管理使用每实例设置与低频进程采样，原生任务队列继续持有输入；自动维护、同轮恢复和答题整局边界见[游戏管理说明](../docs/zh_cn/游戏管理.md)。`TaskScriptPlan.PrepareManaged` 和任务目录发现器保留原计次所有权，不另建输入执行器。`frontend/tests/GameManagement/` 用真实控件及替身服务检查布局和设置，不启动任何游戏进程。

默认录制包位于 `agent/recordings`。`recording_store.py` 负责首次入库、默认标记、复制、带恢复副本的删除与绑定原子更新；编辑和回放仍使用原有录制模型及文件。`agent/task-migrations.json` 声明退役入口，完整任务同步只移除名称和入口均匹配的旧条目，无关自定义任务保留。

任务目录由 `agent/task_catalog.py` 从现有 `interface.json`、导入定义与实例任务发现。普通任务标识由名称和入口稳定生成，支持 Pipeline v1/v2 的 `battle_play` 节点；旧绑定标识可在任务初始化参数的 `execution.binding_id` 中声明兼容。UI 和 C# 不内置副本白名单。

复杂任务通过 `script_task_state`、`execution.resume_entry`、`execution.settings_node`、`playback_node`、`routes` 和恢复入口的 `resume_slots` 声明运行与恢复能力。识别节点仍由各任务 Pipeline 定义。任务查询复用现有脚本服务协议，执行仍使用 Maa 控制器和任务队列。任务资源安装使用 `tools/sync_task_assets.py --task <任务名> --apply`，默认只预览。

宿主互斥接口由页面源码中的 `MFAAvalonia.Services.ScriptStudioGuard` 提供：`SyncRoot`、`IsCapturing` 和 `ThrowIfCapturing()`。录制端必须在同一把锁内检查所有实例的任务状态并取得录制所有权，停止及异常时释放。

上游源自 [MaaXYZ/MFAAvalonia](https://github.com/MaaXYZ/MFAAvalonia/tree/1080d3e1500f3439ac896ab2eca9aeb8e885f1f4)，保留上游依赖版本，未对其进行无关升级。构建产物附带 `LICENSE-MFAAvalonia.txt` 和 `script-studio-build.json`，用于记录上游许可与源码版本；本目录的补丁与页面源码随本工程提供。
