"""
Utility functions and constants for the Napari Scanning SPD application.
"""

import os
import numpy as np
import tifffile


def experiment_data_root() -> str:
    """Directory where confocal and ODMR measurement folders are written.

    Defaults to ``<repo>/data`` so dated ``mmddyy`` folders stay inside the
    project and can be cleaned manually. Override with the
    ``NV_EXPERIMENT_DATA`` environment variable if needed.
    """
    env = os.environ.get('NV_EXPERIMENT_DATA')
    if env:
        return env
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(repo_root, 'data')

# Calibration constant: microns per volt based on empirical measurements
# Air objective 40x 0.95 NA
#MICRONS_PER_VOLT = 130 
# Oil objective 
#MICRONS_PER_VOLT = 51
# Oil objective Zeiss 100x 1.4 NA
MICRONS_PER_VOLT = 24

# Maximum zoom level allowed in the scanning interface
MAX_ZOOM_LEVEL = 9

# Default RFSoC live-PL integration request in picoseconds (5 ms)
BINWIDTH = int(5e9)

# Z piezo analog control calibration (DAQ ao2 -> EXT IN of the piezo controller)
# The piezo is initialized/kept in closed loop by external Thorlabs software; the
# DAQ only commands position through the EXT IN BNC. In closed loop, 0-10 V maps
# to 0-450 um, i.e. 45 um/V.
Z_UM_PER_VOLT = 45.0            # Calibration factor (micrometers per volt)
Z_MAX_TRAVEL_UM = 450.0        # Full travel of the piezo stage in micrometers
Z_VOLTAGE_RANGE = (0.0, 10.0)  # Allowed EXT IN voltage range for closed-loop control

# --- RFSoC4x2 / DAQ configuration (edit here when hardware moves) ------------
# The RFSoC is the timing master on the rfsoc4x2 branch.  Pin/channel defaults
# must be checked against the QickConfig printed by the bitfile before use.
RFSOC_IP = os.environ.get("NV_RFSOC_IP", "192.168.0.236")
RFSOC_SERVER_NAME = os.environ.get("NV_RFSOC_SERVER_NAME", "myqick")
RFSOC_FIRMWARE = os.environ.get("NV_RFSOC_FIRMWARE", "photon_counting")
RFSOC_ADC_CHANNEL = 0
RFSOC_MW_CHANNEL = 0
RFSOC_MW_NQZ = 2
# Second tone (DEER target), DAC B combined with channel 0 before the amplifier.
RFSOC_MW2_CHANNEL = 1
RFSOC_MW2_NQZ = 2
RFSOC_AOM_PMOD = 0
RFSOC_PIXEL_CLOCK_PMOD = 1
RFSOC_EDGE_HIGH_THRESHOLD = 8000
RFSOC_EDGE_LOW_THRESHOLD = 500
RFSOC_PIXEL_CLOCK_WIDTH_NS = 100
RFSOC_CONFOCAL_SETTLE_US = 5.0
RFSOC_GALVO_FLYBACK_S = 0.002
# Longest single edge-counting window measured to stay linear on this board:
# window_linearity() found counts proportional to the window from 13 us to 5 s.
# QICK warns above 2**16 readout samples, but that warning covers summing 15-bit
# analog samples into a 32-bit accumulator, which an edge count cannot overflow.
# The next ceiling up is the 31-bit tProc immediate carrying the window, 6.99 s
# at 307.2 MHz, so a dwell is never split: it is one window or it is refused.
RFSOC_MAX_COUNTING_WINDOW_S = 5.0
# How often the board hands counted pixels back to the host mid-scan. It sets
# the readout stride, so it is also how far the image can advance per update:
# one raster line at ordinary dwells, single pixels once a dwell exceeds it.
RFSOC_STREAM_UPDATE_SECONDS = 0.2
# How long a scan or an experiment waits for another process on this PC to let
# go of the board before giving up.  Bounded, so that a GUI thread waiting on an
# experiment script reports who holds the board instead of looking hung.
RFSOC_CLAIM_WAIT_S = float(os.environ.get("NV_RFSOC_CLAIM_WAIT_S", "30"))
# Minimum wall time between napari preview refreshes during a scan.
SCAN_PREVIEW_MIN_SECONDS = 0.2

DAQ_GALVO_X = "Dev1/ao0"
DAQ_GALVO_Y = "Dev1/ao1"
DAQ_PIEZO_Z = "Dev1/ao2"
DAQ_SPD_COUNTER = "Dev1/ctr0"
# Buffered PMOD pixel-clock output -> NI PFI input.
DAQ_RFSOC_CLOCK_INPUT = "/Dev1/PFI8"
DAQ_XOUT_VOLTAGE = "Dev1/ai14"
DAQ_YOUT_VOLTAGE = "Dev1/ai15"


def calculate_scale(V1, V2, image_width_px, microns_per_volt=MICRONS_PER_VOLT):
    """
    Calculate microns per pixel from a voltage span (legacy volt-based helper).

    Kept for backward compatibility with tools/data that reason in volts.
    New code works in micrometers and should use :func:`um_scale` instead.

    Args:
        V1 (float): Start voltage
        V2 (float): End voltage
        image_width_px (int): Image width in pixels
        microns_per_volt (float): Calibration factor (microns per volt)

    Returns:
        float: Scale in microns per pixel
    """
    voltage_span = abs(V2 - V1)
    scan_width_microns = voltage_span * microns_per_volt
    return scan_width_microns / image_width_px


