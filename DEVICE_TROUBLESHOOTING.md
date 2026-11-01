# WiZ device troubleshooting

The Devices page controls the IP addresses stored in the per-user configuration:
`%APPDATA%\\WiZAmbientSyncBeta\\config.json`.

If a device shows **Offline · check IP / network**:

1. Confirm the PC and WiZ devices are on the same LAN / VLAN.
2. In the Devices page, choose **Discover devices** and run a network scan.
3. Compare the discovered IP addresses with the three configured addresses.
4. Assign each discovered device to Primary bulb, Secondary bulb, or WiZ strip, then add it.
5. Use **Refresh status**. A successful status response should show Online and a device-reported brightness.
6. If discovery returns no devices, check the router's client list and ensure UDP traffic on WiZ control port 38899 is not blocked by the PC firewall or guest-network isolation.

The app does not assume that an unreachable device is switched off. Its toggle remains available to retry a command, while the last confirmed power state is retained when known.

## Startup behaviour

A normal double-click always opens the main window in the foreground, even when Launch at sign-in is enabled. The Windows startup entry uses the explicit `--background` argument to start in the tray.

## Tray menu

The tray context menu uses explicit light/dark foreground and background colours to avoid Windows theme contrast issues.
