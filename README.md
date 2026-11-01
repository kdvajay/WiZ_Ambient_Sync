## v1.2.2-beta.1 update

The Overview page now includes compact device tiles with a confirmed-state power switch and live brightness progress bar. Brightness is converted from pywizlight's 0–255 raw value to a 0–100% display. The Live Music Activity panel is shorter. See `BETA_CHANGELOG.md` for details.

# WiZ Ambient Sync 1.1.4-beta.1 — Desktop App Preview

A tray-based desktop shell around the WiZ Ambient Sync V2 Stage 4.0 Musical Flow engine.
This package uses the **Strip Musical Response V1.1** engine and its 45% strip minimum brightness.

## What is included

- Windows and Linux build scripts (build each release on its target OS).
- Separate engine process, so the Qt window can close while syncing continues in the tray.
- Tray menu: open, start, stop, quit.
- Settings for primary bulb, secondary bulb, strip IP and monitor.
- **Find WiZ devices** network discovery: scan the local LAN, query each device for its model name and supported colour/dimming/effect capabilities, show a best-effort bulb/strip classification, and suggest a role before saving it to the config.
- Per-user writable configuration; the original tuned defaults are copied on first launch.
- Optional launch at login.
- Engine output is shown in the app log.

## Build

Use Python 3.11 or 3.12. Build on the operating system you intend to distribute for.
Do not expect a Windows executable built on Linux (or vice versa).

### Windows 10/11

1. Install 64-bit Python 3.11 or 3.12.
2. Open PowerShell in this folder.
3. Run:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python build.py
```

The `dist` folder contains `WiZAmbientSync.exe` and `wiz-engine.exe`.
Keep both files in the same folder when distributing.

### Ubuntu / Debian / Fedora

Install audio and build prerequisites first. On Ubuntu/Debian:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-dev build-essential libpulse0 libpulse-dev pulseaudio-utils
```

On Fedora, install the equivalent Python development, compiler and PulseAudio client/development packages.

Then:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python build.py
```

The `dist` folder contains `WiZAmbientSync` and `wiz-engine`.
Keep both files in the same folder.

### Arch Linux

```bash
sudo pacman -S --needed python python-pip base-devel libpulse pipewire-pulse
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python build.py
```

If your system uses PipeWire, make sure `pipewire-pulse` is running and the audio output is visible to PulseAudio-compatible applications.

## First launch

1. Run the desktop app.
2. Enter the local IP addresses of your WiZ devices. Leave unused device fields blank.
3. Select the screen to capture.
4. Save settings and choose **Start syncing**.
5. Closing the settings window hides it in the tray. Use the tray menu to stop or quit.
6. Enable **Launch WiZ Ambient Sync when I sign in** if you want the app to start automatically.

The app stores its editable config in the OS user config directory:
- Windows: `%APPDATA%\\WiZAmbientSync\\config.json`
- Linux: `~/.config/WiZAmbientSync/config.json`

## Linux desktop notes

- Tray icons require a desktop environment / shell with StatusNotifier or AppIndicator tray support. Some minimal Wayland sessions do not display legacy tray icons by default.
- The current screen sampler uses `mss`. X11 is the intended Linux capture path for this preview. Wayland compositors (including Hyprland) may restrict direct screen capture; a PipeWire/portal capture backend is a follow-up requirement for reliable native Wayland support.
- Audio loopback depends on the system audio server. PipeWire users should have `pipewire-pulse` active.

## Important limitations of this preview

- This is a **source/build package**, not precompiled Windows and Linux installers. Build separately on each target OS.
- No physical WiZ hardware test has been performed as part of packaging.
- WiZ devices must be reachable on the same local network; use reserved DHCP addresses for reliability.
- The app currently exposes device IPs and monitor selection. Advanced tuning remains editable in the JSON config.
- Closing the app via the tray **Quit** command stops the engine. Closing the main window only hides it.
- Discovery uses the pywizlight local-network UDP discovery mechanism and follows up with the library's bulb-type query for model/capability information. It requires the computer and devices to be on the same reachable LAN; guest Wi-Fi isolation, VLANs, firewalls or blocked UDP broadcast can prevent discovery. If discovery cannot see a device, enter its IP manually.
- Form-factor identification is best-effort. `pywizlight` exposes a module/model name and capability flags, but not every module name explicitly encodes whether the physical product is a bulb or strip. Recognised model-name markers get an automatic suggestion; ambiguous models remain unclassified and require the user to choose a role. Always review suggestions before adding devices.

## Troubleshooting

- If the tray icon is missing, check that your desktop shell supports tray / StatusNotifier icons.
- If audio capture fails, check the default output device and loopback permissions / PulseAudio compatibility.
- If a monitor is not detected, verify the display server and screen-capture permissions.
- If the engine exits, open the app window and inspect the log panel.

## Tuning preserved

This package retains the V1.1 tuning:
- Strip minimum brightness: 45%.
- Strip colour travel: 1.15×.
- Strip motion depth: 1.20×.
- Strip bass and transient motion multipliers: 1.25×.
- Strip transient brightness boost: +4%.
- Existing independent device sender loops, retry behavior and strip-first scheduling remain in the engine.


## Beta 1.1.2

This beta adds a hybrid dark desktop interface with Overview, Live Visualizer, Music Tuning, Devices, Settings and Logs pages. The visualizer receives compact telemetry from the existing engine process; it does not create a second audio capture. Live tuning multipliers default to 1.0, preserving the existing Stage 4.0 response until changed.


## Beta 1.1.3 — Liquid Glass UI refinement

- Refined light and dark themes with translucent layered surfaces, fine borders, softer corners, quieter selected states, and consistent control styling.
- Added clearer hover, pressed, focus, table, scrollbar, slider, and tooltip treatments.
- Kept rendering lightweight: no blur effects, animation frameworks, browser UI, or new runtime dependencies.
- Theme selection remains presentation-only; the audio engine and device sender loops are unchanged.


## v1.1.4-beta.1 UI correction notes

- Light/Dark/System now applies through both Qt stylesheets and the Qt application palette.
- Live spectrum charts use palette colours instead of a hard-coded dark canvas.
- Device status has its own UI-owned status summary; music telemetry no longer overwrites it.
- Telemetry display exceptions are caught and logged so presentation errors cannot terminate the Qt event loop.
- Page content scrolls when the window is too small for a page's controls.
- Spectrum repainting is driven by incoming telemetry; the redundant 20 FPS repaint timer was removed.
- The Stage 4.0 engine source is unchanged.

## WiZ feature parity roadmap

The next device-management increments can build on pywizlight's local UDP controls: power, dimming, RGB, colour temperature and supported built-in scenes/effects. Exact options should be capability-checked per device model. Cloud-account features such as schedules, remote access and account-managed rooms need a separate integration assessment and are not assumed to be available through local UDP control.
