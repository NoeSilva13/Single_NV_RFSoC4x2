# Changelog

All notable changes to this project will be documented in this file following [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) guidelines.

## [RFSoC4x2] - 2026-11-06
### Added
- DEER spectrum (`rfsoc/deer_program.py`, `DEERSpectrumFineRes`): a Hahn echo
  on the sensor on generator 0 and a pi pulse on generator 1 whose frequency
  is swept in the FPGA, as block 11 of `run_odmr_experiments.py`. Each point
  reads the echo with and without the target pulse; `deer_spectrum_result`
  fits the dip for the target resonance and linewidth.
- `RFSOC_MW2_CHANNEL` / `RFSOC_MW2_NQZ` for the second tone,
  `validate_tone()` and a `mw2_frequency_hz` check in
  `validate_hardware_settings()`, and `check_second_generator()` in
  `connection_report()`, which refuses a second generator whose samples per
  clock or DDS clock differ from generator 0's.

## [RFSoC4x2] - 2026-09-17
### Added
- The full FineRes experiment set as uncommentable blocks in
  `run_odmr_experiments.py`: PL intensity (and `live_pl`), dark counts, CW ODMR
  (and `live_cwodmr`), pulsed ODMR, readout-window calibration, Rabi, Ramsey,
  Hahn echo, CPMG-N and T1. Each pulsed sweep is one FineRes acquire; the
  readout window is walked from the host under a single board claim. Fits
  report the numbers the next experiment needs: resonance, pi/2 and pi
  lengths, detuning and T2*, T2, T1, and the three readout timings. Edit the
  kwargs in the block; each experiment lists every parameter in its call.
- A claim on the board that spans processes, so `run_odmr_experiments.py` can run
  next to the confocal app: each experiment holds the board for its sweep instead
  of aborting whatever the app had running, and the app's live count plot fills
  the gaps. `RFSoCSession.acquisition()` now takes an OS file lock (one file per
  board IP, in the temp directory) on top of its thread lock. The kernel releases
  it when the holder exits, killed or not.
- `RFSOC_CLAIM_WAIT_S` (30 s, `NV_RFSOC_CLAIM_WAIT_S`): how long a scan or an
  experiment waits for another process. Past it the caller gives up naming the
  holder's PID and what to do about it, because a GUI scan thread blocking
  indefinitely on a script is indistinguishable from a hang.

### Changed
- Sweep CSVs now start with the experimental configuration: `#` comment lines
  for requested parameters, executed timings, and the fit, then the same data
  table as before. `requested` / `executed` on each result now include the
  laser and delay kwargs from `run_odmr_experiments.py`, not only the swept
  axis.
- `run_odmr_experiments.py` is again a file of commented blocks, one
  experiment per block, rather than a CLI of subcommands.
- The live count plot yields the board to anything that asked for it first, in
  this process or another, and leaves a gap in the trace instead of a point.
  Pausing it by hand before running experiments is no longer necessary.
- A scan cancelled by Stop or refused because another process holds the board
  reports it in the napari notification area instead of a console traceback.

### Removed
- `RFSoCSession.busy`, which only ever reported the threads of its own process.

## [RFSoC4x2] - 2026-09-16
### Fixed
- Confocal images no longer show repeated vertical stripes past the point where
  the readouts ran out. Splitting a dwell into K ADC windows put K readouts in
  the accumulated buffer per shot while the streamer addressed it as one, so the
  tail of every line was read from the wrong buffer slots.

### Added
- `ConfocalFrame`, a QICK program nesting the pixel loop inside a line loop, so
  one acquire covers a whole image. A 100x100 scan at 1 ms dwell went from 500
  acquires to one, and from about 100 s to a little over the 10 s it spends
  counting.
- `stream_counts()`, which drains the accumulated buffer a readout stride at a
  time while the tProc keeps counting. The image and its contrast fill in line by
  line (pixel by pixel once one pixel outlasts `RFSOC_STREAM_UPDATE_SECONDS`) at
  no cost in scan time, and Stop now lands mid-frame.
- `window_linearity()` and `acquire_overhead()` bench probes, measuring the
  longest linear edge-counting window and the fixed cost of an acquire.

### Changed
- Dwell is configured freely up to `RFSOC_MAX_COUNTING_WINDOW_S` (5 s, measured
  with that probe) and always counted in a single window, so a pixel holds the
  counter for its whole dwell. A longer dwell is refused, naming the probe to
  re-run, rather than being split behind the user's back.