def um_scale(a_um, b_um, n_pixels):
    """Micrometers-per-pixel for an axis spanning ``a_um``..``b_um`` in ``n_pixels``.

    This is the canonical scale helper: inputs are already in micrometers, so no
    calibration factor is applied.
    """
    if n_pixels <= 0:
        return 0.0
    return abs(float(b_um) - float(a_um)) / n_pixels 


def save_tiff_with_imagej_metadata(image_data, filepath, x_points, y_points, scan_config, timestamp=None):
    """
    Save TIFF file with comprehensive metadata that ImageJ/Fiji can interpret.
    
    Args:
        image_data (np.ndarray): 2D image array
        filepath (str): Path to save the TIFF file
        x_points (np.ndarray): Fast-axis (columns) scan positions in micrometers
        y_points (np.ndarray): Slow-axis (rows) scan positions in micrometers
        scan_config (dict): Configuration dictionary with scan parameters. May
            include ``axis_names`` (fast, slow), ``scan_mode``, ``microns_per_volt``
            and ``z_um_per_volt`` so the description reflects the real scanned axes.
        timestamp (str): Optional timestamp string
    """
    height, width = image_data.shape

    # Real scanned axes (fast -> columns, slow -> rows). Defaults keep older
    # XY-only callers working.
    axis_names = scan_config.get('axis_names', ('x', 'y'))
    fast_name = str(axis_names[0]).upper()
    slow_name = str(axis_names[1]).upper()
    scan_mode = scan_config.get('scan_mode', None)

    # Positions are already in micrometers (canonical unit).
    microns_per_pixel_x = um_scale(x_points[0], x_points[-1], width)
    microns_per_pixel_y = um_scale(y_points[0], y_points[-1], height)
    
    # Convert to pixels per micron for ImageJ (resolution tags expect pixels per unit)
    pixels_per_micron_x = 1.0 / microns_per_pixel_x if microns_per_pixel_x > 0 else 1.0
    pixels_per_micron_y = 1.0 / microns_per_pixel_y if microns_per_pixel_y > 0 else 1.0
    
    # Create ImageJ-compatible metadata
    # ImageJ expects specific format for the ImageDescription
    imagej_metadata = {
        'ImageWidth': width,
        'ImageLength': height,
        'BitsPerSample': 32,  # float32
        'SampleFormat': 3,    # IEEE floating point
        'unit': 'micron',
        'finterval': scan_config.get('dwell_time', 0.1),
        'spacing': min(microns_per_pixel_x, microns_per_pixel_y),
        'loop': False,
        'min': float(np.nanmin(image_data)),
        'max': float(np.nanmax(image_data)),
    }
    
    # Create comprehensive ImageDescription
    image_description = f"""ImageJ=1.54
images=1
slices=1
frames=1
unit=micron
finterval={scan_config.get('dwell_time', 0.1)}
spacing={min(microns_per_pixel_x, microns_per_pixel_y)}
loop=false
min={float(np.nanmin(image_data))}
max={float(np.nanmax(image_data))}

Confocal NV Microscopy Data
Acquisition Software: NV Scanning Microscopy
Scan Mode: {scan_mode if scan_mode is not None else fast_name + slow_name}
{fast_name} Range: {x_points[0]:.3f} to {x_points[-1]:.3f} um
{slow_name} Range: {y_points[0]:.3f} to {y_points[-1]:.3f} um
{fast_name} Resolution: {width} pixels
{slow_name} Resolution: {height} pixels
{fast_name} Scale: {microns_per_pixel_x:.6f} um/pixel
{slow_name} Scale: {microns_per_pixel_y:.6f} um/pixel
Calibration (galvo): {scan_config.get('microns_per_volt', MICRONS_PER_VOLT)} um/V"""

    z_um_per_volt = scan_config.get('z_um_per_volt', None)
    if z_um_per_volt is not None:
        image_description += f"\nCalibration (piezo Z): {z_um_per_volt} um/V"

    image_description += f"""
Dwell Time: {scan_config.get('dwell_time', 0.1)} s
Detector: Single Photon Detector (SPD)
Scanner: Thorlabs LSKGG4 Galvo-Galvo"""

    if timestamp:
        image_description += f"\nTimestamp: {timestamp}"
    
    # Prepare TIFF tags for ImageJ compatibility
    # Resolution unit: 1=none, 2=inches, 3=centimeters
    # We'll use unit=1 (none) and put the actual scale in ImageDescription
    tiff_tags = {
        'ImageDescription': image_description,
        'Software': 'NV Scanning Microscopy v1.0',
        'XResolution': (pixels_per_micron_x, 1),  # Stored as rational number
        'YResolution': (pixels_per_micron_y, 1),  # Stored as rational number
        'ResolutionUnit': 1,  # No absolute unit (we specify in description)
    }
    
    # Save TIFF with metadata
    tifffile.imwrite(
        filepath,
        image_data.astype(np.float32),
        metadata=tiff_tags,
        resolution=(pixels_per_micron_x, pixels_per_micron_y),
        imagej=True  # Enable ImageJ-specific metadata format
    )
    
    print(f"💾 TIFF saved with ImageJ metadata: {filepath}")
    print(f"📏 Scale: {microns_per_pixel_x:.3f} × {microns_per_pixel_y:.3f} um/pixel") 