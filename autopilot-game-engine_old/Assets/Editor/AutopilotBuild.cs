using System;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;

public static class AutopilotBuild
{
    public static void Web()
    {
        PlayerSettings.WebGL.compressionFormat = WebGLCompressionFormat.Disabled;
        PlayerSettings.WebGL.decompressionFallback = false;
        PlayerSettings.WebGL.exceptionSupport = WebGLExceptionSupport.FullWithoutStacktrace;
        string output = Environment.GetEnvironmentVariable("AUTOPILOT_UNITY_BUILD") ?? "Builds/WebGL";
        BuildReport report = BuildPipeline.BuildPlayer(new BuildPlayerOptions {
            scenes = new[] { "Assets/Scenes/Main.unity" },
            locationPathName = output,
            target = BuildTarget.WebGL,
            options = BuildOptions.None
        });
        if (report.summary.result != BuildResult.Succeeded)
            throw new Exception("Autopilot Web build failed: " + report.summary.result);
    }

    public static void Linux()
    {
        string output = Environment.GetEnvironmentVariable("AUTOPILOT_UNITY_BUILD") ?? "Builds/Linux/Autopilot.x86_64";
        BuildReport report = BuildPipeline.BuildPlayer(new BuildPlayerOptions {
            scenes = new[] { "Assets/Scenes/Main.unity" },
            locationPathName = output,
            target = BuildTarget.StandaloneLinux64,
            options = BuildOptions.None
        });
        if (report.summary.result != BuildResult.Succeeded)
            throw new Exception("Autopilot Unity build failed: " + report.summary.result);
    }
}