- Scan previews refresh on a wall-time interval (`SCAN_PREVIEW_MIN_SECONDS`)
  rather than every N lines, which no longer matches how progress arrives.
- Single-axis and Z sweeps run the same program and streaming loop as a raster,
  so their live plot advances during the sweep instead of appearing at the end.
- QICK's analog sum-buffer warning is filtered while an edge-counting program is
  compiled. It fires for every window past 2**16 samples, which every confocal
  dwell and every live-count point now is, and the 32-bit overflow it warns about
  cannot happen when the readout counts pulses instead of summing samples.
- `window_linearity()` sweeps up to 5 s, stepping through every power of two so a
  counter narrower than the value written would reveal itself by where it wraps.
  Shots per window follow a wall-time budget instead of a fixed count, and the
  gap between windows is a fixed 100 us: holding it equal to the window would
  overrun the 31-bit tProc immediate carrying the trigger and the sync after it.

### Removed
- The 65535-sample window cap and the 128-readouts-per-acquire cap. The first
  came from a QICK warning about summing analog samples in a 32-bit accumulator,
  which an edge count cannot overflow; the second had no hardware basis.
- Per-line and per-block confocal acquisition: `ConfocalLine`, `acquire_raster`,
  `run_lines`, the frame-block helpers, `NV_RFSOC_FRAME_ACQUIRE` and
  `RFSOC_FRAME_BLOCK_SECONDS`. Splitting a dwell into K windows summed over K
  passes of the image went with them.

## [RFSoC4x2] - 2026-09-14
### Added
- Dedicated `rfsoc4x2` hardware branch using QICK-DAWG/QICK 0.2.302.
- RFSoC client singleton, validated hardware configuration, bring-up diagnostics,
  native experiment results, and host-only tests.
- `ConfocalLine`, with RFSoC edge counting, PMOD pixel clock, multi-window
  dwell integration, and uncounted flyback clocks.
- FPGA-swept CW ODMR, pulsed ODMR, Rabi, and T1 APIs.

### Changed
- RFSoC is the confocal timing master; NI AO is armed from an external PFI
  sample clock one line at a time.
- The ODMR runner now performs one QICK acquire per complete experiment.

### Removed
- TimeTagger, Pulse Streamer, Rigol drivers/configuration/dependencies, and
  runtime backend selection from this branch. They remain supported on `main`.

## [Unreleased] - 2026-09-08
### Added
- Live matplotlib contrast plot during ODMR/Rabi/T1 sweeps (`live_plot=True` in `PulseBlaster/odmr_experiments.py`), with final multi-panel PDFs still produced by `plot_results()`.
- Repo-local experiment output root [`data/`](data/) via `common.utils.experiment_data_root()` (override with env var `NV_EXPERIMENT_DATA`). Dated `mmddyy` folders for confocal and ODMR live under `data/` and are gitignored.
- Root entry point [`run_odmr_experiments.py`](run_odmr_experiments.py) for the ODMR script workflow.
- Instrument IPs and DAQ channel defaults live in [`common/utils.py`](common/utils.py) alongside optical calibration constants.

### Changed
- ODMR workflow is **script-only**: edit and run [`run_odmr_experiments.py`](run_odmr_experiments.py). Documentation and `requirements.txt` no longer describe a Qt ODMR GUI.
- Spectrometer wavelength/ROI calibration kept as [`Camera/072926SpectrometerCal02OK.json`](Camera/072926SpectrometerCal02OK.json) (load via the spectrometer app file dialog).
- **Repository layout**: confocal engine modules live under [`confocal/`](confocal/); shared helpers under [`common/`](common/); spectrometer guide moved to [`docs/spectrometer.md`](docs/spectrometer.md). Root keeps only app entry points plus docs/meta.
- Device IPs and DAQ channel names no longer hard-coded in multiple modules; constructors default to values from `common.utils` (Rigol unified to the lab address previously used in the ODMR runner).
- ODMR day-to-day parameters live in [`run_odmr_experiments.py`](run_odmr_experiments.py); [`PulseBlaster/odmr_experiments.py`](PulseBlaster/odmr_experiments.py) is the measurement library only.

### Removed
- Deleted the obsolete Qt ODMR GUI (`odmr_gui_qt.py`) and its docs (`PulseBlaster/README_ODMR_GUI.md`); the GUI called removed APIs (`odmr`, `rabi_oscillation`, `t1_decay`) and was out of sync with the contrast methods.
- Deleted unused plot widgets `plot_widgets/pulse_pattern_visualizer.py` and `plot_widgets/single_axis_plot.py` (only the GUI / superseded by pyqtgraph confocal widgets).
- Removed older duplicate spectrometer calibration JSONs from the repo root.
- Deleted unused `pulse_sequence_diagrams/` (SVG guides not referenced by any code).

