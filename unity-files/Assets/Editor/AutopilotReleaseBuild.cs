using System;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;

public static class AutopilotReleaseBuild
{
    public static void WebGL()
    {
        EditorUserBuildSettings.development = false;
        EditorUserBuildSettings.allowDebugging = false;
        EditorUserBuildSettings.connectProfiler = false;
        PlayerSettings.WebGL.compressionFormat = WebGLCompressionFormat.Disabled;
        PlayerSettings.WebGL.decompressionFallback = false;
        PlayerSettings.WebGL.exceptionSupport = WebGLExceptionSupport.ExplicitlyThrownExceptionsOnly;
        PlayerSettings.WebGL.nameFilesAsHashes = true;
        var scenes = EditorBuildSettings.scenes.Where(s => s.enabled).Select(s => s.path).ToArray();
        if (scenes.Length != 2 || !scenes[0].EndsWith("Menu.unity"))
            throw new Exception("Release needs Menu then Main in build settings");
        var output = Environment.GetEnvironmentVariable("AUTOPILOT_UNITY_BUILD") ?? "Builds/build12";
        var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions {
            scenes = scenes, locationPathName = output,
            target = BuildTarget.WebGL, options = BuildOptions.None
        });
        if (report.summary.result != BuildResult.Succeeded)
            throw new Exception("Release build failed: " + report.summary.result);
    }
}
