import asyncio
import colorsys
import warnings
from collections import deque
import json
import os
from pathlib import Path

import mss
import numpy as np
import soundcard as sc
from pywizlight import wizlight, PilotBuilder

DEVICE_CONTROL_FILE = os.environ.get("WIZ_AMBIENT_DEVICE_CONTROL", "")
# The sender tasks must never perform file I/O to check optional UI controls.
# The main loop refreshes this tiny cache infrequently; senders do a dict lookup.
DEVICE_CONTROL_STATE = {}

def refresh_device_control_state():
    if not DEVICE_CONTROL_FILE:
        DEVICE_CONTROL_STATE.clear()
        return
    try:
        with open(DEVICE_CONTROL_FILE, "r", encoding="utf-8") as handle:
            controls = json.load(handle)
        if isinstance(controls, dict):
            DEVICE_CONTROL_STATE.clear()
            DEVICE_CONTROL_STATE.update({
                str(ip): bool(value.get("enabled", True))
                for ip, value in controls.items()
                if isinstance(value, dict)
            })
    except (OSError, ValueError, TypeError):
        # A transient optional-control-file problem must never disable sync.
        return

def device_is_enabled(ip):
    return DEVICE_CONTROL_STATE.get(str(ip), True)


# ============================================================
# WiZ AMBIENT SYNC V2 — STAGE 4.0 MUSICAL FLOW
#
# Screen palette -> colour vocabulary
# Energy         -> macro brightness
# Bass           -> weight / pulse
# Mids           -> colour travel
# Highs          -> agility
# Transients     -> flow momentum
#
# Unlike Stage 3.3:
#
# There is no HIT -> HOLD -> RECOVER colour state machine.
#
# The light continuously travels through colours already
# present on screen. Music controls how far and how quickly
# that movement occurs.
# ============================================================


# ============================================================
# CONFIG
# ============================================================

CONFIG_FILE = Path(os.environ.get("WIZ_AMBIENT_CONFIG", Path(__file__).resolve().parent / "config.json")).expanduser()

if not CONFIG_FILE.exists():
    raise FileNotFoundError(
        f"Configuration file not found: {CONFIG_FILE}"
    )

with CONFIG_FILE.open("r", encoding="utf-8") as file:
    config = json.load(file)

# Optional desktop-app telemetry and live tuning channel.  Standalone console
# operation remains unchanged unless the desktop shell enables these flags.
TELEMETRY_ENABLED = os.environ.get("WIZ_AMBIENT_TELEMETRY", "0") == "1"
TUNING_FILE = Path(os.environ.get("WIZ_AMBIENT_TUNING", CONFIG_FILE.with_name("tuning.json"))).expanduser()
TUNING_DEFAULTS = {
    "bass_sensitivity": 1.0,
    "mid_sensitivity": 1.0,
    "high_sensitivity": 1.0,
    "motion_intensity": 1.0,
    "transition_speed": 1.0,
    "transient_strength": 1.0,
}

def read_live_tuning():
    values = TUNING_DEFAULTS.copy()
    try:
        if TUNING_FILE.exists():
            with TUNING_FILE.open("r", encoding="utf-8") as handle:
                supplied = json.load(handle)
            for key in values:
                if key in supplied:
                    values[key] = float(np.clip(float(supplied[key]), 0.5, 1.5))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        # Ignore a transient write or malformed optional tuning file.
        pass
    return values


# ============================================================
# DEVICE
# ============================================================

BULB_IP = config["device"].get("bulb_ip")
SECONDARY_BULB_IP = config["device"].get("secondary_bulb_ip")
STRIP_IP = config["device"].get("strip_ip")

DUAL_BULB_CONFIG = config.get("dual_bulb", {})
SECONDARY_HUE_OFFSET = float(DUAL_BULB_CONFIG.get("hue_offset", 0.10))
SECONDARY_BRIGHTNESS_SCALE = float(
    DUAL_BULB_CONFIG.get("brightness_scale", 0.88)
)
SECONDARY_MIN_BRIGHTNESS = int(
    DUAL_BULB_CONFIG.get("min_brightness", 50)
)

MULTI_DEVICE_CONFIG = config.get("multi_device", {})
DEVICE_COUNT_SCALES = {
    int(count): float(scale)
    for count, scale in MULTI_DEVICE_CONFIG.get(
        "brightness_by_active_count",
        {"1": 1.0, "2": 1.12, "3": 1.22}
    ).items()
}
STRIP_BRIGHTNESS_SCALE = float(
    MULTI_DEVICE_CONFIG.get("strip_brightness_scale", 0.95)
)
STRIP_MIN_BRIGHTNESS = int(
    MULTI_DEVICE_CONFIG.get("strip_min_brightness", 50)
)
DEVICE_RETRY_INTERVAL = float(
    MULTI_DEVICE_CONFIG.get("retry_interval", 5.0)
)
DEVICE_ERROR_LOG_INTERVAL = float(
    MULTI_DEVICE_CONFIG.get("error_log_interval", 5.0)
)
STRIP_COLOUR_SMOOTHING = float(
    MULTI_DEVICE_CONFIG.get("strip_colour_smoothing", 0.88)
)
STRIP_MIN_SEND_INTERVAL = float(
    MULTI_DEVICE_CONFIG.get("strip_min_send_interval", 0.10)
)
BULB_MIN_SEND_INTERVAL = float(
    MULTI_DEVICE_CONFIG.get("bulb_min_send_interval", 0.10)
)
STRIP_FLOW_SPEED_MULTIPLIER = float(
    MULTI_DEVICE_CONFIG.get("strip_flow_speed_multiplier", 1.15)
)
STRIP_MOTION_DEPTH_MULTIPLIER = float(
    MULTI_DEVICE_CONFIG.get("strip_motion_depth_multiplier", 1.20)
)
STRIP_BASS_MOTION_MULTIPLIER = float(
    MULTI_DEVICE_CONFIG.get("strip_bass_motion_multiplier", 1.25)
)
STRIP_TRANSIENT_MOTION_MULTIPLIER = float(
    MULTI_DEVICE_CONFIG.get("strip_transient_motion_multiplier", 1.25)
)
STRIP_TRANSIENT_BRIGHTNESS_BOOST = float(
    MULTI_DEVICE_CONFIG.get("strip_transient_brightness_boost", 4.0)
)

MONITOR_NUMBER = config["device"]["monitor"]


# ============================================================
# AUDIO
# ============================================================

SAMPLE_RATE = config["audio"]["sample_rate"]
BLOCK_SIZE = config["audio"]["block_size"]

ENVELOPE_STRENGTH = config["audio"]["envelope_strength"]
BASS_STRENGTH = config["audio"]["bass_strength"]

ENVELOPE_ATTACK = config["audio"]["envelope_attack"]
ENVELOPE_RELEASE = config["audio"]["envelope_release"]

HISTORY_SIZE = config["audio"]["history_size"]
FLOOR_PERCENTILE = config["audio"]["floor_percentile"]
CEILING_PERCENTILE = config["audio"]["ceiling_percentile"]
MIN_DYNAMIC_RANGE = config["audio"]["min_dynamic_range"]

BASS_LOW = config["audio"]["bass_low"]
BASS_HIGH = config["audio"]["bass_high"]

MID_LOW = config["audio"]["mid_low"]
MID_HIGH = config["audio"]["mid_high"]

HIGH_LOW = config["audio"]["high_low"]
HIGH_HIGH = config["audio"]["high_high"]

