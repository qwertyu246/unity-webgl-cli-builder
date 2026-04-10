import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

UNITY_EXE_BY_PREFIX = {
    "2018.4": r"C:\Program Files\Unity\Hub\Editor\2018.4.36f1\Editor\Unity.exe",
    "2021.3": r"C:\Program Files\Unity\Hub\Editor\2021.3.45f2\Editor\Unity.exe",
    "2022.3": r"C:\Program Files\Unity\Hub\Editor\2022.3.62f3\Editor\Unity.exe",
    "6000.4": r"C:\Program Files\Unity\Hub\Editor\6000.4.0f1\Editor\Unity.exe",
    "6000.": r"C:\Program Files\Unity\Hub\Editor\6000.4.0f1\Editor\Unity.exe",  # fallback for Unity 6
    "6000": r"C:\Program Files\Unity\Hub\Editor\6000.4.0f1\Editor\Unity.exe",
}

BUILDER_REL_PATH = Path("Assets/Editor/ViverseWebGLBuilder.cs")
EXECUTE_METHOD = "Viverse.Build.ViverseWebGLBuilder.BuildWebGL"


def die(msg: str, code: int = 2):
    print(f"[ERROR] {msg}", file=sys.stderr)
    raise SystemExit(code)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def write_text(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def is_unity_project_root(p: Path) -> bool:
    return (p / "Assets").is_dir() and (p / "Packages").is_dir() and (p / "ProjectSettings").is_dir()


def sanitize_name(name: str) -> str:
    # Keep it readable but filesystem-safe
    name = name.strip().strip(".")
    name = re.sub(r"[<>:\"/\\|?*]+", "_", name)
    name = re.sub(r"\s+", " ", name)
    return name


def parse_editor_version(project_root: Path) -> str:
    pv = project_root / "ProjectSettings/ProjectVersion.txt"
    if not pv.exists():
        die(f"Missing {pv}. Not a valid Unity project root? project_root={project_root}")
    text = read_text(pv)
    m = re.search(r"m_EditorVersion:\s*([0-9a-zA-Z\.\-]+)", text)
    if not m:
        die(f"Could not parse m_EditorVersion from {pv}")
    return m.group(1).strip()


def pick_unity_exe(editor_version: str) -> str:
    # Prefer exact major.minor match (e.g. 2022.3)
    parts = editor_version.split(".")
    if len(parts) >= 2:
        prefix = f"{parts[0]}.{parts[1]}"
        # handle Unity 6: 6000.4.0f1 etc.
        if prefix in UNITY_EXE_BY_PREFIX:
            return UNITY_EXE_BY_PREFIX[prefix]
        # try 2018.4, 2021.3, 2022.3
        if prefix in UNITY_EXE_BY_PREFIX:
            return UNITY_EXE_BY_PREFIX[prefix]

    # fallback: any key that matches start
    for k, v in UNITY_EXE_BY_PREFIX.items():
        if editor_version.startswith(k):
            return v

    die(f"No Unity.exe mapping for editor version: {editor_version}. Add it to UNITY_EXE_BY_PREFIX.")


def ensure_unity_exe_exists(unity_exe: str):
    if not Path(unity_exe).exists():
        die(f"Unity.exe not found: {unity_exe}")


def robocopy_available() -> bool:
    # Robocopy exists by default on Windows
    return True


def copy_project_to_workspace(source: Path, workspace_project: Path):
    # Mode 1: delete workspace then copy fresh
    if workspace_project.exists():
        shutil.rmtree(workspace_project)

    workspace_project.parent.mkdir(parents=True, exist_ok=True)

    # Copy with robocopy for speed and long paths robustness
    # /MIR mirrors directory tree, /NFL /NDL less output, /R:1 /W:1 reduce retries
    # /XJ avoid junction loops
    if robocopy_available():
        cmd = [
            "robocopy",
            str(source),
            str(workspace_project),
            "/MIR",
            "/R:1",
            "/W:1",
            "/NFL",
            "/NDL",
            "/NP",
            "/NJH",
            "/NJS",
            "/XJ",
        ]
        print("[INFO] Copying project to workspace via robocopy...")
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        # Robocopy uses special exit codes: 0-7 are success-ish, >=8 are failure
        if proc.returncode >= 8:
            print(proc.stdout)
            print(proc.stderr, file=sys.stderr)
            die(f"robocopy failed with code {proc.returncode}")
        return

    # Fallback: shutil copytree (slower)
    print("[INFO] Copying project to workspace via shutil.copytree (fallback)...")
    shutil.copytree(source, workspace_project)


def inject_builder_cs(workspace_project: Path):
    cs_path = workspace_project / BUILDER_REL_PATH
    write_text(cs_path, BUILDER_CS)


def run_unity_build(unity_exe: str, project_path: Path, output_path: Path, log_path: Path, build_name: str):
    output_path.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    args = [
        unity_exe,
        "-batchmode",
        "-quit",
        "-projectPath",
        str(project_path),
        "-executeMethod",
        EXECUTE_METHOD,
        "-logFile",
        str(log_path),
        # custom args we parse inside Unity
        "-viverseBuildOutput",
        str(output_path),
        "-viverseBuildName",
        build_name,
    ]

    print("[INFO] Running Unity CLI build:")
    print("       " + " ".join([f'"{a}"' if " " in a else a for a in args]))

    # Do not capture stdout/stderr because Unity writes to logFile; but we keep them for quick diagnostics.
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if proc.stdout.strip():
        print("[UNITY-STDOUT]\n" + proc.stdout)
    if proc.stderr.strip():
        print("[UNITY-STDERR]\n" + proc.stderr, file=sys.stderr)

    return proc.returncode


def validate_webgl_output(output_path: Path):
    index_html = output_path / "index.html"
    build_dir = output_path / "Build"
    if not index_html.exists():
        die(f"Build output invalid: missing {index_html}")
    if not build_dir.exists():
        die(f"Build output invalid: missing {build_dir}")
    print("[INFO] Output validated:", output_path)


def main():
    ap = argparse.ArgumentParser(description="Unity WebGL CLI builder (workspace rebuild mode, compression disabled).")
    ap.add_argument("--source-project", required=True, help="Path to the SOURCE Unity project (will not be modified).")
    ap.add_argument("--root", required=True, help="Automation root folder (contains unity-workspaces/unity-builds/logs).")
    ap.add_argument("--project-name", default="", help="Optional override project name for output folders.")
    ap.add_argument("--build-name", default="", help="Optional build name label (for logs). Default: timestamp.")
    args = ap.parse_args()

    source_project = Path(args.source_project)
    root = Path(args.root)

    if not is_unity_project_root(source_project):
        die(f"source-project is not a Unity project root: {source_project}")

    project_name = sanitize_name(args.project_name or source_project.name)
    build_name = args.build_name or datetime.now().strftime("%Y%m%d-%H%M%S")

    workspaces_root = root / "unity-workspaces"
    builds_root = root / "unity-builds"
    logs_root = root / "logs"

    workspace_project = workspaces_root / project_name
    output_path = builds_root / project_name / "webgl"
    log_path = logs_root / f"{project_name}_{build_name}.log"

    print("[INFO] Source project :", source_project)
    print("[INFO] Workspace proj :", workspace_project)
    print("[INFO] Output (WebGL) :", output_path)
    print("[INFO] Log file       :", log_path)

    # 1) copy to workspace (fresh)
    copy_project_to_workspace(source_project, workspace_project)

    # 2) inject builder
    inject_builder_cs(workspace_project)

    # 3) route Unity version
    editor_version = parse_editor_version(workspace_project)
    unity_exe = pick_unity_exe(editor_version)
    ensure_unity_exe_exists(unity_exe)

    print("[INFO] Project editor version:", editor_version)
    print("[INFO] Using Unity.exe        :", unity_exe)

    # 4) run Unity build
    rc = run_unity_build(
        unity_exe=unity_exe,
        project_path=workspace_project,
        output_path=output_path,
        log_path=log_path,
        build_name=build_name,
    )

    # Unity sometimes returns 0 even with errors; so we always validate output
    if rc != 0:
        print(f"[WARN] Unity returned non-zero exit code: {rc}", file=sys.stderr)

    # 5) validate output
    validate_webgl_output(output_path)

    print("[SUCCESS] WebGL build complete.")
    print("          Output:", output_path)
    print("          Log   :", log_path)
    sys.exit(0)


BUILDER_CS = r'''// Auto-generated by scripts/build_webgl.py
// This file is injected into the WORKSPACE copy of the project (not your source project).
// It forces WebGL builds with Compression Disabled for maximal hosting compatibility.

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

                // Ensure output directory exists
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
                {
                    return args[i + 1];
                }
            }
            return null;
        }

        private static List<string> FindFallbackScenes()
        {
            // If Scenes In Build is empty, try to find any .unity scenes under Assets and pick a reasonable default order.
            // Heuristic: prefer a scene under Assets/Scenes, or names like Main/Menu/Start.
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
                return -score; // sort ascending
            }

            scenes.Sort((a, b) => Score(a).CompareTo(Score(b)));
            return scenes;
        }
    }
}
'''

if __name__ == "__main__":
    main()