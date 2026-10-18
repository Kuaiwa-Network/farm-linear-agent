using System;
using System.Collections.Generic;
using System.Web.Script.Serialization;

namespace MCPForUnity.Editor.Services
{
    public static class TestJobManager
    {
        public static readonly Dictionary<string, Job> Jobs = new Dictionary<string, Job>();
    }
    public class Job
    {
        public string JobId { get; set; }
        public string Status { get; set; }
        public string Mode { get { return "PlayMode"; } }
        public string Error { get { return null; } }
        public long StartedUnixMs { get; set; }
        public long? FinishedUnixMs { get; set; }
        public Result Result { get; set; }
    }
    public class Result
    {
        public object Summary { get; set; }
        public List<object> Results { get; set; }
    }
}
class Harness
{
    static object Probe()
    {
        // PROBE
    }
    static int Main(string[] args)
    {
        Console.OutputEncoding = new System.Text.UTF8Encoding(false);
        string id = new string('b', 32);
        string scenario = args[0];
        var job = new MCPForUnity.Editor.Services.Job { JobId = id, Status = "Failed", StartedUnixMs = 100001,
            FinishedUnixMs = 110000, Result = new MCPForUnity.Editor.Services.Result {
                Summary = new { Total = 123, Passed = 100, Failed = scenario == "passed" ? 0 : 23,
                    Skipped = 0, DurationSeconds = 10.0, ResultState = "Failed(Child)" }, Results = new List<object>() } };
        for (int i = 0; i < 100; i++)
            job.Result.Results.Add(new { State = "Passed" });
        int count = scenario == "capped" || scenario == "unicode" ? 120 : 23;
        string text = new string(scenario == "unicode" ? '\u4e2d' : 'x', count == 120 ? 5000 : 8);
        for (int i = 0; i < count; i++)
            job.Result.Results.Add(new { State = "Failed:Error", FullName = text, Message = text, StackTrace = text });
        if (scenario == "passed") { job.Status = "Succeeded"; job.Result.Results.Clear(); }
        if (scenario == "running") { job.Status = "Running"; job.FinishedUnixMs = null; }
        if (scenario == "evicted") job.Result = null;
        if (scenario == "old") job.StartedUnixMs = 99999;
        if (scenario == "wrong-id") job.JobId = new string('c', 32);
        if (scenario != "missing") MCPForUnity.Editor.Services.TestJobManager.Jobs.Add(id, job);
        string before = new JavaScriptSerializer().Serialize(job);
        object report;
        try { report = Probe(); if (scenario == "old" || scenario == "wrong-id") return 2; }
        catch (InvalidOperationException ex)
        {
            if (scenario != "old" && scenario != "wrong-id") throw;
            report = new { rejected = ex.Message };
        }
        if (before != new JavaScriptSerializer().Serialize(job)) return 3;
        Console.WriteLine(new JavaScriptSerializer().Serialize(report));
        return 0;
    }
}
