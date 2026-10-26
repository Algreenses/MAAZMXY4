# 脚本工具前端

本目录为现有 MFAAvalonia 增加原生“脚本工具”页面，沿用 Avalonia、SukiUI 和任务前端。页面内采用两个子标签：“脚本库与录制”和“时间轴与编辑”。录制和编辑规则由 ZaoMengScript 迁移，Python 服务不依赖 Qt。

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

安装备份位于 `backup/frontend-script-studio-日期-随机后缀/`。其中 `installation.json` 记录覆盖前已存在和新建的文件；需要恢复时，关闭前端，将备份文件按相同相对路径复制回项目。脚本不会主动关闭任何现有 MFAAvalonia 进程。

## 宿主接入

`patches/` 使用精确文本补丁，每个补丁包含目标路径、旧文本和新文本。脚本要求旧文本只出现一次；补丁已应用时跳过，锚点变化时停止，不尝试模糊修改其它版本源码。

- `01-script-studio-navigation.json`：在共享 `RootViewContent.axaml` 的现有侧栏加入页面。
- `02-script-studio-registration.json`：接入现有 DI 和 ViewLocator 映射。
- `03-script-studio-task-guard.json`：在两个任务启动入口共用的 `TryBeginTaskRun()` 内，与录制端使用同一把锁，防止录制与界面、定时或其它实例启动任务并行。冲突通过原有 Toast 与日志反馈。
- `04-selected-win32-controller.json`、`05-selected-controller-resource.json`：有多个 Win32 配置时，窗口、资源及任务使用当前命名控制器的筛选规则，支持同类型控制器使用不同窗口与资源设置。

宿主互斥接口由页面源码中的 `MFAAvalonia.Services.ScriptStudioGuard` 提供：`SyncRoot`、`IsCapturing` 和 `ThrowIfCapturing()`。录制端必须在同一把锁内检查所有实例的任务状态并取得录制所有权，停止及异常时释放。

上游源自 [MaaXYZ/MFAAvalonia](https://github.com/MaaXYZ/MFAAvalonia/tree/1080d3e1500f3439ac896ab2eca9aeb8e885f1f4)，保留上游依赖版本，未对其进行无关升级。构建产物附带 `LICENSE-MFAAvalonia.txt` 和 `script-studio-build.json`，用于记录上游许可与源码版本；本目录的补丁与页面源码随本工程提供。
