namespace MFAAvalonia.Services;

/// <summary>The project distributes its customized frontend as a complete client.</summary>
public static class FrontendUpdatePolicy
{
    public static bool OnlineFrontendUpdateEnabled => false;
    public const string ManualUpdateMessage = "当前为 MAAZMXY4 脚本工作台定制前端。请关闭助手后，下载本项目的新版完整客户端手动升级，并保留 recordings 和 config。通用 MFAAvalonia 更新包不包含此工作台。";
}
