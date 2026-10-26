// MAA造梦西游4 启动器
// 双击后先跑 updater.py（检查更新），等它结束后由 updater.py 拉起 MFA.exe。
// 找不到 Python 运行时就直接启动 MFA.exe，保证永远能进游戏。

using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

static class Program
{
    [STAThread]
    static void Main()
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string py = Path.Combine(dir, @"runtimes\python\pythonw.exe");
        string script = Path.Combine(dir, "updater.py");
        string mfa = Path.Combine(dir, "MFA.exe");

        if (!File.Exists(py) || !File.Exists(script))
        {
            if (File.Exists(mfa))
                Process.Start(new ProcessStartInfo(mfa) { WorkingDirectory = dir, UseShellExecute = true });
            else
                MessageBox.Show("找不到 MFA.exe，安装包可能不完整，请重新解压。",
                                "MAA造梦西游4", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }

        try
        {
            var psi = new ProcessStartInfo(py, "\"" + script + "\"")
            {
                WorkingDirectory = dir,
                UseShellExecute = false,
                CreateNoWindow = true
            };
            Process p = Process.Start(psi);
            p.WaitForExit();
        }
        catch
        {
            if (File.Exists(mfa))
                Process.Start(new ProcessStartInfo(mfa) { WorkingDirectory = dir, UseShellExecute = true });
        }
    }
}
