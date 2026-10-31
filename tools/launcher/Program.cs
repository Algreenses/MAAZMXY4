// Stable bootstrap: 直接启动前端，不做任何版本检查或更新。

using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

static class Program
{
    [STAThread]
    static void Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        try
        {
            string exe = Path.Combine(root, "MFAAvalonia.exe");
            if (!File.Exists(exe))
                throw new FileNotFoundException("启动文件缺失，请使用完整客户端安装包修复。");

            Process process = Process.Start(new ProcessStartInfo(exe)
            {
                WorkingDirectory = root,
                UseShellExecute = false
            });
            if (process == null) throw new InvalidOperationException("无法启动客户端。");
            process.Dispose();
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "MAA造梦西游4 · 启动未完成", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