BASS_ATTACK = config["audio"]["bass_attack"]
BASS_RELEASE = config["audio"]["bass_release"]

MID_ATTACK = config["audio"]["mid_attack"]
MID_RELEASE = config["audio"]["mid_release"]

HIGH_ATTACK = config["audio"]["high_attack"]
HIGH_RELEASE = config["audio"]["high_release"]

BASS_HISTORY_SIZE = config["audio"]["bass_history_size"]
BAND_HISTORY_SIZE = config["audio"]["band_history_size"]

ENERGY_ATTACK = config["audio"]["energy_attack"]
ENERGY_RELEASE = config["audio"]["energy_release"]

ONSET_HISTORY_SIZE = config["audio"]["onset_history_size"]
ONSET_SHORT_WINDOW = config["audio"]["onset_short_window"]
ONSET_LONG_WINDOW = config["audio"]["onset_long_window"]

ONSET_THRESHOLD = config["audio"]["onset_threshold"]

ONSET_BASS_WEIGHT = config["audio"]["onset_bass_weight"]
ONSET_RHYTHM_WEIGHT = config["audio"]["onset_rhythm_weight"]

IMPACT_NORMAL = config["audio"]["impact_normal"]
IMPACT_STRONG = config["audio"]["impact_strong"]
IMPACT_MAJOR = config["audio"]["impact_major"]

IMPACT_BASS_WEIGHT = config["audio"]["impact_bass_weight"]
IMPACT_TRANSIENT_WEIGHT = config["audio"]["impact_transient_weight"]
IMPACT_ENERGY_WEIGHT = config["audio"]["impact_energy_weight"]


# ============================================================
# BRIGHTNESS
# ============================================================

BRIGHTNESS_GAMMA = config["brightness"]["gamma"]

BRIGHTNESS_ATTACK = config["brightness"]["attack"]
BRIGHTNESS_RELEASE = config["brightness"]["release"]

ABSOLUTE_MIN_BRIGHTNESS = config["brightness"]["absolute_min"]
ABSOLUTE_MAX_BRIGHTNESS = config["brightness"]["absolute_max"]

DARK_MIN = config["brightness"]["dark_min"]
DARK_MAX = config["brightness"]["dark_max"]

NORMAL_MIN = config["brightness"]["normal_min"]
NORMAL_MAX = config["brightness"]["normal_max"]

BRIGHT_MIN = config["brightness"]["bright_min"]
BRIGHT_MAX = config["brightness"]["bright_max"]

SCREEN_BRIGHTNESS_INFLUENCE = config["brightness"]["screen_influence"]

LUMINANCE_SMOOTHING = config["brightness"]["luminance_smoothing"]
LUMINANCE_GAMMA = config["brightness"]["luminance_gamma"]

RHYTHM_ACCENT = config["brightness"]["rhythm_accent"]
BRIGHTNESS_PULSE_DECAY = config["brightness"]["pulse_decay"]

NORMAL_IMPACT_BOOST = config["brightness"]["normal_impact_boost"]
STRONG_IMPACT_BOOST = config["brightness"]["strong_impact_boost"]
MAJOR_IMPACT_BOOST = config["brightness"]["major_impact_boost"]


# ============================================================
# COLOUR
# ============================================================

SCREEN_SAMPLE_STEP = config["colour"]["screen_sample_step"]

MIN_VALUE = config["colour"]["min_value"]
MIN_SATURATION = config["colour"]["min_saturation"]

HUE_BINS = config["colour"]["hue_bins"]

SATURATION_BOOST = config["colour"]["saturation_boost"]
MIN_OUTPUT_SATURATION = config["colour"]["min_output_saturation"]

EDGE_WEIGHT = config["colour"]["edge_weight"]
EDGE_SIZE = config["colour"]["edge_size"]

COLOR_SMOOTHING = config["colour"]["smoothing"]

SCENE_CHANGE_SMOOTHING = config["colour"]["scene_change_smoothing"]
SCENE_CHANGE_THRESHOLD = config["colour"]["scene_change_threshold"]

PALETTE_SIZE = config["colour"]["palette_size"]
PALETTE_MIN_WEIGHT = config["colour"]["palette_min_weight"]
PALETTE_MIN_HUE_DISTANCE = config["colour"]["palette_min_hue_distance"]


# ============================================================
# FLOW
# ============================================================

FLOW_BASE_SPEED = config["flow"]["base_speed"]

FLOW_ENERGY_SPEED = config["flow"]["energy_speed"]
FLOW_MID_SPEED = config["flow"]["mid_speed"]
FLOW_HIGH_SPEED = config["flow"]["high_speed"]

FLOW_BASS_DEPTH = config["flow"]["bass_depth"]
FLOW_MID_DEPTH = config["flow"]["mid_depth"]
FLOW_HIGH_DEPTH = config["flow"]["high_depth"]

FLOW_NORMAL_IMPULSE = config["flow"]["normal_impulse"]
FLOW_STRONG_IMPULSE = config["flow"]["strong_impulse"]
FLOW_MAJOR_IMPULSE = config["flow"]["major_impulse"]

FLOW_MOMENTUM_DECAY = config["flow"]["momentum_decay"]

FLOW_MIN_SECONDARY_CONTRAST = config["flow"]["min_secondary_contrast"]

FLOW_ANCHOR_WEIGHT = config["flow"]["anchor_weight"]

FLOW_QUIET_MOTION = config["flow"]["quiet_motion"]
FLOW_MOTION_GAMMA = config["flow"]["motion_gamma"]

# ============================================================
# MUSICAL PHRASE
# ============================================================

PHRASE_ATTACK = config["phrase"]["attack"]
PHRASE_RELEASE = config["phrase"]["release"]

PHRASE_ENERGY_WEIGHT = config["phrase"]["energy_weight"]
PHRASE_MID_WEIGHT = config["phrase"]["mid_weight"]
PHRASE_HIGH_WEIGHT = config["phrase"]["high_weight"]

PHRASE_GAMMA = config["phrase"]["gamma"]

PHRASE_DEPTH_MIN = config["phrase"]["depth_min"]
PHRASE_DEPTH_MAX = config["phrase"]["depth_max"]

PHRASE_SPEED_MIN = config["phrase"]["speed_min"]
PHRASE_SPEED_MAX = config["phrase"]["speed_max"]
PHRASE_INSTANT_DEPTH_WEIGHT = (
    config["phrase"]["instant_depth_weight"]
)

PHRASE_DEPTH_WEIGHT = (
    config["phrase"]["phrase_depth_weight"]
)

PHRASE_INSTANT_SPEED_WEIGHT = (
    config["phrase"]["instant_speed_weight"]
)

# ============================================================
# SYNC
# ============================================================

UPDATE_INTERVAL = config["sync"]["update_interval"]
SHOW_STATUS = config["sync"]["show_status"]


warnings.filterwarnings(
    "ignore",
    message="data discontinuity in recording"
)


# ============================================================
# HELPERS
# ============================================================

def band_energy(spectrum, freqs, low, high):

    mask = (
        (freqs >= low)
        & (freqs < high)
    )

    if not np.any(mask):
        return 0.0

    return float(
        np.sqrt(
            np.mean(
                spectrum[mask] ** 2
            )
        )
    )


def circular_hue_distance(a, b):

    difference = abs(a - b)

    return min(
        difference,
        1.0 - difference
    )


