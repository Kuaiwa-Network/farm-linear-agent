// Optional zero-argument native Git LFS protocol adapter. No shell or credential setup.
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

public static class FarmBotNativeLfsFilter
{
    private static void Ordinary(string path)
    {
        string current = Path.GetFullPath(path);
        while (!String.IsNullOrEmpty(current))
        {
            if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                throw new IOException();
            current = Path.GetDirectoryName(current);
        }
    }

    private static string SelectedExecutable()
    {
        string self = Assembly.GetExecutingAssembly().Location;
        Ordinary(self);
        string settings = Path.ChangeExtension(self, ".lfs-filter");
        Ordinary(settings);
        if (new FileInfo(settings).Length > 8192) throw new IOException();
        string text = new UTF8Encoding(false, true).GetString(File.ReadAllBytes(settings));
        string[] lines = text.Split('\n');
        if (lines.Length != 3 || lines[2] != "") throw new IOException();
        string executable = lines[0];
        string digest = lines[1];
        if (!Regex.IsMatch(executable, @"^[A-Za-z]:\\") || executable.IndexOf('\0') >= 0 ||
            !String.Equals(Path.GetFullPath(executable), executable, StringComparison.OrdinalIgnoreCase) ||
            !String.Equals(Path.GetFileName(executable), "git-lfs.exe", StringComparison.OrdinalIgnoreCase) ||
            !Regex.IsMatch(digest, "^[a-f0-9]{64}$")) throw new IOException();
        Ordinary(executable);
        using (SHA256 hash = SHA256.Create())
        using (FileStream input = File.OpenRead(executable))
        {
            string actual = BitConverter.ToString(hash.ComputeHash(input)).Replace("-", "").ToLowerInvariant();
            if (actual != digest) throw new IOException();
        }
        return executable;
    }

    private static void Transfer(Stream source, Stream destination)
    {
        byte[] buffer = new byte[65536];
        int size;
        while ((size = source.Read(buffer, 0, buffer.Length)) != 0)
        {
            destination.Write(buffer, 0, size);
            // Git's packet-line handshake must not wait for a text/buffer boundary.
            destination.Flush();
        }
    }

    public static int Main(string[] args)
    {
        Process child = null;
        try
        {
            if (args.Length != 0) return 1;
            string executable = SelectedExecutable();
            child = new Process();
            child.StartInfo = new ProcessStartInfo(executable, "filter-process")
            {
                UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardInput = true, RedirectStandardOutput = true
            };
            // Framework constructs its redirected stdin writer at Start and
            // captures Console.InputEncoding. Its AutoFlush can emit a UTF-8
            // preamble before our raw BaseStream forwarding even begins.
            // Select a BOM-free writer only for creation, then restore the
            // caller's complete encoding. Protocol bytes still bypass text IO.
            Encoding originalInputEncoding = Console.InputEncoding;
            try
            {
                Console.InputEncoding = new UTF8Encoding(false);
                if (!child.Start()) return 1;
            }
            finally { Console.InputEncoding = originalInputEncoding; }
            Process ownedChild = child;
            int inputFailed = 0;
            Thread input = new Thread(delegate()
            {
                try { Transfer(Console.OpenStandardInput(), ownedChild.StandardInput.BaseStream); }
                catch { Interlocked.Exchange(ref inputFailed, 1); }
                finally { try { ownedChild.StandardInput.Close(); } catch { } }
            });
            input.IsBackground = true;
            input.Start();
            Transfer(child.StandardOutput.BaseStream, Console.OpenStandardOutput());
            child.WaitForExit();
            int result = child.ExitCode;
            return result != 0 ? result : (Interlocked.CompareExchange(ref inputFailed, 0, 0) == 0 ? 0 : 1);
        }
        catch
        {
            // Only the Process object this invocation started is eligible for termination.
            try { if (child != null && !child.HasExited) { child.Kill(); child.WaitForExit(5000); } } catch { }
            return 1;
        }
        finally { if (child != null) child.Dispose(); }
    }
}
