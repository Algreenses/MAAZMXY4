using System;
using System.IO;
using System.Text.Json.Nodes;
using System.Threading;

namespace MFAAvalonia.Services;

/// <summary>Turn catalog capabilities and saved bindings into native Pipeline overrides.</summary>
public static class TaskScriptPlan
{
    public static void StopIfUnbound(JsonObject plan, Action<string> log, CancellationToken token)
    {
        if (plan["stop_reason"]?.GetValue<string>() != "unbound_script") return;
        token.ThrowIfCancellationRequested();
        log(plan["message"]?.GetValue<string>() ?? "任务尚未绑定脚本，本次任务已结束，请绑定后再开始。");
        // MFATask already treats cancellation as STOPPED: no failed-task toast,
        // no completed iteration, and no execution of later queued tasks.
        throw new OperationCanceledException(token);
    }

    public static JsonObject Parameters(JsonObject pipeline, string nodeName)
    {
        if (pipeline[nodeName] is not JsonObject node) pipeline[nodeName] = node = new();
        if (node["action"] is not JsonObject action) node["action"] = action = new();
        if (action["param"] is not JsonObject param) action["param"] = param = new();
        if (param["custom_action_param"] is not JsonObject values) param["custom_action_param"] = values = new();
        return values;
    }

    public static (string Entry, JsonObject Pipeline, JsonObject? Profile) Prepare(JsonObject plan, string entry, string? overrides)
    {
        var pipeline = TaskRecoveryPolicy.MergePipelineOverrides(overrides);
        var binding = plan["binding"] as JsonObject;
        var recording = plan["recording"] as JsonObject;
        var profile = plan["profile"] as JsonObject;
        if (binding?["mode"]?.GetValue<string>() == "replace")
        {
            var replacement = "ScriptPlayback_" + Guid.NewGuid().ToString("N");
            var playback = Playback(recording);
            return (replacement, new JsonObject { [replacement] = new JsonObject
            {
                ["action"] = new JsonObject { ["type"] = "Custom", ["param"] = new JsonObject
                { ["custom_action"] = "battle_play", ["custom_action_param"] = playback } },
                ["next"] = new JsonArray(), ["post_delay"] = 0
            } }, null);
        }
        if (profile != null)
        {
            var defaults = profile["parameters"]?.DeepClone() ?? new JsonObject();
            var settingNode = defaults["execution"]?["settings_node"]?.GetValue<string>();
            var current = Parameters(pipeline, entry).DeepClone();
            var settings = settingNode == null ? new JsonObject() : Parameters(pipeline, settingNode).DeepClone();
            var complete = TaskRecoveryPolicy.MergePipelineOverrides(new JsonArray(defaults, current, settings).ToJsonString());
            complete["phase"] = "begin";
            complete["task_binding_id"] = plan["id"]!.DeepClone();
            if (binding != null)
            {
                complete["file"] = recording!["path"]!.DeepClone();
                complete["expected_recording_file"] = recording["file"]!.DeepClone();
                complete["expected_recording_sha256"] = recording["sha256"]!.DeepClone();
            }
            var parameters = Parameters(pipeline, entry);
            parameters.Clear();
            foreach (var item in complete) parameters[item.Key] = item.Value?.DeepClone();
        }
        else if (binding != null)
        {
            var nodes = plan["playback_nodes"]?.AsArray() ?? throw new InvalidDataException("任务缺少回放节点。");
            if (nodes.Count == 0) throw new InvalidDataException("任务没有回放节点，请使用替代任务方式。");
            foreach (var node in nodes)
            {
                var name = node!.GetValue<string>();
                var parameters = Parameters(pipeline, name);
                var defaults = plan["playback_parameters"]?[name]?.DeepClone() ?? new JsonObject();
                var combined = TaskRecoveryPolicy.MergePipelineOverrides(new JsonArray(defaults, parameters.DeepClone()).ToJsonString());
                foreach (var value in combined) parameters[value.Key] = value.Value?.DeepClone();
                foreach (var value in Playback(recording)) parameters[value.Key] = value.Value?.DeepClone();
                var action = pipeline[name]!["action"]!.AsObject();
                action["type"] = "Custom";
                action["param"]!["custom_action"] = "battle_play";
                var includesReturn = pipeline["脚本任务设置"]?["action"]?["param"]?["custom_action_param"]?["exit_mode"]?.GetValue<string>() == "recorded_return";
                if (includesReturn)
                {
                    var next = plan["playback_routes"]?[name]?["recorded_return_next"] as JsonArray;
                    if (next == null || next.Count == 0)
                        throw new InvalidDataException("任务资源缺少录制返回确认流程，请更新本项目完整客户端；自定义任务需配置 script_binding.recorded_return_next。");
                    pipeline[name]!["next"] = next.DeepClone();
                }
            }
        }
        return (entry, pipeline, profile);
    }

    private static JsonObject Playback(JsonObject? recording)
    {
        if (recording == null) throw new InvalidDataException("缺少已校验的任务脚本。");
        return new JsonObject { ["file"] = recording["path"]!.DeepClone(),
            ["expected_sha256"] = recording["sha256"]!.DeepClone(), ["strict_task"] = true,
            ["expected_short_side"] = 720, ["speed"] = 1.0 };
    }
}