def hue_to_rgb(hue, saturation):

    rr, gg, bb = colorsys.hsv_to_rgb(
        float(hue % 1.0),
        float(saturation),
        1.0
    )

    return np.array(
        [rr * 255, gg * 255, bb * 255],
        dtype=np.float64
    )


def remap(value, low, high):

    if high <= low:
        return 1.0

    return float(
        np.clip(
            (value - low) / (high - low),
            0.0,
            1.0
        )
    )


def smooth_value(current, target, attack, release):

    coefficient = (
        attack
        if target > current
        else release
    )

    return current + coefficient * (
        target - current
    )


def normalise_history(
    value,
    history,
    floor_percentile=15,
    ceiling_percentile=90,
    minimum_range=0.0001
):

    history.append(value)

    if len(history) < 20:
        return 0.0

    values = np.array(
        history,
        dtype=np.float64
    )

    floor = float(
        np.percentile(
            values,
            floor_percentile
        )
    )

    ceiling = float(
        np.percentile(
            values,
            ceiling_percentile
        )
    )

    dynamic_range = max(
        ceiling - floor,
        minimum_range
    )

    return float(
        np.clip(
            (value - floor) / dynamic_range,
            0.0,
            1.0
        )
    )


# ============================================================
# COLOUR CONTRAST
# ============================================================

def colour_contrast(anchor, candidate):

    hue_distance = circular_hue_distance(
        anchor["hue"],
        candidate["hue"]
    )

    hue_score = min(
        hue_distance * 2.0,
        1.0
    )

    rgb_a = anchor["rgb"] / 255.0
    rgb_b = candidate["rgb"] / 255.0

    rgb_distance = float(
        np.linalg.norm(
            rgb_a - rgb_b
        )
        / np.sqrt(3.0)
    )

    return float(
        0.70 * hue_score
        + 0.30 * rgb_distance
    )


# ============================================================
# SCREEN ANALYSIS
# ============================================================

def analyse_screen(screenshot):

    image = np.asarray(
        screenshot
    )[:, :, :3]

    # MSS BGRA/BGR -> RGB
    image = image[:, :, ::-1]

    image = image[
        ::SCREEN_SAMPLE_STEP,
        ::SCREEN_SAMPLE_STEP
    ]

    rgb = (
        image.astype(np.float32)
        / 255.0
    )

    height, width, _ = rgb.shape

    r = rgb[:, :, 0]
    g = rgb[:, :, 1]
    b = rgb[:, :, 2]


    # ========================================================
    # LUMINANCE
    # ========================================================

    luminance_pixels = (
        0.2126 * r
        + 0.7152 * g
        + 0.0722 * b
    )

    screen_luminance = float(
        np.percentile(
            luminance_pixels,
            65
        )
    )

    screen_luminance = float(
        np.clip(
            screen_luminance,
            0,
            1
        )
    )


    # ========================================================
    # RGB -> HSV
    # ========================================================

    maximum = np.max(
        rgb,
        axis=2
    )

    minimum = np.min(
        rgb,
        axis=2
    )

    delta = maximum - minimum

    saturation = np.zeros_like(
        maximum
    )

    nonzero = maximum > 0

    saturation[nonzero] = (
        delta[nonzero]
        / maximum[nonzero]
    )

    value = maximum

    hue = np.zeros_like(
        maximum
    )

    valid_delta = delta > 0.00001

    red_max = (
        valid_delta
        & (maximum == r)
    )

    green_max = (
        valid_delta
        & (maximum == g)
    )

    blue_max = (
        valid_delta
        & (maximum == b)
    )

    hue[red_max] = (
        (
            (g[red_max] - b[red_max])
            / delta[red_max]
        ) % 6
    )

    hue[green_max] = (
        (
            (b[green_max] - r[green_max])
            / delta[green_max]
        ) + 2
    )

    hue[blue_max] = (
        (
            (r[blue_max] - g[blue_max])
            / delta[blue_max]
        ) + 4
    )

    hue /= 6.0


    # ========================================================
    # VALID COLOUR PIXELS
    # ========================================================

    valid = (
        (value >= MIN_VALUE)
        & (saturation >= MIN_SATURATION)
    )

    if np.count_nonzero(valid) < 20:
        return None, screen_luminance


    # ========================================================
    # EDGE WEIGHTING
    # ========================================================

    yy, xx = np.mgrid[
        0:height,
        0:width
    ]

    xn = xx / max(width - 1, 1)
    yn = yy / max(height - 1, 1)

    edge = (
        (xn < EDGE_SIZE)
        | (xn > 1 - EDGE_SIZE)
        | (yn < EDGE_SIZE)
        | (yn > 1 - EDGE_SIZE)
    )


    # ========================================================
    # PIXEL WEIGHTS
    # ========================================================

    weights = saturation ** 2.2

    weights *= (
        0.35 + value
    )

    weights[edge] *= EDGE_WEIGHT

    weights *= valid


    # ========================================================
    # HUE HISTOGRAM
    # ========================================================

    hue_bins = np.floor(
        hue * HUE_BINS
    ).astype(int)

    hue_bins = np.clip(
        hue_bins,
        0,
        HUE_BINS - 1
    )

    histogram = np.zeros(
        HUE_BINS,
        dtype=np.float64
    )

    for i in range(HUE_BINS):

        histogram[i] = np.sum(
            weights[hue_bins == i]
        )


    histogram = (
        0.25 * np.roll(histogram, 1)
        + 0.50 * histogram
        + 0.25 * np.roll(histogram, -1)
    )

    maximum_histogram = float(
        np.max(histogram)
    )

    if maximum_histogram <= 0:
        return None, screen_luminance


    # ========================================================
    # PALETTE REGIONS
    # ========================================================

    candidate_bins = list(
        np.argsort(histogram)[::-1]
    )

    selected_bins = []

    for candidate in candidate_bins:

        relative_weight = (
            histogram[candidate]
            / maximum_histogram
        )

        if relative_weight < PALETTE_MIN_WEIGHT:
            break

        candidate_hue = (
            (candidate + 0.5)
            / HUE_BINS
        )

        sufficiently_different = True

        for selected in selected_bins:

            selected_hue = (
                (selected + 0.5)
                / HUE_BINS
            )

            if (
                circular_hue_distance(
                    candidate_hue,
                    selected_hue
                )
                < PALETTE_MIN_HUE_DISTANCE
            ):
                sufficiently_different = False
                break

        if sufficiently_different:
            selected_bins.append(
                int(candidate)
            )

        if len(selected_bins) >= PALETTE_SIZE:
            break


    if not selected_bins:
        return None, screen_luminance


    # ========================================================
    # BUILD PALETTE
    # ========================================================

    palette = []

    for selected_bin in selected_bins:

        previous_bin = (
            selected_bin - 1
        ) % HUE_BINS

        next_bin = (
            selected_bin + 1
        ) % HUE_BINS

        mask = (
            valid
            & (
                (hue_bins == previous_bin)
                | (hue_bins == selected_bin)
                | (hue_bins == next_bin)
            )
        )

        local_weights = weights[mask]

        if (
            len(local_weights) == 0
            or np.sum(local_weights) <= 0
        ):
            continue

        local_hues = hue[mask]
        local_saturation = saturation[mask]

        angles = (
            local_hues
            * 2
            * np.pi
        )

        sin_average = np.sum(
            np.sin(angles)
            * local_weights
        )

        cos_average = np.sum(
            np.cos(angles)
            * local_weights
        )

        palette_hue = (
            np.arctan2(
                sin_average,
                cos_average
            )
            / (2 * np.pi)
        ) % 1.0

        weight_sum = np.sum(
            local_weights
        )

        palette_sat = (
            np.sum(
                local_saturation
                * local_weights
            )
            / weight_sum
        )

        palette_sat = float(
            np.clip(
                palette_sat
                * SATURATION_BOOST,
                MIN_OUTPUT_SATURATION,
                1.0
            )
        )

        palette_rgb = hue_to_rgb(
            palette_hue,
            palette_sat
        )

        palette.append(
            {
                "hue": float(palette_hue),
                "saturation": palette_sat,
                "rgb": palette_rgb,
                "weight": float(
                    histogram[selected_bin]
                    / maximum_histogram
                )
            }
        )


    if not palette:
        return None, screen_luminance

    return palette, screen_luminance


