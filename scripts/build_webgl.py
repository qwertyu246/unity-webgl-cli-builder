"""
Unity WebGL CLI Auto Builder (Windows)

What this script does:
- (Optional) Rebuild a fresh workspace copy of your Unity project (safe mode).
- Install a shared Unity Editor build script into Assets/Editor/ (so we can call -executeMethod).
- Detect Unity editor version from ProjectSettings/ProjectVersion.txt.
- Route to the correct Unity.exe (Unity Hub installs).
- Run Unity in batchmode to build WebGL.
- Force WebGL Compression = Disabled (implemented in the C# builder).
- Validate output contains index.html and Build/ folder.

Folders (relative to --root):
- unity-workspaces/ : workspace copy of projects (ignored by git; optionally keep .gitkeep)
- unity-builds/     : WebGL output (ignored by git; optionally keep .gitkeep)
- logs/             : Unity log files (ignored by git; optionally keep .gitkeep)

Notes:
- Paths may contain spaces; subprocess.run([...]) is used to avoid quoting issues.
- If you use --no-copy, Unity may modify your source project (Library/ settings upgrades, etc).
"""

import argparse
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Map major.minor version prefix -> Unity.exe path
# Adjust these paths to match your machine / Unity Hub installation locations.
UNITY_EXE_BY_PREFIX = {
    "2018.4": r"C:\Program Files\Unity\Hub\Editor\2018.4.36f1\Editor\Unity.exe",
    "2021.3": r"C:\Program Files\Unity\Hub\Editor\2021.3.45f2\Editor\Unity.exe",
    "2022.3": r"C:\Program Files\Unity\Hub\Editor\2022.3.62f3\Editor\Unity.exe",
    "6000.4": r"C:\Program Files\Unity\Hub\Editor\6000.4.0f1\Editor\Unity.exe",
    # extra fallbacks for Unity 6 numbering
    "6000.": r"C:\Program Files\Unity\Hub\Editor\6000.4.0f1\Editor\Unity.exe",
    "6000": r"C:\Program Files\Unity\Hub\Editor\6000.4.0f1\Editor\Unity.exe",
}

# Where we install the builder inside the target Unity project
BUILDER_DEST_REL = Path("Assets/Editor/ViverseWebGLBuilder.cs")

# Must match the namespace/class/method defined in ViverseWebGLBuilder.cs
EXECUTE_METHOD = "Viverse.Build.ViverseWebGLBuilder.BuildWebGL"


def die(msg: str, code: int = 2):
    """Print error and exit."""
    print(f"[ERROR] {msg}", file=sys.stderr)
    raise SystemExit(code)


def is_unity_project_root(p: Path) -> bool:
    """Basic Unity project root check."""
    return (p / "Assets").is_dir() and (p / "Packages").is_dir() and (p / "ProjectSettings").is_dir()


def sanitize_name(name: str) -> str:
    """Make a folder name safe-ish for Windows paths, but keep it readable."""
    name = name.strip().strip(".")
    name = re.sub(r"[<>:\"/\\|?*]+", "_", name)
    name = re.sub(r"\s+", " ", name)
    return name


def parse_editor_version(project_root: Path) -> str:
    """
    Read Unity editor version from:
      ProjectSettings/ProjectVersion.txt
    Example line:
      m_EditorVersion: 6000.4.0f1
    """
    pv = project_root / "ProjectSettings/ProjectVersion.txt"
    if not pv.exists():
        die(f"Missing {pv}. Not a valid Unity project root? project_root={project_root}")
    text = pv.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"m_EditorVersion:\s*([0-9a-zA-Z\.\-]+)", text)
    if not m:
        die(f"Could not parse m_EditorVersion from {pv}")
    return m.group(1).strip()


def pick_unity_exe(editor_version: str) -> str:
    """
    Select Unity.exe based on editor version prefix.
    - Prefer major.minor match (e.g. '2022.3')
    - Then try startswith matches for fallbacks (e.g. '6000.')
    """
    parts = editor_version.split(".")
    if len(parts) >= 2:
        prefix = f"{parts[0]}.{parts[1]}"
        if prefix in UNITY_EXE_BY_PREFIX:
            return UNITY_EXE_BY_PREFIX[prefix]

    for k, v in UNITY_EXE_BY_PREFIX.items():
        if editor_version.startswith(k):
            return v

    die(f"No Unity.exe mapping for editor version: {editor_version}. Add it to UNITY_EXE_BY_PREFIX.")


def ensure_unity_exe_exists(unity_exe: str):
    """Fail fast if Unity.exe path is wrong."""
    if not Path(unity_exe).exists():
        die(f"Unity.exe not found: {unity_exe}")