### Documentation
- Updated main `README.md`, `PulseBlaster/README.md`, and `requirements.txt` for the script-driven ODMR flow, `data/` layout, and package reorganization.

## [Unreleased] - 2026-07-21
### Added
- **Multi-dimensional scanning (XY / XZ / YZ / XYZ)** via a **Scan Mode** selector in the Scan Parameters panel that drives the **New Scan** button. XZ/YZ produce a 2D image in a dedicated napari layer; XYZ produces a 3D volume layer with per-axis `scale`/`units`. Axes not part of the selected mode are held at their current position (galvo held via the persistent AO task, piezo pre-moved and settled).
- Generic N-axis raster engine `raster_engine.py` (`build_raster_waveforms`, `raster_geometry`, `reconstruct`, `run_raster`): composes the fast..slow µm waveform (with inter-line flyback that also covers slow-axis settling), converts each axis to volts via its `DAQAxis`, drives `scanning_core.run_hardware_timed_sweep`, and reconstructs a 2D image or 3D volume. The XY raster now delegates to this engine.
- Axis abstraction `daq_axis.py` (`DAQAxis`): single home for the µm↔V calibration, AO channel, and travel/voltage limits. `DAQZController` is now a thin `DAQAxis` subclass; `axis_x`/`axis_y` (galvo) and `axis_z` (piezo) are instantiated in the app.
- Manual multi-axis control widget `widgets/axis_controls.py` (`AxisControlWidget`): a slider + spinbox per axis (X/Y/Z) that commands the galvo (both AO channels written together) and piezo, and mirrors the scanner's current position. `refresh_positions(x, y, z)` updates the display without moving hardware (used on click-to-move and at the end of a scan) and `move_callback` keeps `current_position_um`/single-axis tracking in sync. It replaces the Z-only piezo widget.
- Click-to-move on the single-axis line-scan plots: left-clicking an X/Y `pyqtgraph` plot moves the galvo to that position (snapping to the nearest measured point) and syncs the axis control widget. The **Scan Z** tab now supports the same click-to-move for the piezo Z (snapped to the nearest measured point, clamped to travel, refreshing the axis control widget).
- Selectable camera resolution in the spectrometer app (`spectrometer_app.py`): a new **Resolution** panel with a preset dropdown plus a **Custom...** width/height entry lets the user change the POA camera image size at runtime (up to the sensor maximum `6252x4176`). `POACameraController.set_resolution()`/`get_max_resolution()` change the image size in place (stop stream, `SetImageSize` with width%4/height%2 clamping, buffer realloc, restart stream), the `CameraWorker` wraps it with a safe thread stop/restart, and applying a resolution resets the ROI to the full frame and restores the default wavelength calibration. The resolution is also saved/restored in the calibration JSON. Removed the hardcoded `6252x480` assumptions across the ROI ranges, visual-ROI bounds/clamps, and dimension handling.

