using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;

namespace MFAAvalonia.Services;

/// <summary>Single-request adapter to the same backend used by the launcher.</summary>
public sealed class ProjectUpdateClient
{
    public string Root { get; }
    public ProjectUpdateClient(string? root = null) => Root = root ?? AppContext.BaseDirectory;

    public async Task<JsonNode> CallAsync(string method, JsonObject? parameters = null,
        Action<long, long>? progress = null, CancellationToken cancellationToken = default)
    {
        var python = Path.Combine(Root, "runtimes", "python", "python.exe");
        if (!File.Exists(python) || !File.Exists(Path.Combine(Root, "update_engine", "__main__.py")))
            throw new InvalidOperationException("统一更新运行文件缺失，请首次手动安装本项目的完整客户端。");
        var info = new ProcessStartInfo(python)
        {
            WorkingDirectory = Root, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardInputEncoding = new UTF8Encoding(false), StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };
        foreach (var argument in new[] { "-E", "-s", "-u", "-X", "utf8", "-m", "update_engine" })
            info.ArgumentList.Add(argument);
        return await ExchangeAsync(info, method, parameters, progress, cancellationToken);
    }

    internal static async Task<JsonNode> ExchangeAsync(ProcessStartInfo info, string method,
        JsonObject? parameters = null, Action<long, long>? progress = null, CancellationToken cancellationToken = default)
    {
        using var process = new Process { StartInfo = info };
        using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(method == "download" ? 60 : 5));
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(timeout.Token, cancellationToken);
        process.Start();
        // Drain stderr concurrently, but never display arbitrary trace output as
        // successful protocol data. The worker sends user-facing errors on stdout.
        var errors = process.StandardError.ReadToEndAsync(linked.Token);
        try
        {
            await process.StandardInput.WriteLineAsync(new JsonObject
                { ["method"] = method, ["params"] = parameters ?? new JsonObject() }.ToJsonString());
            process.StandardInput.Close();
            JsonNode? result = null;
            while (await process.StandardOutput.ReadLineAsync(linked.Token) is { } line)
            {
                if (line.Length > 1024 * 1024) throw new IOException("更新器响应过大。");
                var message = JsonNode.Parse(line) ?? throw new IOException("更新器返回空响应。");
                if (message["event"]?.GetValue<string>() == "progress")
                {
                    progress?.Invoke(message["done"]!.GetValue<long>(), message["total"]!.GetValue<long>());
                    continue;
                }
                if (result != null) throw new IOException("更新器重复返回结果。");
                if (message["ok"]?.GetValue<bool>() != true)
                    throw new InvalidOperationException(message["error"]?.GetValue<string>() ?? "更新操作失败。");
                result = message["result"] ?? new JsonObject();
            }
            await process.WaitForExitAsync(linked.Token);
            await errors;
            if (process.ExitCode != 0 || result == null) throw new IOException("更新工作进程异常结束，请查看更新日志。");
            return result;
        }
        finally
        {
            if (!process.HasExited) process.Kill(); // only our one-shot helper, never a game/client
        }
    }
}
