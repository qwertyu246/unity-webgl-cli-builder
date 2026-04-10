// ViverseWebGLBuilder.cs
//
// Purpose
// - This is a Unity Editor build script intended to be executed via Unity CLI:
//     Unity.exe -batchmode -quit -executeMethod Viverse.Build.ViverseWebGLBuilder.BuildWebGL ...
//
// How it is used
// - Our Python orchestrator copies this file into the target project at:
//     Assets/Editor/ViverseWebGLBuilder.cs
//   then calls -executeMethod.
//
// Key behaviors
// - Force WebGL build target
// - Force WebGL Compression = Disabled (best hosting compatibility; avoids Brotli/Gzip header issues)
// - Build to the folder passed in via -viverseBuildOutput
// - Exit code:
//     0 = success
//     1+ = failure (see log for details)

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace Viverse.Build
{
    public static class ViverseWebGLBuilder
    {
        // CLI entrypoint (must match -executeMethod argument)
        public static void BuildWebGL()
        {
            try
            {
                // Custom CLI args passed from Python (or any orchestrator).
                // Example:
                //   -viverseBuildOutput "C:\...\unity-builds\Project\webgl"
                //   -viverseBuildName   "20260410-134510"
                var outputPath = GetArgValue("-viverseBuildOutput");
                var buildName = GetArgValue("-viverseBuildName") ?? "build";

                if (string.IsNullOrEmpty(outputPath))
                {
                    Debug.LogError("Missing required arg: -viverseBuildOutput <path>");
                    EditorApplication.Exit(2);
                    return;
                }

                // Ensure output directory exists
                Directory.CreateDirectory(outputPath);

                // 1) Switch active build target to WebGL
                // This can take a moment the first time (platform switch).
                EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.WebGL, BuildTarget.WebGL);

                // 2) Force WebGL compression to Disabled for maximum hosting compatibility.
                // If you later want Gzip/Brotli, you'll need correct Content-Encoding headers on the server.
#if UNITY_2018_4_OR_NEWER
                PlayerSettings.WebGL.compressionFormat = WebGLCompressionFormat.Disabled;
#endif

                // 3) Determine which scenes to build
                // Preferred: "Scenes In Build" from File > Build Settings...
                var scenes = EditorBuildSettings.scenes
                    .Where(s => s != null && s.enabled && !string.IsNullOrEmpty(s.path))
                    .Select(s => s.path)
                    .ToList();

                // Fallback: if Scenes In Build is empty, try finding scenes in Assets/.
                if (scenes.Count == 0)
                {
                    scenes = FindFallbackScenes();
                }

                if (scenes.Count == 0)
                {
                    Debug.LogError(
                        "No scenes found to build.\n" +
                        "- Add at least one scene in File > Build Settings...\n" +
                        "- Or ensure at least one .unity scene exists under Assets/."
                    );
                    EditorApplication.Exit(3);
                    return;
                }

                Debug.Log($"[ViverseWebGLBuilder] BuildName={buildName}");
                Debug.Log("[ViverseWebGLBuilder] OutputPath=" + outputPath);
                Debug.Log("[ViverseWebGLBuilder] Scenes:\n - " + string.Join("\n - ", scenes));

                // 4) Build
                // For WebGL, locationPathName should be a folder path (Unity will generate index.html, Build/, TemplateData/, etc.)
                var options = new BuildPlayerOptions
                {
                    scenes = scenes.ToArray(),
                    locationPathName = outputPath,
                    target = BuildTarget.WebGL,
                    options = BuildOptions.None
                };

                BuildReport report = BuildPipeline.BuildPlayer(options);

                if (report == null)
                {
                    Debug.LogError("BuildPipeline.BuildPlayer returned null report.");
                    EditorApplication.Exit(4);
                    return;
                }

                if (report.summary.result != BuildResult.Succeeded)
                {
                    Debug.LogError(
                        $"Build failed: {report.summary.result} | " +
                        $"errors={report.summary.totalErrors} warnings={report.summary.totalWarnings}"
                    );
                    EditorApplication.Exit(1);
                    return;
                }

                Debug.Log($"Build succeeded. Output: {outputPath}");
                EditorApplication.Exit(0);
            }
            catch (Exception ex)
            {
                // Any unhandled exception should mark the build as failed.
                Debug.LogException(ex);
                EditorApplication.Exit(1);
            }
        }

        // Small helper for custom CLI args
        private static string GetArgValue(string argName)
        {
            string[] args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == argName && i + 1 < args.Length)
                    return args[i + 1];
            }
            return null;
        }

        // If Scenes In Build is empty, we try to find *.unity scenes and pick a reasonable order.
        // This is a convenience fallback so the pipeline can still build without manual editor setup.
        private static List<string> FindFallbackScenes()
        {
            string[] sceneGuids = AssetDatabase.FindAssets("t:Scene", new[] { "Assets" });

            List<string> scenes = sceneGuids
                .Select(AssetDatabase.GUIDToAssetPath)
                .Where(p => p.EndsWith(".unity", StringComparison.OrdinalIgnoreCase))
                .Distinct()
                .ToList();

            if (scenes.Count == 0) return scenes;

            // Heuristic sorting:
            // - Prefer scenes under Assets/Scenes
            // - Prefer names like Main/Menu/Start
            int Score(string p)
            {
                string lower = p.ToLowerInvariant();
                int score = 0;
                if (lower.Contains("/scenes/")) score += 50;
                if (lower.Contains("main")) score += 30;
                if (lower.Contains("menu")) score += 20;
                if (lower.Contains("start")) score += 20;
                if (lower.Contains("sample")) score -= 5;
                return -score; // sort ascending
            }

            scenes.Sort((a, b) => Score(a).CompareTo(Score(b)));
            return scenes;
        }
    }
}