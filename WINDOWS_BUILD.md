# Windows EXE and installer build

This project can produce both a portable Windows build and a standard Windows installer.

## Option A — Build locally on Windows

Requirements:
- Windows 10 or 11, 64-bit
- Python 3.12, 64-bit, with **Add Python to PATH**
- Inno Setup 6 (optional if you only want the portable EXEs)

Download Inno Setup 6: https://jrsoftware.org/isdl.php

1. Extract this project folder.
2. Open PowerShell in the extracted folder.
3. If PowerShell blocks scripts, run this for the current terminal only:

   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   ```

4. Run:

   ```powershell
   .\build-windows.ps1
   ```

Outputs:
- `dist\WiZAmbientSync.exe` — desktop interface
- `dist\wiz-engine.exe` — background lighting engine
- `installer-output\WiZ-Ambient-Sync-Beta-Setup-1.1.3-beta.1-x64.exe` — installer (if Inno Setup is installed)

Keep the two EXEs in `dist` together. The beta installer uses a separate application identity and installs alongside the stable release. It stores beta preferences separately under `%APPDATA%\WiZAmbientSyncBeta\config.json` and creates Start Menu shortcuts. It can optionally create a desktop shortcut.

## Option B — Build on GitHub Actions

The included workflow `.github/workflows/windows-build.yml` builds on a Windows runner.

1. Create a private or public GitHub repository and upload the contents of this folder.
2. Push the project to GitHub.
3. Open the repository's **Actions** tab.
4. Select **Build Windows app and installer**.
5. Click **Run workflow**.
6. When complete, download the `WiZ-Ambient-Sync-Windows-x64` artifact.

The artifact contains both portable EXEs and the installer.

## Installing

Run the setup EXE. Windows may show SmartScreen because this preview is not code-signed. Only run builds you created or trust. Choose whether to create a desktop shortcut. The installer offers to launch the app when setup finishes.

## User configuration

The beta app stores configuration under `%APPDATA%\WiZAmbientSyncBeta\config.json`, with tuning and device-control state alongside it. Uninstall intentionally leaves these files intact so your device IPs and preferences are preserved.

## Beta 1.1.3 changes

- Minimal Liquid Glass-inspired UI with layered translucent surfaces, subtle borders, softer corners and restrained lavender accents.
- Light, Dark and System appearance modes use the same component styling.
- Improved hover/focus/pressed states, sliders, checkboxes, tables, scrollbars and tooltips.
- No blur effects, browser framework or new runtime dependencies; theme changes remain presentation-only.
- The Stage 4.0 engine is byte-for-byte unchanged from beta 1.1.2-beta.1. Audio capture, analysis, colour flow, sender loops and dispatch order are untouched.
- Device status polling remains paused while syncing to protect responsiveness.

## Current preview limitations

- This is an unsigned preview; it is not yet suitable for public distribution.
- No Windows executable or installer was compiled in the Linux build environment used to prepare this source package. Run the Windows build script or GitHub Actions workflow to create native Windows binaries.
- Network discovery, audio capture, screen capture, and WiZ device control still need testing on the target Windows PC and actual devices.
- The installer is x64. Windows on ARM is not currently targeted.
