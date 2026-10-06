// Optional native LFS callback. The private build pins one origin and GCM binary.
// Credentials travel only through Git's callback pipe; never display them directly.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;

public static class FarmBotNativeGitAndLfsAskpass {
    public static int Main(string[] args) {
        try {
            if (args.Length != 1) return 1;
            var prompt = Regex.Match(args[0], "\\A(Username|Password) for '([^']+)': ?\\z");
            Uri requested;
            if (!prompt.Success || HasControl(args[0]) || args[0].Contains("\\") ||
                !Uri.TryCreate(prompt.Groups[2].Value, UriKind.Absolute, out requested)) return 1;
            // A failed GitHub lookup must never fall back to another credential store.
            if (requested.Host == "github.com") return FarmBotNativeGitAskpass.Main(args);
            return Lfs(prompt.Groups[1].Value, requested);
        } catch { return 1; }
    }

    private static bool HasControl(string value) {
        foreach (char c in value) if (Char.IsControl(c)) return true;
        return false;
    }

    private static bool OrdinaryPath(string path) {
        if (!Path.IsPathRooted(path) || !String.Equals(Path.GetFullPath(path), path, StringComparison.OrdinalIgnoreCase))
            return false;
        for (string entry = path; entry != null; entry = Path.GetDirectoryName(entry))
            if ((File.GetAttributes(entry) & FileAttributes.ReparsePoint) != 0) return false;
        return true;
    }

    private static string Digest(string path) {
        using (var input = File.OpenRead(path))
        using (var hash = SHA256.Create())
            return BitConverter.ToString(hash.ComputeHash(input)).Replace("-", "").ToLowerInvariant();
    }

    private static string BoundedRead(StreamReader reader) {
        var result = new StringBuilder();
        var buffer = new char[1024];
        int count;
        while ((count = reader.Read(buffer, 0, buffer.Length)) != 0) {
            if (result.Length + count > 8192) return null;
            result.Append(buffer, 0, count);
        }
        return result.ToString();
    }

    private static void Discard(StreamReader reader) {
        var buffer = new char[1024];
        while (reader.Read(buffer, 0, buffer.Length) != 0) { }
    }

    private static int Lfs(string field, Uri requested) {
        var settings = Path.ChangeExtension(Assembly.GetExecutingAssembly().Location, ".lfs");
        if (!File.Exists(settings) || !OrdinaryPath(settings) || new FileInfo(settings).Length > 16384) return 1;
        var lines = new UTF8Encoding(false, true).GetString(File.ReadAllBytes(settings)).Split('\n');
        if (lines.Length != 4 || lines[3].Length != 0) return 1;
        for (int i = 0; i < 3; i++) {
            lines[i] = lines[i].TrimEnd('\r');
            if (lines[i].Length == 0 || HasControl(lines[i])) return 1;
        }
        Uri allowed;
        if (lines[0].Contains("\\") || !Uri.TryCreate(lines[0], UriKind.Absolute, out allowed) ||
            (allowed.Scheme != "http" && allowed.Scheme != "https") || allowed.Host.Length == 0 ||
            allowed.Host.TrimEnd('.') == "github.com" || allowed.Port <= 0 || allowed.UserInfo.Length != 0 ||
            !String.Equals(lines[0], allowed.GetLeftPart(UriPartial.Authority), StringComparison.OrdinalIgnoreCase) ||
            requested.Scheme != allowed.Scheme || requested.Host != allowed.Host || requested.Port != allowed.Port ||
            requested.Query.Length != 0 || requested.Fragment.Length != 0 ||
            (field == "Username" && requested.UserInfo.Length != 0)) return 1;
        var executable = lines[1];
        if (!File.Exists(executable) || !OrdinaryPath(executable) ||
            !Path.GetFileName(executable).Equals("git-credential-manager.exe", StringComparison.OrdinalIgnoreCase) ||
            !Regex.IsMatch(lines[2], "\\A[0-9a-f]{64}\\z") || Digest(executable) != lines[2]) return 1;
        var info = new ProcessStartInfo(executable, "get") {
            UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true,
            RedirectStandardOutput = true, RedirectStandardError = true,
            StandardOutputEncoding = new UTF8Encoding(false, true),
            StandardErrorEncoding = new UTF8Encoding(false)
        };
        info.EnvironmentVariables["GCM_INTERACTIVE"] = "never";
        info.EnvironmentVariables["GIT_TERMINAL_PROMPT"] = "0";
        info.EnvironmentVariables["GCM_GUI_PROMPT"] = "false";
        // Framework's redirected stdin captures Console.InputEncoding at Start.
        var original = Console.InputEncoding;
        Process child;
        try { Console.InputEncoding = new UTF8Encoding(false); child = Process.Start(info); }
        finally { Console.InputEncoding = original; }
        using (child) {
            var output = Task.Run(() => BoundedRead(child.StandardOutput));
            var error = Task.Run(() => Discard(child.StandardError));
            // Explicit server-only lookup, matching credential.useHttpPath=false.
            child.StandardInput.Write("protocol=" + allowed.Scheme + "\nhost=" + allowed.Authority + "\n\n");
            child.StandardInput.Close();
            if (!child.WaitForExit(15000)) { child.Kill(); child.WaitForExit(); return 1; }
            if (child.ExitCode != 0 || !output.Wait(2000) || !error.Wait(2000) || output.Result == null) return 1;
            var fields = new Dictionary<string,string>();
            foreach (var raw in output.Result.Split('\n')) {
                var line = raw.TrimEnd('\r');
                if (line.Length == 0) continue;
                int split = line.IndexOf('=');
                if (split <= 0 || HasControl(line)) return 1;
                var key = line.Substring(0, split);
                if (fields.ContainsKey(key)) return 1;
                fields[key] = line.Substring(split + 1);
            }
            string user, password, value;
            if (!fields.TryGetValue("username", out user) || !fields.TryGetValue("password", out password) ||
                user.Length == 0 || password.Length == 0) return 1;
            if (fields.TryGetValue("protocol", out value) && value != allowed.Scheme) return 1;
            if (fields.TryGetValue("host", out value) && value != allowed.Authority) return 1;
            if (requested.UserInfo.Length > 0 && Uri.UnescapeDataString(requested.UserInfo) != user) return 1;
            Console.OutputEncoding = new UTF8Encoding(false);
            Console.Out.WriteLine(field == "Username" ? user : password);
            return 0;
        }
    }
}
