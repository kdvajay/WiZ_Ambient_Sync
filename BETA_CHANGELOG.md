# WiZ Ambient Sync 1.2.2-beta.1

- Fixed the Light-theme device discovery dialog, including its table, viewport and role selectors.
- Added a simple theme-aware chevron to dropdowns.
- Replaced Overview brightness display bars with interactive sliders; sliders are enabled only when sync is stopped and the bulb is online/on.
- Brightness commands are sent on slider release and then verified using the device's direct `getPilot` response.
- Dashboard connected count and average brightness now derive from live device responses, not potentially stale engine telemetry.
- Reworked the hero into a compact inline title/description/action row.
- Increased Live Music Activity height, removed excess top spacing in Your lights, and restored full device-card borders.
- Removed beta/stage labels from the sidebar and footer; refined sidebar title and navigation.
- Stage 4.0 audio engine is unchanged.

Validation: Python syntax compilation and archive integrity checks. No Windows GUI or physical-bulb test was possible in this environment.
