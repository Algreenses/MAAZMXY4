using System.Diagnostics;
using System.Reflection;
using MFAAvalonia.Services;

if (args is ["--worker", var mode])
{
    Console.ReadLine();
    if (mode == "cancel") { await Task.Delay(30000); return 0; }
    if (mode == "eof") return 0;
    if (mode == "invalid") { Console.WriteLine("not-json"); return 0; }
    if (mode == "error") { Console.WriteLine("{\"ok\":false,\"error\":\"expected failure\"}"); return 1; }
    Console.WriteLine("{\"event\":\"progress\",\"done\":12,\"total\":24}");
    Console.WriteLine("{\"ok\":true,\"result\":{\"status\":\"verified\"}}");
    if (mode == "duplicate") Console.WriteLine("{\"ok\":true,\"result\":{}}");
    return mode == "exit-error" ? 1 : 0;
}
foreach (var test in new[] { "success", "eof", "invalid", "error", "duplicate", "exit-error", "cancel" })
{
    var info = new ProcessStartInfo(Environment.ProcessPath!)
    {
        UseShellExecute = false, CreateNoWindow = true,
        RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true
    };
    if (string.Equals(Path.GetFileNameWithoutExtension(Environment.ProcessPath), "dotnet", StringComparison.OrdinalIgnoreCase))
        info.ArgumentList.Add(Assembly.GetExecutingAssembly().Location);
    info.ArgumentList.Add("--worker"); info.ArgumentList.Add(test);
    using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(test == "cancel" ? 0.3 : 5));
    var progress = false;
    Exception? caught = null;
    try
    {
        var result = await ProjectUpdateClient.ExchangeAsync(info, "check", progress: (done, total) =>
            progress = done == 12 && total == 24, cancellationToken: cancellation.Token);
        if (result["status"]?.GetValue<string>() != "verified") throw new Exception("Wrong result");
    }
    catch (Exception error) { caught = error; }
    if (test == "success" ? caught != null || !progress : caught == null)
        throw new Exception($"Transport regression: {test}: {caught}");
    if (test == "cancel" && caught is not OperationCanceledException)
        throw new Exception("Cancellation did not propagate");
    Console.WriteLine("PASS " + test);
}
ScriptStudioGuard.UpdateBlockReason = "installing";
try { ScriptStudioGuard.BeginCapture(); throw new Exception("Recording was allowed during installation"); }
catch (InvalidOperationException) { }
ScriptStudioGuard.UpdateBlockReason = "";
ScriptStudioGuard.BeginCapture(); ScriptStudioGuard.EndCapture();
Console.WriteLine("PASS install guard protects recording and shared task-start gate");
return 0;

namespace MFAAvalonia.Extensions.MaaFW
{
    public sealed class MaaProcessor
    {
        public static List<MaaProcessor> Processors { get; } = new();
        public bool IsTaskRunActive { get; set; }
    }
}