### Changed
- **µm is now the canonical unit for all axes.** Scan Parameters store/return X/Y in micrometers (no hidden volt round-trip); the µm→V conversion happens only at the DAQ boundary (waveform build / analog writes). `on_mouse_click`, single-axis scans, zoom, `ScanPointsManager`, and scale calculation all work in µm. Scale is now `um_scale(a_um, b_um, n_px)` instead of the volt-based `calculate_scale`.
- Configurable piezo settling: when Z is a stepping (slow) axis, the flyback window between lines is widened to cover the user-set **Z Dwell Time** instead of a hardcoded 25 ms, so short Z steps can run faster.
- N-D saving: `.npz` now records `scan_mode`, per-axis µm ranges/points/resolution/scale, `microns_per_volt`, `z_um_per_volt`, `units='um'`, and `format_version`; 2D modes still write CSV/TIFF/PNG. The loader detects 2D vs 3D (adds a 3D volume layer for 3D files) and remains backward compatible with old volt-based files (scale is always µm/px, so they visualize unchanged; Apply converts old volt ranges to µm using the saved/current calibration).
- The piezo Z is set to zero on program close (unchanged). After a scan, only the galvo axes that were actually scanned return to zero; a galvo axis left free (Y in an XZ scan, X in a YZ scan) holds its last position instead of resetting.
- Removed the **"Apply Changes"** button from Scan Parameters; **New Scan** now rebuilds XY points from the live spinboxes at start time (dwell / Z / single-axis already read `get_params()` live).
- Moved Z scan parameters (Z Min / Z Max / Z Resolution / Z Dwell Time) into the Scan Parameters panel; the Scan Z tab now runs a single linear hardware-timed Z sweep from those values (via `run_z_sweep`) and no longer auto-moves the piezo to the peak. Removed the dwell/coarse/fine/range spinboxes from `AutoFocusWidget`. When the sweep finishes it returns the piezo to its pre-sweep Z position instead of leaving it at the ramp's Z max.
- Rewrote the live signal plot (`plot_widgets/live_plot_napari_widget.py`) on `pyqtgraph` with a ring buffer and added lightweight controls (pause/resume, clear, refresh rate, window length, auto-Y, log-Y); the widget width is constrained to the controls row.
- Added a **"bin (ms)"** control to the live signal plot so the user can change the Time Tagger counter integration window at runtime (via a `binwidth_callback` that rebuilds the `Counter`); this binwidth now affects only the live plot.
- Migrated the auto-focus and single-axis scans to hardware-timed DAQ sweeps counted by `CountBetweenMarkers` (DAQ clock defines the bins), matching the 2D raster scan. Both previously used the free-running `Counter`/`binwidth`, which was incorrect since both move the DAQ and the DAQ clock is routed to the Time Tagger.
- Gave the auto-focus widget its own **"Dwell (ms)"** control (default 25 ms), independent of the scan `dwell_time`, since the piezo objective settles far slower than the galvos (~25 ms for a 1-100 µm step per the datasheet).
- Consolidated auto-focus into a single self-contained `AutoFocusWidget` (pyqtgraph): the dwell field, editable sweep parameters (coarse step / fine step / fine range), "Auto Focus" button, and coarse/fine result plot now live in one dock, and the sweep plots each point in real time as it is acquired (no progress bar). Replaced the previous `magicgui` button + separate matplotlib `SingleAxisPlot` dock and the `SignalBridge` (the widget now uses its own internal Qt signals for thread-safe updates).
- Refactored `SingleAxisScanWidget` (`widgets/single_axis_scan.py`) onto `pyqtgraph` with a `QTabWidget`: the X and Y scans now live in separate tabs, each with its own scan button and plot (with a red peak marker), replacing the previous shared matplotlib `SingleAxisPlot`.
- Integrated the auto-focus panel as a third **"Z Axis"** tab inside the single-axis widget (via `SingleAxisScanWidget.add_z_tab`) instead of a separate dock, and renamed its button from "Auto Focus" to **"Scan Z"** so X/Y/Z line scans share one dock.
- Extracted the shared AO + `CountBetweenMarkers` primitive into `scanning_core.py` (`run_hardware_timed_sweep`, `counts_to_rate`); the 2D raster scan, auto-focus, and single-axis scan now all use it and share the same `scan_lock`/`scan_in_progress` mutual exclusion and Stop-button integration.
- The free-running `Counter`/`binwidth` in `confocal_main_control.py` now feeds only the live signal plot.
- Migrated the whole GUI stack from PyQt5 to PySide6 (Qt6) through the `qtpy` abstraction layer: all `PyQt5.*` imports now go through `qtpy.*`, so the binding can be swapped without touching source.
- Applied Qt6 compatibility fixes: `QDesktopWidget().screenGeometry()` replaced with `QGuiApplication.primaryScreen().availableGeometry()`; `.exec_()` → `.exec()`; enums fully qualified (`Qt.AlignmentFlag`, `Qt.Orientation`, `QMessageBox.StandardButton`/`Icon`, `QFileDialog.FileMode`).
- Replaced Thorlabs Kinesis USB Z control with DAQ analog output (`Dev1/ao2` → piezo EXT IN) via new `daq_z_controller.py` (`DAQZController`).
- Moved the auto-focus Z-sweep algorithm into `widgets/auto_focus.py` (`run_focus_sweep`); the Z controller is now a minimal voltage/position mapper.
- Grouped the bottom action buttons (New Scan / Stop / Save / Reset Zoom / Set to Zero / Load Scan) into a compact 3x2 grid and let the axis control widget expand to fill the remaining width.
- "Set to Zero" now also syncs `current_position_um` and refreshes the axis control widget after parking the galvo at (0, 0).
- Disabled mouse pan/zoom and the context menu on the single-axis plots so a left click is used only for click-to-move.
- Fixed region (ROI) zoom for non-XY scans: drawing a rectangle now re-scans the selected region in the mode of the layer just scanned (XY / XZ / YZ) into that same layer, instead of always overwriting the XY layer. Zoom history and Reset Zoom are now mode-aware and restore the original view of whichever mode was zoomed (3D volumes are excluded). Additional follow-up fixes: the zoom-area rectangle is kept on top of newly created scan layers so it stays visible; region zoom now updates the Scan Parameters widget for both scanned axes (including Z for XZ/YZ, via new `z_range`/`z_res` support in `update_values`/`update_scan_parameters`); and starting a New Scan resets the zoom history/level so Reset Zoom applies to the current base scan of any mode.
- Kept all position trackers in sync after a raster scan (including Reset Zoom): the raster `finally` now also updates the single-axis widget's internal X/Y tracking to the parked/home position, so a subsequent single-axis scan holds the correct fixed-axis value instead of a stale one (previously only `current_position_um` and the axis control widget were updated).
- Startup/shutdown status notices (TimeTagger connected / server started, scanner/Z zeroed, controller released) now print to the terminal instead of raising napari alerts, and the DAQ axis validation no longer prints a second "initialized" line (only the galvo's single DAQ-connection message remains).
- The saved `.csv` header now labels rows/columns with the real scanned axes (e.g. `Z\X` for an XZ scan) instead of always "X"/"Y", and records the scan mode, galvo/piezo `µm/V` calibration, and the separate Z dwell time for Z-involving scans; units in the header are unified to `µm`.
- The saved `.tiff` ImageJ description now uses the real scanned-axis names (fast/slow) and scan mode instead of hardcoded "X"/"Y", and reports both the galvo and (when Z is scanned) piezo `µm/V` calibration. Pixel scale/resolution were already correct; only the descriptive labels were fixed.

### Removed
- Removed the coarse/fine auto-focus algorithm (`run_focus_sweep`) and the module-level `DEFAULT_COARSE_STEP` / `DEFAULT_FINE_STEP` / `DEFAULT_FINE_RANGE` from `widgets/auto_focus.py`; Z scanning is now a single linear sweep driven by Scan Parameters (`z_scan`).
- Removed `PIEZO_COARSE_STEP`, `PIEZO_FINE_STEP`, and `PIEZO_FINE_RANGE` from `utils.py` (superseded by editable Z scan parameters in the Scan Parameters panel).
- Removed the PyQt5 dependency: `requirements.txt` now pins `PySide6>=6.6.0` and the `napari[pyside6]` extra (set `QT_API=pyside6`).
- Cleaned up unused Qt imports in `odmr_gui_qt.py` (`QFrame`, `QSpacerItem`, `QSizePolicy`, `QFont`, `QPalette`, `QColor`) and `spectrometer_app.py` (`QPixmap`, `QImage`, `QFont`).
- Deleted `piezo_controller.py` (Thorlabs Kinesis `.NET`/USB path); no longer imported by the confocal app.
- Deleted `widgets/piezo_controls.py` (`PiezoControlWidget`); the manual Z control is superseded by the multi-axis `widgets/axis_controls.py` (`AxisControlWidget`).
- Dropped `pythonnet` and the Thorlabs Kinesis SDK install step from `requirements.txt` / README (piezo is initialized externally; this app only writes EXT IN voltage).

### Documentation
- Updated `README.md`, `widgets/README.md`, and architecture diagram for `daq_z_controller.py` and Z calibration constants (`Z_UM_PER_VOLT`, `Z_MAX_TRAVEL_UM`, `Z_VOLTAGE_RANGE`).
- Overhauled `README.md`: corrected calibration constants (`MICRONS_PER_VOLT = 24`, `MAX_ZOOM_LEVEL = 9`), default device IPs (Pulse Streamer `192.168.0.203`, RIGOL `192.168.0.223`), and spectrometer camera resolution (`6252x480`, not `1920x1080`).
- Documented the T1 relaxation measurement tab in `odmr_gui_qt.py`, previously missing from all documentation (ODMR/Rabi were documented, T1 was not).
- Expanded the repository overview with every supporting module (`data_manager.py`, `odmr_data_manager.py`, `galvo_controller.py`, `daq_z_controller.py`, `plot_scan_results.py`, `thread_safe_bridge.py`, `plot_widgets/`, `widgets/`) and added an architecture diagram.
- Rewrote `requirements.txt` to include all actual runtime dependencies (`napari`, `nidaqmx`, `pandas`, `tifffile`, `magicgui`, `qtpy`, `pyvisa`, `scipy`) with vendor-SDK installation notes.
- Corrected `PulseBlaster/README.md` and `PulseBlaster/README_ODMR_GUI.md`: fixed stale run instructions and example code referencing non-existent methods (`create_simple_laser_pulse`, `create_odmr_sequence`, `experiments.odmr()`), updated default IPs, documented the Rabi/T1 tabs, and clarified that Ramsey/spin-echo are not implemented.
- Corrected `widgets/README.md`: replaced references to the retired `napari_scanning_SPD.py` / `ConfigManager` with the current `confocal_main_control.py` entry point and real `ScanParametersManager` / `ScanPointsManager` / `ZoomLevelManager` classes; fixed widget factory signatures and documented `stop_scan`, `update_scan_parameters_widget`, and `create_camera_control_widget`.
- Corrected `README_Spectrometer.md` resolution and image-format claims to match `spectrometer_app.py`.
- Updated `README.md` and `widgets/README.md` for the manual `AxisControlWidget` (replacing the piezo control dock), single-axis plot click-to-move, the default Z dwell time (5 ms), and the actual `mmddyy###` data-folder naming.

### Removed
- Deleted unreferenced duplicate `TimeTagger/time_tags_test.1.ttbin` (the non-suffixed `time_tags_test.ttbin` is the one actually used as the virtual TimeTagger fallback).
- Deleted four leftover auto-exported `pulse_sequence_diagrams/Untitled diagram _ Mermaid Chart-*.{svg,mmd}` files not referenced by any documentation or code.
- Deleted `Camera/POA_Camera_Test.py` — vendor SDK walkthrough with a broken import path (`import pyPOACamera` instead of `from Camera import pyPOACamera`), fully superseded by `Camera/camera_video_mode.py`.
- Deleted `Camera/Video_Class_Test` — extensionless one-time integration test with a broken import path, superseded by production camera wrappers.
- Deleted `TimeTagger/FileWriterTT.py` — the `.ttbin` replay file it originally generated (`time_tags_test.ttbin`) already exists in the repo and serves as the virtual TimeTagger fallback; the recording script itself is no longer needed.
- Deleted `PulseBlaster/test_rigol_connection.py` — raw-socket RIGOL network diagnostic; the PyVISA-based `RigolDSG836Controller` in `rigol_dsg836.py` now handles connection diagnostics within the GUI.
- Retained `TimeTagger/CountRateLive.py` — useful standalone count-rate monitor that can verify the TimeTagger and SPD are working independently of the full confocal stack.

## [1.1.0] - 2024-03-21
### Added
- Comprehensive spectrometer application documentation in main README
- Detailed hardware requirements for spectrometer setup
- Quick start guide for spectrometer operation

### Changed
- Simplified UI by removing redundant plot profile functionality
- Improved performance by reducing widget overhead
- Updated main README structure to include three main applications
- Changed "both programs" to "all programs" in shared codebase description

### Documentation
- Integrated spectrometer documentation into main README
- Added spectrometer-specific hardware requirements
- Enhanced quick start guide with spectrometer section
- Updated repository overview to better reflect all components

## [1.0.0] - 2025-06-30
### Added
- Initial project-level `CHANGELOG.md`.
- Comprehensive `requirements.txt` listing all runtime Python dependencies (Napari, PyQt5, pandas, etc.).
- Draft packaging notes (optional vendor libraries such as `TimeTagger` and `PulseStreamer` clearly marked).
- Enhanced TIFF metadata for ImageJ/Fiji compatibility
  - Added comprehensive ImageJ-compatible metadata to saved TIFF files
  - Includes pixel scale information (microns per pixel)
  - Includes scan parameters (voltage ranges, resolution, dwell time)
  - Includes calibration constants and acquisition details
  - Automatic scale bar support when opening in ImageJ/Fiji
  - Added `tifffile` dependency for proper metadata handling

### Changed
- Replaced napari's default TIFF save with custom metadata-rich TIFF save function
- Enhanced TIFF files now contain full acquisition context for analysis

### Fixed
- Minor formatting issues encountered when building on Python 3.12.

### Deprecated
- None.

### Removed
- None.

### Security
- None.


[1.1.0]: https://github.com/NoeSilva13/Single_NV_Scannig_Microscopy/releases/tag/v1.1.0
[1.0.0]: https://github.com/NoeSilva13/Single_NV_Scannig_Microscopy/releases/tag/v1.0.0 