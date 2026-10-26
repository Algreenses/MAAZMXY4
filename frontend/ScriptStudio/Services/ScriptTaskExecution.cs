using System;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using MaaFramework.Binding;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Helper;

namespace MFAAvalonia.Services;

/// <summary>Reuse the studio catalog and Maa execution for every configured task.</summary>
public static class ScriptTaskExecution
{
    public static async Task<MaaJobStatus> RunAsync(MaaProcessor processor, string? name, string? entry, string? overrides,
        Func<string?, string?, CancellationToken, Task<MaaJobStatus>> execute, Action stopOnFailure, CancellationToken token)
    {
        if (string.IsNullOrEmpty(entry) || string.IsNullOrEmpty(name)) return await execute(entry, overrides, token);
        try
        {
            using var library = new ScriptStudioClient(AppPaths.DataRoot, releaseCaptureOnDisconnect: false);
            var plan = (await library.CallAsync("execution_plan", new JsonObject
            {
                ["name"] = name, ["entry"] = entry,
                ["overrides"] = TaskRecoveryPolicy.MergePipelineOverrides(overrides)
            }, token)).AsObject();
            token.ThrowIfCancellationRequested();
            if (plan["handled"]?.GetValue<bool>() != true) return await execute(entry, overrides, token);
            stopOnFailure();
            var prepared = TaskScriptPlan.Prepare(plan, entry, overrides);
            if (prepared.Profile != null)
                return await TaskRecovery.RunAsync(processor, prepared.Entry, prepared.Pipeline.ToJsonString(), prepared.Profile, execute, token);
            return await execute(prepared.Entry, prepared.Pipeline.ToJsonString(), token);
        }
        catch (OperationCanceledException) { throw; }
        catch (Exception error)
        {
            stopOnFailure();
            processor.AddLog("任务脚本未执行：" + error.Message);
            return MaaJobStatus.Failed;
        }
    }
}
