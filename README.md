# Unity WebGL CLI Auto Build (VIVERSE)

這個 repo 是一個 **Unity WebGL CLI 自動化建置工具**（Python orchestrator + Unity `-executeMethod`），用來把指定 Unity 專案輸出成可部署/可 publish 的 WebGL build。

> 注意：本 repo **不包含任何 Unity 專案本體**。Unity 專案（source project）放在你的電腦其他位置，本工具只會讀取/複製後建置。

---

## 資料夾結構

```
autoBulid/
  scripts/                # Python 與共用 Builder 腳本
  unity-workspaces/       # (可選) 工作區：複製 Unity 專案後在這裡 build（很大，不進 git）
  unity-builds/           # WebGL 輸出（很大，不進 git）
  logs/                   # Unity log（不進 git）
```

Git 只追蹤：
- `scripts/`
- `.gitignore`
- `README.md`
- `logs/.gitkeep`, `unity-workspaces/.gitkeep`, `unity-builds/.gitkeep`

---

## 需求

- Windows
- Python 3.x（建議 3.10+）
- Unity Hub 安裝的 Unity Editor（至少你要 build 的那個版本）
- WebGL module 已安裝（Unity Hub → Installs → Add modules → WebGL Build Support）

本機 Unity Editor（範例路徑）：
- `C:\Program Files\Unity\Hub\Editor\2018.4.36f1\Editor\Unity.exe`
- `C:\Program Files\Unity\Hub\Editor\2021.3.45f2\Editor\Unity.exe`
- `C:\Program Files\Unity\Hub\Editor\2022.3.62f3\Editor\Unity.exe`
- `C:\Program Files\Unity\Hub\Editor\6000.4.0f1\Editor\Unity.exe`

---

## 腳本功能概述

- 讀取 `ProjectSettings/ProjectVersion.txt` 判斷 `m_EditorVersion`
- 依版本選擇對應 `Unity.exe`
- 注入/更新 `Assets/Editor/ViverseWebGLBuilder.cs`
- 使用 Unity CLI 進行 WebGL build
- 強制 **WebGL Compression = Disabled**（避免 `.br` / server header 問題）
- 檢查輸出資料夾必須包含 `index.html` 與 `Build/`

---

## 使用方法

### 1) 建置（安全模式：copy 到 workspace 再 build）
這是推薦模式：不會污染原始 Unity 專案。

```powershell
cd "C:\Users\VPA313\Documents\david\unity-cli-bulid\autoBulid"

python .\scripts\build_webgl.py `
  --source-project "C:\Users\VPA313\Unity\My project test01" `
  --root "C:\Users\VPA313\Documents\david\unity-cli-bulid\autoBulid"
```

輸出位置（預設）：
- Workspace：`.\unity-workspaces\<ProjectName>\`
- WebGL：`.\unity-builds\<ProjectName>\webgl\`
- Log：`.\logs\<ProjectName>_<timestamp>.log`

### 2) 建置（快速模式：直接 build 原專案，不 copy）
會修改原始 Unity 專案（可能產生 `Library/`、更新設定或升級），請自行承擔風險：

```powershell
python .\scripts\build_webgl.py `
  --no-copy `
  --source-project "C:\Users\VPA313\Unity\My project test01" `
  --root "C:\Users\VPA313\Documents\david\unity-cli-bulid\autoBulid"
```

---

## 常見問題

### Q1: Unity build 失敗要看哪裡？
看 log 檔：
- `.\logs\*.log`

建議從 log 末尾往上找第一個 `Error`。

### Q2: 為什麼強制 Compression Disabled？
為了最大相容性（尤其是自架/不同平台 hosting 時，Brotli/Gzip 常因 header 設定導致載入失敗）。
等流程穩定後，可以再做 Gzip/Brotli 與 server header 的最佳化。

### Q3: 專案沒設定 Scenes in Build 怎麼辦？
Builder 會嘗試 fallback：從 `Assets/` 底下尋找 `.unity` scenes，並用簡單 heuristic 排序後建置。
最穩的作法仍是：在 Unity Editor 的 `File > Build Settings...` 正確設定 Scenes。

---

## 自訂 Builder 腳本

預設會使用：
- `.\scripts\ViverseWebGLBuilder.cs`

你可以改這個檔案來調整 WebGL build 設定，腳本每次 build 都會將它複製到目標專案的：
- `Assets/Editor/ViverseWebGLBuilder.cs`