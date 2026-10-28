// Stable bootstrap: recovery must not depend on the Python DLLs being replaced.

using System;
using System.Diagnostics;
using System.IO;
using System.Text.RegularExpressions;
using System.Windows.Forms;

static class Program
{
    [STAThread]
    static void Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string pointer = Path.Combine(root, @"temp\project-update\worker-ready.txt");
        try
        {
            if (File.Exists(pointer))
            {
                string token = File.ReadAllText(pointer);
                if (!Regex.IsMatch(token, @"\A[0-9a-f]{32}\z"))
                    throw new InvalidDataException("更新恢复记录损坏，请保留 temp/project-update 后联系维护者。");
                string worker = Path.Combine(root, @"temp\project-update\workers", token);
                StartPython(Path.Combine(worker, @"runtimes\python\pythonw.exe"),
                    "-E -s -u -X utf8 -m update_engine worker --recover", worker, false);
                return;
            }
            string python = Path.Combine(root, @"runtimes\python\pythonw.exe");
            string updater = Path.Combine(root, "updater.py");
            if (!File.Exists(python) || !File.Exists(updater))
                throw new FileNotFoundException("启动运行文件缺失，请使用完整客户端安装包修复。");
            StartPython(python, "-E -s -X utf8 \"" + updater + "\"", root, true);
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "MAA造梦西游4 · 启动未完成", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    static void StartPython(string python, string arguments, string directory, bool wait)
    {
        if (!File.Exists(python))
            throw new FileNotFoundException("更新助手运行时缺失，请保留备份并重新安装完整客户端。");
        Process process = Process.Start(new ProcessStartInfo(python, arguments)
        {
            WorkingDirectory = directory, UseShellExecute = false, CreateNoWindow = true
        });
        if (process == null) throw new InvalidOperationException("无法启动更新助手。");
        if (wait) process.WaitForExit();
        process.Dispose();
    }
}
