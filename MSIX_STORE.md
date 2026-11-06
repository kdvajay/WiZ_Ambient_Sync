# Microsoft Store MSIX workflow

The Store package is an additional Windows distribution channel. It does not
replace the portable executables or the Inno Setup installer produced by the
existing Windows workflow.

## Local packaging

Build the normal Windows executables first, then package them:

```powershell
python build.py
.\build-msix.ps1 `
  -IdentityName "<Partner Center identity name>" `
  -Publisher "<Partner Center publisher>"
```

The script reads `VERSION`, converts `2.0.0` to the numeric MSIX
version `2.0.0.0`, stages the existing `WiZAmbientSync.exe` and
`wiz-engine.exe`, and creates an x64 package under `msix-output`.

`makeappx.exe` is required. It is normally available in a Windows development
environment or on the GitHub-hosted Windows runner. A local development
certificate can be supplied with `-CertificatePath` and
`-CertificatePassword`; Store submission identity must match Partner Center.

## GitHub Actions

The separate workflow is [`.github/workflows/windows-msix.yml`](./.github/workflows/windows-msix.yml).
It uses Python 3.12, reuses `build.py`, and uploads an unsigned MSIX artifact.

The workflow includes the current Partner Center identity as its fallback. You
may also set these repository variables to keep the identity configurable:

- `MSIX_IDENTITY_NAME` — the exact package identity assigned by Partner Center.
- `MSIX_PUBLISHER` — the exact publisher string assigned by Partner Center.

The current fallback values are:

- `Kdvajay.WiZAmbientSync`
- `CN=8996203F-A5D5-4486-9239-288C30EF9DE1`

## Package behavior

The package entry point is the existing PyInstaller GUI. The engine remains a
sidecar executable in the same package and is started by the GUI through its
existing `QProcess` contract. User configuration continues to live in the
per-user configuration directory; the package installation directory is not
used for mutable state.

The manifest declares local/private network access for WiZ UDP discovery and
control, internet client access for the packaged desktop application, and
full-trust execution for the Win32 GUI and engine processes. It also declares a
disabled StartupTask extension for future Store-compatible startup handling.

## Store submission checklist

Before submission, replace the placeholder identity values and provide:

- Store listing description, screenshots, category, support URL, and privacy
  policy URL.
- Partner Center publisher identity matching the manifest.
- Numeric package version and x64 architecture selection.
- Certification testing on a clean Windows installation.
- Validation of loopback audio, screen capture, tray operation, LAN discovery,
  direct device control, engine start/stop, upgrade, and uninstall behavior.

Audio and screen samples are processed locally. WiZ communication is sent to
the configured devices on the local network; the application does not require
a cloud account or analytics service.
