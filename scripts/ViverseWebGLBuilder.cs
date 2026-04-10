// Shared builder script (copied into target project's Assets/Editor/ before build)
// Forces WebGL builds with Compression Disabled for maximal hosting compatibility.

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
        public static void BuildWebGL()
        {
            try
            {
                var outputPath = GetArgValue("-viverseBuildOutput");
                var buildName = GetArgValue("-viverseBuildName") ?? "build";

                if (string.IsNullOrEmpty(outputPath))
                {
                    Debug.LogError("Missing required arg: -viverseBuildOutput <path>");
                    EditorApplication.Exit(2);
                    return;
                }

                Directory.CreateDirectory(outputPath);

                // Switch target to WebGL
                EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.WebGL, BuildTarget.WebGL);

                // Force WebGL compression = Disabled (avoid .br/.gz server header issues)
#if UNITY_2018_4_OR_NEWER
                PlayerSettings.WebGL.compressionFormat = WebGLCompressionFormat.Disabled;
#endif

                // Scene list: use existing Scenes In Build; fallback if empty
                var scenes = EditorBuildSettings.scenes
                    .Where(s => s != null && s.enabled && !string.IsNullOrEmpty(s.path))
                    .Select(s => s.path)
                    .ToList();

                if (scenes.Count == 0)
                {
                    scenes = FindFallbackScenes();
                }

                if (scenes.Count == 0)
                {
                    Debug.LogError("No scenes found to build. Configure File > Build Settings... or ensure at least one .unity scene exists.");
                    EditorApplication.Exit(3);
                    return;
                }

                Debug.Log($"[ViverseWebGLBuilder] BuildName={buildName}");
                Debug.Log("[ViverseWebGLBuilder] OutputPath=" + outputPath);
                Debug.Log("[ViverseWebGLBuilder] Scenes:\n - " + string.Join("\n - ", scenes));

                var options = new BuildPlayerOptions
                {
                    scenes = scenes.ToArray(),
                    locationPathName = outputPath,
                    target = BuildTarget.WebGL,
                    options = BuildOptions.None
                };

                var report = BuildPipeline.BuildPlayer(options);
                if (report == null)
                {
                    Debug.LogError("BuildPipeline.BuildPlayer returned null report.");
                    EditorApplication.Exit(4);
                    return;
                }

                if (report.summary.result != BuildResult.Succeeded)
                {
                    Debug.LogError($"Build failed: {report.summary.result} | errors={report.summary.totalErrors} warnings={report.summary.totalWarnings}");
                    EditorApplication.Exit(1);
                    return;
                }

                Debug.Log($"Build succeeded. Output: {outputPath}");
                EditorApplication.Exit(0);
            }
            catch (Exception ex)
            {
                Debug.LogException(ex);
                EditorApplication.Exit(1);
            }
        }

        private static string GetArgValue(string argName)
        {
            var args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == argName && i + 1 < args.Length)
                    return args[i + 1];
            }
            return null;
        }

        private static List<string> FindFallbackScenes()
        {
            var sceneGuids = AssetDatabase.FindAssets("t:Scene", new[] { "Assets" });
            var scenes = sceneGuids
                .Select(AssetDatabase.GUIDToAssetPath)
                .Where(p => p.EndsWith(".unity", StringComparison.OrdinalIgnoreCase))
                .Distinct()
                .ToList();

            if (scenes.Count == 0) return scenes;

            int Score(string p)
            {
                var lower = p.ToLowerInvariant();
                int score = 0;
                if (lower.Contains("/scenes/")) score += 50;
                if (lower.Contains("main")) score += 30;
                if (lower.Contains("menu")) score += 20;
                if (lower.Contains("start")) score += 20;
                if (lower.Contains("sample")) score -= 5;
                return -score;
            }

            scenes.Sort((a, b) => Score(a).CompareTo(Score(b)));
            return scenes;
        }
    }
}