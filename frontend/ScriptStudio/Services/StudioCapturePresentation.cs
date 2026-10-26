using System;
using System.Text.Json.Nodes;

namespace MFAAvalonia.Services;

public static class StudioCapturePresentation
{
    public static string Title(JsonObject snapshot)
    {
        var state = snapshot["state"]?.GetValue<string>();
        if (state is "countdown" or "resume_countdown")
            return $"{(state == "countdown" ? "开始录制" : "继续录制")}倒计时  {Math.Ceiling(snapshot["countdown_seconds"]?.GetValue<double>() ?? 0):0} 秒";
        var seconds = (snapshot["elapsed_ms"]?.GetValue<double>() ?? 0) / 1000;
        var label = state switch { "recording" => "正在录制", "paused" => "已暂停", "stopping" => "正在停止", _ => "录制结束" };
        return $"{label}  {seconds:0.0} 秒 · {snapshot["event_count"]} 个事件";
    }
}
