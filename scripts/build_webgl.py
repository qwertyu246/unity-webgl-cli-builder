import argparse
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
    "6000.": r"C:\Program Files\Unity\Hub\Editor\6000.4.0f1\Editor\Unity.exe",
    "6000": r"C:\Program Files\Unity\Hub\Editor\6000.4.0f1\Editor\Unity.exe",
}

BUILDER_DEST_REL = Path("Assets/Editor/ViverseWebGLBuilder.cs")
EXECUTE_METHOD = "Viverse.Build.ViverseWebGLBuilder.BuildWebGL"


def die(msg: str, code: int = 2):
    print(f"[ERROR] {msg}", file=sys.stderr)
    raise SystemExit(code)


def is_unity_project_root(p: Path) -> bool:
    return (p / "Assets").is_dir() and (p / "Packages").is_dir() and (p / "ProjectSettings").is_dir()


def sanitize_name(name: str) -> str:
    name = name.strip().strip(".")
    name = re.sub(r"[<>:\"/\\|?*]+", "_", name)
    name = re.sub(r"\s+", " ", name)
    return name


def parse_editor_version(project_root: Path) -> str:
    pv = project_root / "ProjectSettings/ProjectVersion.txt"
    if not pv.exists():
        die(f"Missing {pv}. Not a valid Unity project root? project_root={project_root}")
    text = pv.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"m_EditorVersion:\s*([0-9a-zA-Z\.\-]+)", text)
    if not m:
        die(f"Could not parse m_EditorVersion from {pv}")
    return m.group(1).strip()


def pick_unity_exe(editor_version: str) -> str:
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
    if not Path(unity_exe).exists():
        die(f"Unity.exe not found: {unity_exe}")


def robocopy_project(source: Path, dest: Path):
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
    # Robocopy success codes are 0-7, failure is >=8
    if proc.returncode >= 8:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        die(f"robocopy failed with code {proc.returncode}")


def prepare_target_project(source_project: Path, workspace_project: Path, no_copy: bool) -> Path:
    """
    Returns the project path that Unity should build:
    - if no_copy=True: build directly in source_project
    - else: rebuild workspace and build there
    """
    if no_copy:
        return source_project

    if workspace_project.exists():
        shutil.rmtree(workspace_project)

    workspace_project.parent.mkdir(parents=True, exist_ok=True)
    print("[INFO] Copying project to workspace via robocopy...")
    robocopy_project(source_project, workspace_project)
    return workspace_project


def install_builder(builder_source: Path, target_project: Path):
    if not builder_source.exists():
        die(f"Builder .cs not found: {builder_source}")

    dest = target_project / BUILDER_DEST_REL
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = builder_source.read_text(encoding="utf-8", errors="ignore")
    dest.write_text(content, encoding="utf-8", newline="\n")


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
        "-viverseBuildOutput",
        str(output_path),
        "-viverseBuildName",
        build_name,
    ]

    print("[INFO] Running Unity CLI build:")
    print("       " + " ".join([f'"{a}"' if " " in a else a for a in args]))

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
    ap = argparse.ArgumentParser(description="Unity WebGL CLI builder (optional workspace copy, compression disabled via builder script).")
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

    project_name = sanitize_name(args.project_name or source_project.name)
    build_name = args.build_name or datetime.now().strftime("%Y%m%d-%H%M%S")

    workspaces_root = root / "unity-workspaces"
    builds_root = root / "unity-builds"
    logs_root = root / "logs"
    default_builder = root / "scripts" / "ViverseWebGLBuilder.cs"
    builder_path = Path(args.builder) if args.builder else default_builder

    workspace_project = workspaces_root / project_name
    output_path = builds_root / project_name / "webgl"
    log_path = logs_root / f"{project_name}_{build_name}.log"

    print("[INFO] Source project :", source_project)
    print("[INFO] no_copy       :", args.no_copy)
    print("[INFO] Target builder:", builder_path)
    print("[INFO] Workspace proj :", workspace_project)
    print("[INFO] Output (WebGL) :", output_path)
    print("[INFO] Log file       :", log_path)

    target_project = prepare_target_project(source_project, workspace_project, no_copy=args.no_copy)

    # Install builder into target project
    install_builder(builder_path, target_project)

    # Route Unity version based on target project (workspace or source)
    editor_version = parse_editor_version(target_project)
    unity_exe = pick_unity_exe(editor_version)
    ensure_unity_exe_exists(unity_exe)

    print("[INFO] Project editor version:", editor_version)
    print("[INFO] Using Unity.exe        :", unity_exe)

    rc = run_unity_build(
        unity_exe=unity_exe,
        project_path=target_project,
        output_path=output_path,
        log_path=log_path,
        build_name=build_name,
    )

    if rc != 0:
        print(f"[WARN] Unity returned non-zero exit code: {rc}", file=sys.stderr)

    validate_webgl_output(output_path)

    print("[SUCCESS] WebGL build complete.")
    print("          Output:", output_path)
    print("          Log   :", log_path)
    sys.exit(0)


if __name__ == "__main__":
    main()