# ============================================================
# SCREEN -> BRIGHTNESS RANGE
# ============================================================

def get_screen_brightness_range(luminance):

    luminance = float(
        np.clip(
            luminance,
            0,
            1
        )
    )

    luminance = (
        luminance
        ** LUMINANCE_GAMMA
    )

    if luminance < 0.50:

        t = luminance / 0.50

        minimum = (
            DARK_MIN
            + t
            * (
                NORMAL_MIN
                - DARK_MIN
            )
        )

        maximum = (
            DARK_MAX
            + t
            * (
                NORMAL_MAX
                - DARK_MAX
            )
        )

    else:

        t = (
            (luminance - 0.50)
            / 0.50
        )

        minimum = (
            NORMAL_MIN
            + t
            * (
                BRIGHT_MIN
                - NORMAL_MIN
            )
        )

        maximum = (
            NORMAL_MAX
            + t
            * (
                BRIGHT_MAX
                - NORMAL_MAX
            )
        )

    return (
        float(minimum),
        float(maximum)
    )


# ============================================================
# FLOW PALETTE TARGET
# ============================================================

def get_flow_target(
    palette,
    phase,
    motion_depth
):

    if palette is None or len(palette) == 0:
        return None

    anchor = palette[0]
    anchor_rgb = anchor["rgb"]

    if len(palette) == 1:
        return anchor_rgb.copy()

    candidates = []

    for candidate in palette[1:]:

        contrast = colour_contrast(
            anchor,
            candidate
        )

        if contrast >= FLOW_MIN_SECONDARY_CONTRAST:
            candidates.append(
                candidate
            )

    if not candidates:
        return anchor_rgb.copy()


    # ========================================================
    # CONTINUOUS TRIANGULAR PALETTE TRAVEL
    #
    # phase 0.0 -> anchor
    #       0.5 -> secondary
    #       1.0 -> anchor
    #
    # Next cycle uses the next available screen colour.
    # ========================================================

    cycle = phase % 1.0

    palette_cycle = int(
        phase
    )

    candidate = candidates[
        palette_cycle
        % len(candidates)
    ]

    candidate_rgb = candidate["rgb"]

    if cycle < 0.5:
        travel = cycle * 2.0
    else:
        travel = (
            1.0 - cycle
        ) * 2.0

    # Smoothstep prevents mechanical linear movement.
    travel = (
        travel
        * travel
        * (
            3.0
            - 2.0 * travel
        )
    )

    travel *= motion_depth

    anchor_limit = (
        1.0
        - FLOW_ANCHOR_WEIGHT
    )

    # Strong musical motion is allowed to reach farther
    # into the secondary screen colour.
    dynamic_limit = (
        anchor_limit
        + 0.22
        * motion_depth
    )

    travel *= min(
        dynamic_limit,
        1.0
    )

    target = (
        anchor_rgb
        * (
            1.0 - travel
        )
        + candidate_rgb
        * travel
    )

    return np.clip(
        target,
        0,
        255
    )


# ============================================================
# COORDINATED DEVICE OUTPUT
# ============================================================

def get_secondary_rgb(primary_rgb):
    """Create a coordinated accent colour for the secondary bulb."""
    red, green, blue = (
        float(channel) / 255.0 for channel in primary_rgb
    )
    hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
    hue = (hue + SECONDARY_HUE_OFFSET) % 1.0
    rr, gg, bb = colorsys.hsv_to_rgb(hue, saturation, value)
    return tuple(
        int(round(channel * 255))
        for channel in (rr, gg, bb)
    )


def to_wiz_brightness(percent):
    """Convert percentage brightness to the 1..255 WiZ range."""
    return int(np.clip(round(float(percent) * 255.0 / 100.0), 1, 255))


def configured_devices():
    """Build only configured devices; blank IPs are safely ignored."""
    candidates = [
        ("Primary", BULB_IP, "primary"),
        ("Secondary", SECONDARY_BULB_IP, "secondary"),
        ("Strip", STRIP_IP, "strip"),
    ]

    devices = []
    seen_ips = set()

    for name, ip, role in candidates:
        if ip is None:
            continue

        ip = str(ip).strip()
        if not ip or ip.lower() in {
            "none", "null", "set_ip", "your_strip_ip", "set_strip_ip"
        }:
            continue

        if ip in seen_ips:
            print(f"Skipping {name}: duplicate IP address {ip}")
            continue

        seen_ips.add(ip)
        devices.append({
            "name": name,
            "ip": ip,
            "role": role,
            "light": wizlight(ip),
        })

    return devices


def device_output(device, primary_rgb, primary_brightness):
    """Return coordinated colour and brightness for one device."""
    role = device["role"]

    if role == "secondary":
        rgb = get_secondary_rgb(primary_rgb)
        brightness = max(
            SECONDARY_MIN_BRIGHTNESS,
            round(primary_brightness * SECONDARY_BRIGHTNESS_SCALE)
        )
    elif role == "strip":
        rgb = tuple(int(channel) for channel in primary_rgb)
        brightness = max(
            STRIP_MIN_BRIGHTNESS,
            round(primary_brightness * STRIP_BRIGHTNESS_SCALE)
        )
    else:
        rgb = tuple(int(channel) for channel in primary_rgb)
        brightness = primary_brightness

    brightness = int(np.clip(brightness, 1, 100))
    return rgb, to_wiz_brightness(brightness), brightness


async def device_sender(
    device,
    latest_commands,
    command_versions,
    command_events,
    device_states,
    device_last_error_log,
):
    """
    One independent sender per WiZ device.

    Only the newest pending command is retained. If a send is in progress,
    newer frames replace the pending value instead of building a stale queue.
    """
    name = device["name"]
    ip = device["ip"]
    role = device["role"]
    loop = asyncio.get_running_loop()
    min_interval = (
        STRIP_MIN_SEND_INTERVAL if role == "strip"
        else BULB_MIN_SEND_INTERVAL
    )
    last_send = -999.0

    while True:
        await command_events[name].wait()
        command_events[name].clear()

        command = latest_commands[name]
        version = command_versions[name]
        if command is None:
            continue

        # Respect a modest per-device send interval. While waiting, the
        # command slot can still be overwritten by a newer colour.
        wait_for = min_interval - (loop.time() - last_send)
        if wait_for > 0:
            await asyncio.sleep(wait_for)

        # Consume any updates that arrived during the wait; the slot below
        # already contains the newest one.
        command_events[name].clear()
        command = latest_commands[name]
        version = command_versions[name]
        if command is None:
            continue

        rgb, brightness = command

        # A user can explicitly switch an individual device off from the GUI.
        # In that case keep its independent sender alive but suppress sync frames.
        if not device_is_enabled(ip):
            device_states[name] = False
            continue

        try:
            await device["light"].turn_on(
                PilotBuilder(rgb=rgb, brightness=brightness)
            )
            last_send = loop.time()
            was_unavailable = device_states[name] is False
            device_states[name] = True

            if was_unavailable:
                print(f"\\nWiZ {name} ({ip}) reconnected.")

        except Exception as exc:
            device_states[name] = False
            error_time = loop.time()

            if (
                error_time - device_last_error_log.get(name, -999.0)
                >= DEVICE_ERROR_LOG_INTERVAL
            ):
                print(
                    f"\\nWiZ {name} ({ip}) unavailable: "
                    f"{type(exc).__name__}: {exc}"
                )
                device_last_error_log[name] = error_time

            # Keep running independently. The latest command is retried
            # after the configured delay; other devices are unaffected.
            await asyncio.sleep(DEVICE_RETRY_INTERVAL)
            command_events[name].set()
            continue

        # If a newer frame arrived while this command was in flight,
        # immediately process the newest one (subject to the send interval).
        if command_versions[name] != version:
            command_events[name].set()


