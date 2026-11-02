# WiZ Ambient Sync

WiZ Ambient Sync turns music, motion, and the colours already present on your screen into a living light composition for WiZ bulbs and light strips.

It is a local-first desktop application with a Qt interface and a dedicated asynchronous engine. The engine listens to the computer's audio output, analyses the screen, interprets musical energy and impact, and sends coordinated colour, brightness, and motion updates to selected WiZ devices over the local network.

The result is designed to feel less like a sequence of light commands and more like a continuous visual performance: quiet passages breathe, bass gives the room weight, transients create momentum, and screen colour supplies the palette.

> Current release: **1.2.2-beta.1**
> This repository contains the source and build configuration. It does not contain precompiled installers.

 ## Download

[![Download Latest Release](https://img.shields.io/github/v/release/kdvajay/WiZ_Ambient_Sync)](https://github.com/kdvajay/WiZ_Ambient_Sync/releases/latest)

Download the latest compiled Windows application:
[WiZ Ambient Sync](https://github.com/kdvajay/WiZ_Ambient_Sync/releases/latest)

## What the application does

### A complete desktop control surface

The application runs from the system tray and provides a focused workspace for controlling and observing the experience:

- **Overview** — a compact dashboard showing connection state, active devices, average brightness, current audio source, and the current musical flow.
- **Live Visualizer** — a lightweight spectrum display for bass, mids, highs, energy, phrase level, brightness, and detected impact.
- **Music Tuning** — live controls for sensitivity, colour movement, transition speed, beat/transient response, and related response multipliers, with presets for quickly shaping the character of the sync.
- **Devices** — device tiles with live online/power state, reported brightness, power controls, brightness sliders, device enable/disable controls, and LAN discovery.
- **Settings** — device addresses, capture display, theme, launch-at-login behaviour, and tray behaviour.
- **Logs** — engine output and diagnostics in the same window, so failures and unavailable devices remain visible.

The interface supports **System**, **Light**, and **Dark** themes. It can remain available in the tray while the main window is closed; stopping or quitting from the tray controls the engine explicitly.

### Screen-aware colour

The engine samples the selected display and extracts a weighted colour palette. It considers saturation, luminance, scene changes, and screen edges, then smooths the result so the lights follow the visual atmosphere without flickering from frame to frame.

The resulting palette is not merely copied to every device. A primary colour anchors the composition, while secondary colours are chosen for contrast and movement. When a strip is present, its flow can travel through the available screen-derived colours rather than jumping between isolated presets.

### Music-aware motion and brightness

Audio is captured from the system output and separated into bass, mid, and high-frequency bands. The engine combines those bands with envelope tracking, adaptive normalisation, phrase-level smoothing, onset detection, and impact classification.

Those measurements drive distinct visual qualities:

- **Bass** contributes weight, depth, and broad brightness movement.
- **Mids** influence colour travel and the sense of motion through the palette.
- **Highs** add agility and fine movement.
- **Energy** shapes overall intensity and brightness.
- **Onsets and impacts** inject short-lived pulses and flow momentum.
- **Phrase movement** controls the longer arc of depth and speed, allowing the response to evolve with the music rather than reset on every beat.

Brightness is bounded, gamma-shaped, and smoothed with separate attack and release behaviour. Screen luminance also influences the usable brightness range, helping the lighting remain legible across dark, normal, and bright visual scenes.

### Coordinated multi-device output

The engine supports a primary bulb, a secondary bulb, and a strip, while safely ignoring blank device addresses. Device-specific compensation keeps different form factors visually related without forcing them to behave identically:

- A secondary bulb can receive a hue offset, brightness scale, and minimum brightness.
- A strip can use its own brightness scale, minimum brightness, colour smoothing, flow speed, motion depth, bass response, transient response, and transient brightness boost.
- Active-device compensation helps maintain a coherent overall presence when one, two, or three devices are participating.
- Each device has an independent sender loop, so one unavailable device does not stall the others.
- Failed devices are retried after a configurable interval and repeated errors are rate-limited in the log.
- A device can be disabled from the UI without removing its saved configuration.

The current tuned defaults preserve the project's established strip response: a 45% strip minimum, increased flow speed and motion depth, stronger bass/transient motion, and a modest transient brightness lift.

### Device discovery and direct controls

The Devices page can discover WiZ devices on the local network, query their model and capabilities, and suggest whether each device is a bulb or strip. Classification is deliberately best-effort: suggestions should be reviewed before saving.

When sync is stopped, the application can query configured devices directly and send:

- power on/off commands;
- brightness commands from the device tiles;
- live verification through the device's reported pilot state;
- per-device enable/disable state for the sync engine.

The UI never performs device polling inside the audio engine. Direct device status work runs in background workers, keeping the audio and rendering paths responsive.

## Architecture

WiZ Ambient Sync is intentionally split into two processes:

```text
Desktop UI (PySide6)
        │
        ├── starts/stops the engine
        ├── sends tuning and device-control state
        ├── reads telemetry and logs
        └── renders dashboard and visualizer
                │
                ▼
Async engine (asyncio)
        │
        ├── captures system audio with SoundCard
        ├── samples the selected display with mss
        ├── analyses FFT bands, energy, onsets, impacts, and phrases
        ├── derives a smoothed screen palette
        └── sends local UDP commands through pywizlight
```

The visualizer displays telemetry emitted by the existing engine; it does not capture audio a second time. This keeps the analysis authoritative and prevents the UI from competing with the engine for the audio stream.

The source entry points are:

- `app/main.py` — PySide6 desktop interface, tray integration, discovery, direct device controls, configuration, themes, telemetry, and logs.
- `engine/WiZ_Ambient_Sync.py` — audio/screen analysis, colour-flow logic, brightness response, multi-device coordination, retry handling, and telemetry.
- `app/config.json` — the shipped baseline configuration copied to the user's writable configuration directory on first launch.
- `build.py` — cross-platform PyInstaller build for the GUI and engine executables.
- `installer.iss` — Windows installer definition.

## Requirements

- Python **3.11 or 3.12**, 64-bit recommended on Windows.
- A computer with an audio output that can be captured through SoundCard.
- A display accessible to `mss`.
- WiZ devices reachable from the same local network as the computer.
- On Linux, a desktop environment with tray support and a working PulseAudio-compatible audio path. PipeWire users should have `pipewire-pulse` active.

Runtime dependencies are declared in `requirements.txt`:

- PySide6
- platformdirs
- PyInstaller
- pywizlight
- mss
- numpy
- soundcard

## Build from source

Build on the operating system where the resulting executable will be used. The build script produces two executables that must be distributed together: `WiZAmbientSync` and `wiz-engine`.

### Windows 10/11

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python build.py
```

The versioned output is written to `dist\1.2.2-beta.1\`.

### Ubuntu, Debian, or Fedora

On Ubuntu/Debian, install the native audio and build prerequisites first:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-dev build-essential libpulse0 libpulse-dev pulseaudio-utils
```

On Fedora, install the equivalent Python development, compiler, and PulseAudio client/development packages. Then build:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python build.py
```

### Arch Linux

```bash
sudo pacman -S --needed python python-pip base-devel libpulse pipewire-pulse
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python build.py
```

### Windows installer

After building the executables, open `installer.iss` with Inno Setup 6 or run it through the build workflow. The installer places the GUI and engine together and creates optional Start Menu and desktop shortcuts.

## First launch

1. Start `WiZAmbientSync`.
2. Open **Settings** and choose the display to capture.
3. Enter the local IP addresses of the WiZ devices you want to use, or open **Devices** and choose **Discover devices**.
4. Review discovery suggestions and assign each device its intended role.
5. Save the settings.
6. Choose **Start syncing** from the application or tray menu.

The application keeps running in the tray when the window is closed if that option is enabled. Use **Stop** to end the active sync session and **Quit** to close the application and stop the engine.

## Configuration and tuning

The shipped `app/config.json` contains the complete tuning surface. It is copied into the user's platform-specific configuration directory on first launch, where it can be edited without modifying the installation.

Typical locations are:

- **Windows:** `%LOCALAPPDATA%\WiZAmbientSyncBeta\config.json`
- **Linux:** `~/.config/WiZAmbientSyncBeta/config.json`

The configuration is organised into these areas:

- `device` — configured IP addresses and capture display.
- `dual_bulb` — secondary-bulb hue and brightness compensation.
- `multi_device` — strip behaviour, active-device scaling, send intervals, retry timing, smoothing, and per-device response multipliers.
- `audio` — sample rate, FFT block size, frequency bands, normalisation, attack/release, onset detection, and impact thresholds.
- `brightness` — gamma, bounds, screen influence, luminance smoothing, rhythm accents, impact boosts, and pulse decay.
- `colour` — palette sampling, saturation, edge weighting, scene-change detection, palette size, and smoothing.
- `flow` — speed, depth, impulses, momentum decay, secondary contrast, and quiet-scene motion.
- `phrase` — longer-form musical depth and speed response.
- `sync` — update interval and status output.

For most users, the in-app **Music Tuning** controls are the safer starting point. Advanced edits to the JSON can produce beautiful results, but should be made incrementally and with the engine stopped.

## GitHub Actions

The workflow in `.github/workflows/windows-build.yml` runs for version tags matching `v*` or manually through `workflow_dispatch`. It:

1. checks out the repository;
2. installs Python 3.12 and project dependencies;
3. builds the GUI and engine with PyInstaller;
4. installs Inno Setup;
5. creates the Windows x64 installer; and
6. uploads the portable executables and installer as a workflow artifact.

## Privacy and network behaviour

The application is designed around local operation:

- Audio is analysed on the local computer.
- Screen samples are analysed on the local computer.
- WiZ commands are sent to the device IPs on the local network through `pywizlight`.
- No cloud account, remote-access service, schedule service, or analytics pipeline is assumed by this project.

The computer and the WiZ devices must be able to reach one another. Guest Wi-Fi isolation, VLAN boundaries, firewall rules, or blocked UDP broadcast can prevent discovery or control. If discovery fails, entering a known device IP manually is the supported fallback.

## Current limitations

- This is a beta source/build package, not a precompiled release bundle.
- Builds should be made separately on Windows and Linux; a Windows executable is not produced from a Linux build and vice versa.
- No physical WiZ hardware test is performed by the packaging process itself.
- Linux screen capture currently targets X11 most reliably. Wayland compositors may restrict direct capture; a PipeWire/portal capture backend remains a future improvement.
- Audio capture depends on the operating system's loopback and audio-server configuration.
- Device form-factor classification during discovery is best-effort and should be confirmed by the user.
- Advanced tuning remains available through the JSON configuration rather than being exposed entirely in the UI.

## Troubleshooting

### No tray icon

Confirm that the desktop environment supports StatusNotifier or AppIndicator tray icons. Some minimal Wayland sessions do not display legacy tray icons by default.

### No audio response

Check that the intended output is the system default and that loopback capture is available. On Linux, verify that PulseAudio compatibility is working and that `pipewire-pulse` is running when using PipeWire.

### A monitor is missing

Confirm that the display is visible to the operating system and that the selected capture backend has permission to read it. X11 is currently the most dependable Linux path.

### Discovery finds nothing

Make sure the computer and WiZ devices are on the same reachable LAN. Check guest-network isolation, VLAN routing, firewall rules, and UDP broadcast handling. Enter the device IP manually when the network intentionally blocks discovery.

### A device drops out during sync

Inspect the **Logs** page. Sender loops are independent, and unavailable devices are retried automatically after the configured retry interval. Confirm the device has a stable/reserved IP and remains reachable from the host.

### The engine exits

Open the application and inspect the engine output in **Logs**. The most common causes are missing audio access, an unavailable capture display, invalid configuration values, or no configured WiZ device.

## Project status

WiZ Ambient Sync is an evolving beta project. The current release focuses on a carefully tuned ambient engine, dependable local device control, and a clear desktop workflow. Future work can extend device-management parity, strengthen native Wayland capture, and continue refining the relationship between musical phrasing, screen colour, and physical light.

## License

## 📄 License

This project is licensed under the **MIT License**.

You are free to use, modify, and distribute this software, including for commercial purposes, provided that the original copyright and license notice are included.

See the [LICENSE](LICENSE) file for the full license text.
