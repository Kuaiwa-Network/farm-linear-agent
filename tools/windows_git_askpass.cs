// Optional native Windows Git callback. Credentials remain in gh's existing store.
// Git consumes stdout privately; never invoke this callback to display credentials.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

public static class FarmBotNativeGitAskpass {
    public static int Main(string[] args) {
        try {
            if (args.Length != 1) return 1;
            var prompt = Regex.Match(args[0], "^(Username|Password) for '([^']+)': ?$");
            Uri uri;
            if (!prompt.Success || !Uri.TryCreate(prompt.Groups[2].Value, UriKind.Absolute, out uri)) return 1;
            if (uri.Scheme != "https" || uri.Host != "github.com" || uri.Port != 443 ||
                uri.Query.Length != 0 || uri.Fragment.Length != 0) return 1;
            if (prompt.Groups[1].Value == "Username" && uri.UserInfo.Length != 0) return 1;
            var exe = Environment.GetEnvironmentVariable("FARMBOT_NATIVE_GH");
            if (String.IsNullOrEmpty(exe) || !Path.IsPathRooted(exe) || !File.Exists(exe) ||
                !Path.GetFileName(exe).Equals("gh.exe", StringComparison.OrdinalIgnoreCase)) return 1;
            var info = new ProcessStartInfo(exe, "auth git-credential get") {
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = new UTF8Encoding(false),
                StandardErrorEncoding = new UTF8Encoding(false)
            };
            // Framework has no StandardInputEncoding selector. Its redirected
            // writer captures Console.InputEncoding when the process starts.
            var originalInputEncoding = Console.InputEncoding;
            Process child;
            try {
                Console.InputEncoding = new UTF8Encoding(false);
                child = Process.Start(info);
            } finally {
                Console.InputEncoding = originalInputEncoding;
            }
            using (child) {
                var output = child.StandardOutput.ReadToEndAsync();
                var error = child.StandardError.ReadToEndAsync();
                child.StandardInput.Write("protocol=https\nhost=github.com\n\n");
                child.StandardInput.Close();
                if (!child.WaitForExit(15000)) { child.Kill(); child.WaitForExit(); return 1; }
                if (child.ExitCode != 0) return 1;
                var raw = output.Result;
                if (raw.Length > 8192) return 1;
                var fields = new Dictionary<string,string>();
                foreach (var line in raw.Split('\n')) {
                    if (line.TrimEnd('\r').Length == 0) continue;
                    int split = line.IndexOf('=');
                    if (split <= 0) return 1;
                    string key = line.Substring(0, split);
                    if (fields.ContainsKey(key)) return 1;
                    fields[key] = line.Substring(split + 1).TrimEnd('\r');
                }
                string user, password, protocol, host;
                if (!fields.TryGetValue("protocol", out protocol) || protocol != "https" ||
                    !fields.TryGetValue("host", out host) || host != "github.com" ||
                    !fields.TryGetValue("username", out user) || !fields.TryGetValue("password", out password) ||
                    !Regex.IsMatch(user, "^[A-Za-z0-9_.-]+$") || !Regex.IsMatch(password, "^[A-Za-z0-9_-]+$")) return 1;
                if (uri.UserInfo.Length > 0 && Uri.UnescapeDataString(uri.UserInfo) != user) return 1;
                Console.OutputEncoding = new UTF8Encoding(false);
                Console.Out.WriteLine(prompt.Groups[1].Value == "Username" ? user : password);
                return 0;
            }
        } catch { return 1; }
    }
}