# ============================================================
# MAIN
# ============================================================

async def main():

    devices = configured_devices()
    if not devices:
        raise RuntimeError(
            "No WiZ devices configured. Set at least device.bulb_ip "
            "or another device IP in config.json."
        )

    # Each device has its own sender task and one latest-command slot.
    # Dispatch the strip first so its sender is woken before the bulbs.
    # This is a small scheduling head start, not a guarantee of network order.
    dispatch_devices = sorted(
        devices,
        key=lambda device: 0 if device["role"] == "strip" else 1,
    )
    device_states = {device["name"]: None for device in devices}
    device_last_error_log = {}
    latest_commands = {device["name"]: None for device in devices}
    command_versions = {device["name"]: 0 for device in devices}
    command_events = {
        device["name"]: asyncio.Event() for device in devices
    }
    sender_tasks = [
        asyncio.create_task(
            device_sender(
                device,
                latest_commands,
                command_versions,
                command_events,
                device_states,
                device_last_error_log,
            ),
            name=f"wiz-sender-{device['name'].lower()}",
        )
        for device in dispatch_devices
    ]

    speaker = sc.default_speaker()

    loopback = sc.get_microphone(
        id=str(speaker.id),
        include_loopback=True
    )

    screen = mss.MSS()

    if MONITOR_NUMBER >= len(screen.monitors):

        print(
            f"Monitor {MONITOR_NUMBER} not found."
        )

        print(
            f"Physical monitors detected: "
            f"{len(screen.monitors) - 1}"
        )

        return

    monitor = screen.monitors[
        MONITOR_NUMBER
    ]


    # ========================================================
    # STARTUP
    # ========================================================

    print()
    print("============================================")
    print("      WiZ AMBIENT SYNC V2 — STAGE 4.0")
    print("              MUSICAL FLOW")
    print("============================================")
    print()

    print("Audio:", speaker.name)

    print(
        "Monitor:",
        MONITOR_NUMBER,
        f"({monitor['width']}x{monitor['height']})"
    )

    print()
    print("Energy    -> brightness")
    print("Bass      -> weight / pulse")
    print("Mids      -> colour travel")
    print("Highs     -> colour agility")
    print("Transient -> flow momentum")
    print("Screen    -> colour palette")
    print()
    print("Configured WiZ devices:")
    for device in devices:
        print(f"  {device['name']}: {device['ip']} ({device['role']})")
    print(
        "Brightness compensation:",
        DEVICE_COUNT_SCALES,
        "| secondary scale:",
        f"{SECONDARY_BRIGHTNESS_SCALE:.2f}",
        "| strip scale:",
        f"{STRIP_BRIGHTNESS_SCALE:.2f}"
    )
    print(
        f"Unavailable devices retry every {DEVICE_RETRY_INTERVAL:.1f}s."
    )

    print()
    print("SYNC ACTIVE — Ctrl+C to stop")
    print()


    # ========================================================
    # AUDIO STATE
    # ========================================================

    envelope = 0.0

    bass_envelope = 0.0
    mid_envelope = 0.0
    high_envelope = 0.0

    energy_level = 0.0
    phrase_level = 0.0

    envelope_history = deque(
        maxlen=HISTORY_SIZE
    )

    bass_history = deque(
        maxlen=BASS_HISTORY_SIZE
    )

    mid_history = deque(
        maxlen=BAND_HISTORY_SIZE
    )

    high_history = deque(
        maxlen=BAND_HISTORY_SIZE
    )

    onset_history = deque(
        maxlen=ONSET_HISTORY_SIZE
    )

    previous_onset_signal = 0.0


    # ========================================================
    # LIGHT STATE
    # ========================================================

    smooth_brightness = float(
        ABSOLUTE_MIN_BRIGHTNESS
    )

    smooth_rgb = np.array(
        [120.0, 0.0, 255.0],
        dtype=np.float64
    )

    # The strip follows the same musical target, but uses less smoothing
    # so it catches up faster than the bulbs.
    strip_smooth_rgb = smooth_rgb.copy()

    smooth_luminance = 0.50

    previous_anchor_hue = None


    # ========================================================
    # FLOW STATE
    # ========================================================

    flow_phase = 0.0
    strip_flow_phase = 0.0
    flow_momentum = 0.0

    brightness_pulse = 0.0

    last_impact_time = -999.0


    # ========================================================
    # TIMING
    # ========================================================

    last_update = 0.0

    loop = asyncio.get_running_loop()
    live_tuning = read_live_tuning()
    last_tuning_check = 0.0
    last_device_control_check = 0.0
    refresh_device_control_state()


    # ========================================================
    # AUDIO CAPTURE
    # ========================================================

    with loopback.recorder(
        samplerate=SAMPLE_RATE
    ) as recorder:

        while True:

            data = recorder.record(
                numframes=BLOCK_SIZE
            )

            tuning_now = loop.time()
            if tuning_now - last_tuning_check >= 0.5:
                live_tuning = read_live_tuning()
                last_tuning_check = tuning_now
            # Optional UI power toggles are refreshed at 1 Hz in the main
            # loop; per-device senders only consult the in-memory cache.
            if tuning_now - last_device_control_check >= 1.0:
                refresh_device_control_state()
                last_device_control_check = tuning_now

            if data.ndim > 1:

                audio = np.mean(
                    data,
                    axis=1
                )

            else:

                audio = data

            audio = (
                audio
                - np.mean(audio)
            )


            # =================================================
            # RMS / ENVELOPE
            # =================================================

            rms = float(
                np.sqrt(
                    np.mean(
                        audio ** 2
                    )
                )
            )

            envelope = smooth_value(
                envelope,
                rms,
                ENVELOPE_ATTACK,
                ENVELOPE_RELEASE
            )

            envelope_history.append(
                envelope
            )


            # =================================================
            # RHYTHM NORMALISATION
            # =================================================

            if len(envelope_history) >= 20:

                history = np.array(
                    envelope_history
                )

                floor = float(
                    np.percentile(
                        history,
                        FLOOR_PERCENTILE
                    )
                )

                ceiling = float(
                    np.percentile(
                        history,
                        CEILING_PERCENTILE
                    )
                )

            else:

                floor = 0.0

                ceiling = max(
                    envelope * 1.5,
                    0.02
                )

            dynamic_range = max(
                ceiling - floor,
                MIN_DYNAMIC_RANGE
            )

            rhythm_level = float(
                np.clip(
                    (
                        envelope - floor
                    )
                    / dynamic_range,
                    0,
                    1
                )
            )


            # =================================================
            # FFT
            # =================================================

            windowed = (
                audio
                * np.hanning(
                    len(audio)
                )
            )

            spectrum = np.abs(
                np.fft.rfft(
                    windowed
                )
            )

            freqs = np.fft.rfftfreq(
                len(windowed),
                1 / SAMPLE_RATE
            )


            # =================================================
            # RAW FREQUENCY BANDS
            # =================================================

            raw_bass = band_energy(
                spectrum,
                freqs,
                BASS_LOW,
                BASS_HIGH
            )

            raw_mid = band_energy(
                spectrum,
                freqs,
                MID_LOW,
                MID_HIGH
            )

            raw_high = band_energy(
                spectrum,
                freqs,
                HIGH_LOW,
                HIGH_HIGH
            )


            # =================================================
            # BAND ENVELOPES
            # =================================================

            bass_envelope = smooth_value(
                bass_envelope,
                raw_bass,
                BASS_ATTACK,
                BASS_RELEASE
            )

            mid_envelope = smooth_value(
                mid_envelope,
                raw_mid,
                MID_ATTACK,
                MID_RELEASE
            )

            high_envelope = smooth_value(
                high_envelope,
                raw_high,
                HIGH_ATTACK,
                HIGH_RELEASE
            )


            # =================================================
            # BAND NORMALISATION
            # =================================================

            bass_level = normalise_history(
                bass_envelope,
                bass_history
            )

            mid_level = normalise_history(
                mid_envelope,
                mid_history
            )

            high_level = normalise_history(
                high_envelope,
                high_history
            )

            # User-adjustable sensitivity multipliers preserve the original
            # Stage 4.0 defaults at 1.0 and only shape downstream response.
            bass_level = float(np.clip(bass_level * live_tuning["bass_sensitivity"], 0.0, 1.0))
            mid_level = float(np.clip(mid_level * live_tuning["mid_sensitivity"], 0.0, 1.0))
            high_level = float(np.clip(high_level * live_tuning["high_sensitivity"], 0.0, 1.0))

            # =================================================
            # COMBINED ENERGY
            # =================================================

            combined = (
                ENVELOPE_STRENGTH
                * rhythm_level
                + BASS_STRENGTH
                * bass_level
            )

            combined = float(
                np.clip(
                    combined,
                    0,
                    1
                )
            )


            # =================================================
            # TRANSIENT DETECTION
            # =================================================

            onset_signal = (
                ONSET_BASS_WEIGHT
                * bass_level
                + ONSET_RHYTHM_WEIGHT
                * rhythm_level
            )

            onset_signal = float(
                np.clip(
                    onset_signal,
                    0,
                    1
                )
            )

            onset_history.append(
                onset_signal
            )

            transient_strength = 0.0

            if len(onset_history) >= ONSET_LONG_WINDOW:

                onset_array = np.array(
                    onset_history,
                    dtype=np.float64
                )

                short_average = float(
                    np.mean(
                        onset_array[
                            -ONSET_SHORT_WINDOW:
                        ]
                    )
                )

                background = float(
                    np.mean(
                        onset_array[
                            -ONSET_LONG_WINDOW:
                            -ONSET_SHORT_WINDOW
                        ]
                    )
                )

                relative_rise = max(
                    0.0,
                    short_average
                    - background
                )

                immediate_rise = max(
                    0.0,
                    onset_signal
                    - previous_onset_signal
                )

                transient_strength = (
                    0.65 * relative_rise
                    + 0.35 * immediate_rise
                )

            previous_onset_signal = onset_signal


            # =================================================
            # MACRO ENERGY
            # =================================================

            energy_level = smooth_value(
                energy_level,
                combined,
                ENERGY_ATTACK,
                ENERGY_RELEASE
            )

            energy_level = float(
                np.clip(
                    energy_level,
                    0,
                    1
                )
            )

            # =================================================
            # STAGE 4.2 — MUSICAL PHRASE ENVELOPE
            #
            # This is intentionally slower than the normal
            # energy envelope.
            #
            # Individual notes/impacts should not radically
            # change it. Sustained musical activity should.
            # =================================================

            phrase_target = (
                PHRASE_ENERGY_WEIGHT
                * energy_level

                + PHRASE_MID_WEIGHT
                * mid_level

                + PHRASE_HIGH_WEIGHT
                * high_level
            )

            phrase_target = float(
                np.clip(
                    phrase_target,
                    0.0,
                    1.0
                )
            )

            phrase_target = (
                phrase_target
                ** PHRASE_GAMMA
            )

            phrase_level = smooth_value(
                phrase_level,
                phrase_target,
                PHRASE_ATTACK,
                PHRASE_RELEASE
            )

            phrase_level = float(
                np.clip(
                    phrase_level,
                    0.0,
                    1.0
                )
            )


            # =================================================
            # MUSICAL IMPACT
            # =================================================

            impact_strength = (
                IMPACT_TRANSIENT_WEIGHT
                * transient_strength

                + IMPACT_BASS_WEIGHT
                * bass_level
                * transient_strength

                + IMPACT_ENERGY_WEIGHT
                * energy_level
                * transient_strength
            )

            impact_weight_sum = (
                IMPACT_TRANSIENT_WEIGHT
                + IMPACT_BASS_WEIGHT
                + IMPACT_ENERGY_WEIGHT
            )

            impact_strength /= (
                impact_weight_sum
            )

            impact_strength = float(
                np.clip(
                    impact_strength,
                    0,
                    1
                )
            )


            if impact_strength >= IMPACT_MAJOR:

                impact_class = "MAJOR"

            elif impact_strength >= IMPACT_STRONG:

                impact_class = "STRONG"

            elif impact_strength >= IMPACT_NORMAL:

                impact_class = "NORMAL"

            else:

                impact_class = "NONE"


            # =================================================
            # UPDATE
            # =================================================

            now = loop.time()

            if (
                now - last_update
                >= UPDATE_INTERVAL
            ):

                dt = (
                    UPDATE_INTERVAL
                    if last_update == 0
                    else min(
                        now - last_update,
                        0.25
                    )
                )


                # =============================================
                # IMPACT EVENT
                # =============================================

                impact_triggered = False

                if (
                    transient_strength >= ONSET_THRESHOLD
                    and impact_strength >= IMPACT_NORMAL
                    and now - last_impact_time >= 0.12
                ):

                    impact_triggered = True
                    last_impact_time = now

                    if impact_class == "MAJOR":

                        impulse = (
                            FLOW_MAJOR_IMPULSE
                        )

                    elif impact_class == "STRONG":

                        impulse = (
                            FLOW_STRONG_IMPULSE
                        )

                    else:

                        impulse = (
                            FLOW_NORMAL_IMPULSE
                        )

                    if impact_class == "MAJOR":

                        brightness_pulse += (
                            MAJOR_IMPACT_BOOST
                        )

                    elif impact_class == "STRONG":

                        brightness_pulse += (
                            STRONG_IMPACT_BOOST
                        )

                    else:

                        brightness_pulse += (
                            NORMAL_IMPACT_BOOST
                        )

                    brightness_pulse = float(
                        np.clip(
                            brightness_pulse,
                            0.0,
                            12.0
                        )
                    )

                    # Stronger actual impacts create a little
                    # extra momentum within their class.
                    impulse *= (
                        0.75
                        + 0.50
                        * impact_strength
                    )

                    flow_momentum += impulse * live_tuning["transient_strength"]


                # =============================================
                # MOMENTUM DECAY
                # =============================================

                flow_momentum *= (
                    FLOW_MOMENTUM_DECAY
                    ** (
                        dt / UPDATE_INTERVAL
                    )
                )
                brightness_pulse *= (
                    BRIGHTNESS_PULSE_DECAY
                    ** (
                        dt / UPDATE_INTERVAL
                    )
                )

                if brightness_pulse < 0.05:
                    brightness_pulse = 0.0

                flow_momentum = float(
                    np.clip(
                        flow_momentum,
                        0,
                        1
                    )
                )


                # =============================================
                # FLOW SPEED
                # =============================================

                # =============================================
                # STAGE 4.3 — FLOW SPEED
                # =============================================

                base_energy_speed = (
                    FLOW_BASE_SPEED
                    + FLOW_ENERGY_SPEED
                    * energy_level
                )

                spectral_speed = (
                    FLOW_MID_SPEED
                    * mid_level
                    + FLOW_HIGH_SPEED
                    * high_level
                )

                phrase_speed = (
                    PHRASE_SPEED_MIN
                    + phrase_level
                    * (
                        PHRASE_SPEED_MAX
                        - PHRASE_SPEED_MIN
                    )
                )

                # Phrase controls the broader movement.
                phrase_component = (
                    (
                        base_energy_speed
                        + spectral_speed
                        * (
                            1.0
                            - PHRASE_INSTANT_SPEED_WEIGHT
                        )
                    )
                    * phrase_speed
                )

                # A portion of M/H bypasses phrase scaling,
                # preserving Stage 4.1's immediate agility.
                instant_component = (
                    spectral_speed
                    * PHRASE_INSTANT_SPEED_WEIGHT
                )

                flow_speed = (
                    phrase_component
                    + instant_component
                    + flow_momentum
                )
                flow_speed *= live_tuning["transition_speed"]
                # Bulbs retain the original shared colour-travel speed.
                flow_phase += flow_speed * dt

                # The strip gets a separate, slightly faster phase while
                # using the exact same screen-derived palette.
                strip_flow_phase += (
                    flow_speed * STRIP_FLOW_SPEED_MULTIPLIER * dt
                )

                # Avoid unlimited floating-point growth.
                if flow_phase > 10000.0:
                    flow_phase %= 6.0
                if strip_flow_phase > 10000.0:
                    strip_flow_phase %= 6.0


                # =============================================
                # SCREEN
                # =============================================

                screenshot = screen.grab(
                    monitor
                )

                palette, screen_luminance = (
                    analyse_screen(
                        screenshot
                    )
                )


                # =============================================
                # SCREEN LUMINANCE
                # =============================================

                smooth_luminance += (
                    LUMINANCE_SMOOTHING
                    * (
                        screen_luminance
                        - smooth_luminance
                    )
                )

                screen_min, screen_max = (
                    get_screen_brightness_range(
                        smooth_luminance
                    )
                )

                effective_min = (
                    ABSOLUTE_MIN_BRIGHTNESS
                    * (
                        1
                        - SCREEN_BRIGHTNESS_INFLUENCE
                    )
                    + screen_min
                    * SCREEN_BRIGHTNESS_INFLUENCE
                )

                effective_max = (
                    ABSOLUTE_MAX_BRIGHTNESS
                    * (
                        1
                        - SCREEN_BRIGHTNESS_INFLUENCE
                    )
                    + screen_max
                    * SCREEN_BRIGHTNESS_INFLUENCE
                )


                # =============================================
                # MACRO BRIGHTNESS
                # =============================================

                audio_level = (
                    energy_level
                    ** BRIGHTNESS_GAMMA
                )

                target_brightness = (
                    effective_min
                    + audio_level
                    * (
                        effective_max
                        - effective_min
                    )
                )


                # =============================================
                # SMALL RHYTHMIC BREATHING
                # =============================================

                target_brightness += (
                    RHYTHM_ACCENT
                    * rhythm_level
                    * (
                        0.55
                        + 0.45
                        * bass_level
                    )
                )


                # =============================================
                # IMPACT BRIGHTNESS
                #
                # Stage 3.3 forced brightness to 72/80/90+.
                # Stage 4 adds an accent instead.
                # =============================================

                # =============================================
                # STAGE 4.1 — BRIGHTNESS PULSE
                #
                # Impacts inject brightness energy.
                # The pulse then decays over subsequent frames,
                # producing breathing rather than a one-frame flash.
                # =============================================

                target_brightness += brightness_pulse


                target_brightness = float(
                    np.clip(
                        target_brightness,
                        ABSOLUTE_MIN_BRIGHTNESS,
                        ABSOLUTE_MAX_BRIGHTNESS
                    )
                )


                # =============================================
                # BRIGHTNESS ATTACK / RELEASE
                # =============================================

                smooth_brightness = smooth_value(
                    smooth_brightness,
                    target_brightness,
                    BRIGHTNESS_ATTACK,
                    BRIGHTNESS_RELEASE
                )

                brightness = int(
                    np.clip(
                        round(smooth_brightness),
                        ABSOLUTE_MIN_BRIGHTNESS,
                        ABSOLUTE_MAX_BRIGHTNESS
                    )
                )

                wiz_brightness = int(
                    np.clip(
                        round(
                            brightness
                            * 255
                            / 100
                        ),
                        1,
                        255
                    )
                )


                # =============================================
                # MUSICAL MOTION DEPTH
                # =============================================

                # =============================================
                # STAGE 4.2 — PHRASE-GATED MOTION DEPTH
                #
                # Fast spectrum still determines the texture of
                # movement, but the slow phrase envelope determines
                # how much of that movement is allowed through.
                # =============================================

                instant_motion = (
                    FLOW_QUIET_MOTION

                    + FLOW_BASS_DEPTH
                    * bass_level

                    + FLOW_MID_DEPTH
                    * mid_level

                    + FLOW_HIGH_DEPTH
                    * high_level
                )

                instant_motion = float(
                    np.clip(
                        instant_motion,
                        0.0,
                        1.0
                    )
                )

                instant_motion = (
                    instant_motion
                    ** FLOW_MOTION_GAMMA
                )

                # =============================================
                # STAGE 4.3 — DUAL-TIMESCALE MOTION
                #
                # Phrase = broad musical structure
                # Instant = immediate musical liveliness
                #
                # Unlike 4.2, instantaneous motion is NOT simply
                # multiplied by the phrase envelope.
                # =============================================

                phrase_depth = (
                    PHRASE_DEPTH_MIN
                    + phrase_level
                    * (
                        PHRASE_DEPTH_MAX
                        - PHRASE_DEPTH_MIN
                    )
                )

                motion_depth = (
                    PHRASE_DEPTH_WEIGHT
                    * phrase_depth

                    + PHRASE_INSTANT_DEPTH_WEIGHT
                    * instant_motion
                )

                # Rhythmic impacts temporarily push colour movement
                # beyond both envelopes.
                motion_depth += (
                    0.14
                    * flow_momentum
                )
                motion_depth *= live_tuning["motion_intensity"]

                motion_depth = float(
                    np.clip(
                        motion_depth,
                        0.0,
                        1.0
                    )
                )

                # Strip-only musical emphasis. Reuse the same detected
                # bass/mid/high levels and phrase envelope; do not alter
                # audio capture, FFT, normalisation, or bulb motion.
                strip_instant_motion = (
                    FLOW_QUIET_MOTION
                    + FLOW_BASS_DEPTH
                    * STRIP_BASS_MOTION_MULTIPLIER
                    * bass_level
                    + FLOW_MID_DEPTH * mid_level
                    + FLOW_HIGH_DEPTH * high_level
                )
                strip_instant_motion = float(np.clip(
                    strip_instant_motion, 0.0, 1.0
                )) ** FLOW_MOTION_GAMMA

                strip_motion_depth = (
                    PHRASE_DEPTH_WEIGHT * phrase_depth
                    + PHRASE_INSTANT_DEPTH_WEIGHT
                    * strip_instant_motion
                )
                strip_motion_depth += (
                    0.14
                    * STRIP_TRANSIENT_MOTION_MULTIPLIER
                    * flow_momentum
                )
                strip_motion_depth *= STRIP_MOTION_DEPTH_MULTIPLIER
                strip_motion_depth *= live_tuning["motion_intensity"]
                strip_motion_depth = float(np.clip(
                    strip_motion_depth, 0.0, 1.0
                ))


                # =============================================
                # COLOUR FLOW
                # =============================================

                palette_count = 0
                scene_change = False

                if palette is not None:

                    palette_count = len(
                        palette
                    )

                    anchor_hue = (
                        palette[0]["hue"]
                    )


                    # =========================================
                    # SCENE CHANGE
                    # =========================================

                    if previous_anchor_hue is not None:

                        hue_change = (
                            circular_hue_distance(
                                anchor_hue,
                                previous_anchor_hue
                            )
                        )

                        scene_change = (
                            hue_change
                            > SCENE_CHANGE_THRESHOLD
                        )

                    previous_anchor_hue = (
                        anchor_hue
                    )


                    # =========================================
                    # GET MOVING TARGET
                    # =========================================

                    target_rgb = get_flow_target(
                        palette,
                        flow_phase,
                        motion_depth
                    )
                    strip_target_rgb = get_flow_target(
                        palette,
                        strip_flow_phase,
                        strip_motion_depth
                    )


                    if target_rgb is not None:

                        if scene_change:

                            smoothing = (
                                SCENE_CHANGE_SMOOTHING
                            )

                        else:

                            # High-frequency content slightly
                            # increases responsiveness.
                            smoothing = (
                                COLOR_SMOOTHING
                                + 0.10
                                * high_level
                            )

                        smoothing = float(
                            np.clip(
                                smoothing,
                                0,
                                1
                            )
                        )

                        smooth_rgb += (
                            smoothing
                            * (
                                target_rgb
                                - smooth_rgb
                            )
                        )

                        strip_smoothing = float(
                            np.clip(STRIP_COLOUR_SMOOTHING, 0.0, 1.0)
                        )
                        if scene_change:
                            strip_smoothing = max(strip_smoothing, 0.94)

                        if strip_target_rgb is not None:
                            strip_smooth_rgb += (
                                strip_smoothing
                                * (strip_target_rgb - strip_smooth_rgb)
                            )


                # =============================================
                # RGB OUTPUT
                # =============================================

                rgb = tuple(
                    int(round(x))
                    for x in np.clip(
                        smooth_rgb,
                        0,
                        255
                    )
                )


                # =============================================
                # ADAPTIVE MULTI-DEVICE BRIGHTNESS
                # =============================================

                # Only confirmed reachable devices contribute to compensation.
                active_count = sum(
                    state is True for state in device_states.values()
                )
                active_count = max(1, active_count)

                compensation = DEVICE_COUNT_SCALES.get(
                    active_count,
                    DEVICE_COUNT_SCALES.get(
                        max(DEVICE_COUNT_SCALES),
                        1.0
                    )
                )

                coordinated_brightness = int(np.clip(
                    round(brightness * compensation),
                    ABSOLUTE_MIN_BRIGHTNESS,
                    ABSOLUTE_MAX_BRIGHTNESS
                ))

                # Publish to the strip first, waking its independent sender
                # before the bulb senders. Replacing a slot never queues
                # stale colour frames.
                strip_rgb = tuple(
                    int(round(channel))
                    for channel in np.clip(strip_smooth_rgb, 0, 255)
                )

                for device in dispatch_devices:
                    name = device["name"]
                    output_rgb = strip_rgb if device["role"] == "strip" else rgb
                    device_brightness_input = coordinated_brightness
                    if device["role"] == "strip":
                        device_brightness_input += round(
                            STRIP_TRANSIENT_BRIGHTNESS_BOOST
                            * flow_momentum
                        )

                    device_rgb, device_brightness, _ = device_output(
                        device,
                        output_rgb,
                        device_brightness_input
                    )

                    latest_commands[name] = (
                        device_rgb,
                        device_brightness,
                    )
                    command_versions[name] += 1
                    command_events[name].set()

                # Let the independent sender tasks run immediately after
                # publishing each frame. Without this yield, the main loop
                # can keep processing frames while device senders starve.
                await asyncio.sleep(0)

                active_names = [
                    name for name, state in device_states.items()
                    if state is True
                ]
                active_count = len(active_names)

                # =============================================
                # STATUS
                # =============================================

                if SHOW_STATUS or TELEMETRY_ENABLED:

                    bar_width = 16

                    filled = int(
                        np.clip(
                            energy_level,
                            0,
                            1
                        )
                        * bar_width
                    )

                    visualizer = (
                        "#" * filled
                        + "·"
                        * (
                            bar_width
                            - filled
                        )
                    )

                    event = (
                        impact_class
                        if impact_triggered
                        else "----"
                    )

                    status = (
                        f"[{visualizer}] "
                        f"B:{bass_level:.2f} "
                        f"M:{mid_level:.2f} "
                        f"H:{high_level:.2f} "
                        f"E:{energy_level:.2f} "
                        f"Ph:{phrase_level:.2f} "
                        f"L:{brightness:3}% "
                        f"Set:{coordinated_brightness:3}% "
                        f"Dev:{active_count}/{len(devices)} "
                        f"F:{flow_phase % 1.0:.2f} "
                        f"Mo:{flow_momentum:.2f} "
                        f"D:{motion_depth:.2f} "
                        f"Pu:{brightness_pulse:.1f} "
                        f"{event:6}"
                    )
                    if TELEMETRY_ENABLED:
                        telemetry = {
                            "bass": round(float(bass_level), 4),
                            "mids": round(float(mid_level), 4),
                            "highs": round(float(high_level), 4),
                            "energy": round(float(energy_level), 4),
                            "phrase": round(float(phrase_level), 4),
                            "brightness": int(brightness),
                            "target_brightness": int(coordinated_brightness),
                            "devices_active": int(active_count),
                            "devices_total": int(len(devices)),
                            "flow": round(float(flow_phase % 1.0), 4),
                            "momentum": round(float(flow_momentum), 4),
                            "motion_depth": round(float(motion_depth), 4),
                            "pulse": round(float(brightness_pulse), 3),
                            "impact": str(event).strip(),
                        }
                        print("WIZ_METRIC:" + json.dumps(telemetry, separators=(",", ":")), flush=True)
                    else:
                        print("\r" + status, end="", flush=True)

                last_update = now


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        print()
        print()
        print(
            "Ambient sync stopped."
        )