def robocopy_project(source: Path, dest: Path):
    """
    Copy a Unity project directory using robocopy.
    - /MIR mirrors the directory tree (dest becomes exact copy of source).
    - robocopy uses special exit codes:
        0-7 => success (including 'copied some files')
        >=8 => failure
    """
    cmd = [
        "robocopy",
        str(source),
        str(dest),
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
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if proc.returncode >= 8:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        die(f"robocopy failed with code {proc.returncode}")


def prepare_target_project(source_project: Path, workspace_project: Path, no_copy: bool) -> Path:
    """
    Decide which project Unity should build:
    - no_copy=True  => build directly in source_project (fast, but modifies it)
    - no_copy=False => rebuild workspace by copying source -> workspace (safe mode)
    """
    if no_copy:
        return source_project

    # Mode 1: always rebuild workspace fresh
    if workspace_project.exists():
        shutil.rmtree(workspace_project)

    workspace_project.parent.mkdir(parents=True, exist_ok=True)

    print("[INFO] Copying project to workspace via robocopy...")
    robocopy_project(source_project, workspace_project)
    return workspace_project


def install_builder(builder_source: Path, target_project: Path):
    """
    Copy shared builder .cs into target Unity project:
      <target_project>/Assets/Editor/ViverseWebGLBuilder.cs
    """
    if not builder_source.exists():
        die(f"Builder .cs not found: {builder_source}")

    dest = target_project / BUILDER_DEST_REL
    dest.parent.mkdir(parents=True, exist_ok=True)

    content = builder_source.read_text(encoding="utf-8", errors="ignore")
    dest.write_text(content, encoding="utf-8", newline="\n")


def run_unity_build(unity_exe: str, project_path: Path, output_path: Path, log_path: Path, build_name: str) -> int:
    """
    Run Unity in batch mode to build WebGL via -executeMethod.
    The C# builder reads:
      -viverseBuildOutput <path>
      -viverseBuildName <name>
    """
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
        # custom args parsed inside the C# builder:
        "-viverseBuildOutput",
        str(output_path),
        "-viverseBuildName",
        build_name,
    ]

    print("[INFO] Running Unity CLI build:")
    print("       " + " ".join([f'"{a}"' if " " in a else a for a in args]))

    # Unity writes most info to -logFile, but capture stdout/stderr for quick diagnostics.
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if proc.stdout.strip():
        print("[UNITY-STDOUT]\n" + proc.stdout)
    if proc.stderr.strip():
        print("[UNITY-STDERR]\n" + proc.stderr, file=sys.stderr)

    return proc.returncode


def validate_webgl_output(output_path: Path):
    """
    Validate minimal WebGL build output structure.
    Unity WebGL build normally produces:
      <output>/index.html
      <output>/Build/...
    """
    index_html = output_path / "index.html"
    build_dir = output_path / "Build"
    if not index_html.exists():
        die(f"Build output invalid: missing {index_html}")
    if not build_dir.exists():
        die(f"Build output invalid: missing {build_dir}")
    print("[INFO] Output validated:", output_path)


def main():
    ap = argparse.ArgumentParser(description="Unity WebGL CLI builder (optional workspace copy; compression disabled via C# builder).")
    ap.add_argument("--source-project", required=True, help="Path to the SOURCE Unity project.")
    ap.add_argument("--root", required=True, help="Automation root folder (contains unity-workspaces/unity-builds/logs).")
    ap.add_argument("--builder", default="", help="Path to shared builder .cs (default: <root>/scripts/ViverseWebGLBuilder.cs).")
    ap.add_argument("--project-name", default="", help="Optional override project name for output folders.")
    ap.add_argument("--build-name", default="", help="Optional build name label (for logs). Default: timestamp.")
    ap.add_argument("--no-copy", action="store_true", help="Build directly in source project (faster, but will modify it).")
    args = ap.parse_args()

    source_project = Path(args.source_project)
    root = Path(args.root)

    if not is_unity_project_root(source_project):
        die(f"source-project is not a Unity project root: {source_project}")

    # Name for workspace/output folders
    project_name = sanitize_name(args.project_name or source_project.name)
    build_name = args.build_name or datetime.now().strftime("%Y%m%d-%H%M%S")

    # Root subfolders
    workspaces_root = root / "unity-workspaces"
    builds_root = root / "unity-builds"
    logs_root = root / "logs"

    # Shared builder script path (kept in this tool repo)
    default_builder = root / "scripts" / "ViverseWebGLBuilder.cs"
    builder_path = Path(args.builder) if args.builder else default_builder

    # Derived paths
    workspace_project = workspaces_root / project_name
    output_path = builds_root / project_name / "webgl"
    log_path = logs_root / f"{project_name}_{build_name}.log"

    print("[INFO] Source project  :", source_project)
    print("[INFO] no_copy        :", args.no_copy)
    print("[INFO] Builder source :", builder_path)
    print("[INFO] Workspace proj  :", workspace_project)
    print("[INFO] Output (WebGL)  :", output_path)
    print("[INFO] Log file        :", log_path)

    # Decide build target project path (source or workspace)
    target_project = prepare_target_project(source_project, workspace_project, no_copy=args.no_copy)

    # Ensure builder exists in target project so -executeMethod works
    install_builder(builder_path, target_project)

    # Route correct Unity.exe
    editor_version = parse_editor_version(target_project)
    unity_exe = pick_unity_exe(editor_version)
    ensure_unity_exe_exists(unity_exe)

    print("[INFO] Project editor version:", editor_version)
    print("[INFO] Using Unity.exe        :", unity_exe)

    # Run Unity build
    rc = run_unity_build(
        unity_exe=unity_exe,
        project_path=target_project,
        output_path=output_path,
        log_path=log_path,
        build_name=build_name,
    )

    # Unity sometimes returns 0 even with errors, so always validate output.
    if rc != 0:
        print(f"[WARN] Unity returned non-zero exit code: {rc}", file=sys.stderr)

    validate_webgl_output(output_path)

    print("[SUCCESS] WebGL build complete.")
    print("          Output:", output_path)
    print("          Log   :", log_path)
    sys.exit(0)


if __name__ == "__main__":
    main()