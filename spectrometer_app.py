#!/usr/bin/env python3
"""
Qt-based CCD Camera Spectrometer Application
Uses POA camera to capture horizontal line spectra and display wavelength vs intensity plots
"""

import math
import os
import sys
import time
import json
import numpy as np
from typing import Optional, Tuple
from qtpy.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QGridLayout, QLabel, QSlider, QPushButton, 
                            QSpinBox, QDoubleSpinBox, QComboBox, QGroupBox, QSplitter,
                            QCheckBox, QStatusBar, QMessageBox, QFileDialog, QSizePolicy)
from qtpy.QtCore import QTimer, Qt, QThread, Signal as pyqtSignal, QMutex
import pyqtgraph as pg
import cv2

# Import camera module
from Camera.camera_video_mode import POACameraController
from Camera import pyPOACamera
from common.utils import experiment_data_root


class CameraWorker(QThread):
    """Worker thread for camera operations"""
    frame_ready = pyqtSignal(np.ndarray)
    error_occurred = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self.camera = POACameraController()
        self.running = False
        self.mutex = QMutex()
        
    def initialize_camera(self, camera_index: int = 0) -> bool:
        """Initialize camera with 6252x480 resolution (spectrometer)"""
        try:
            # Connect with 6252x480 resolution for spectrometer
            success = self.camera.connect(camera_index, width=6252, height=480)
            if success:
                # Set image format to RAW8 for better performance
                self.camera.image_format = pyPOACamera.POAImgFormat.POA_RAW8
                return True
            else:
                self.error_occurred.emit("Failed to connect to camera")
                return False
        except Exception as e:
            self.error_occurred.emit(f"Camera initialization error: {str(e)}")
            return False
    
    def start_streaming(self):
        """Start camera streaming"""
        self.mutex.lock()
        self.running = True
        self.mutex.unlock()
        
        if self.camera.is_connected:
            if self.camera.start_stream():
                self.start()
            else:
                self.error_occurred.emit("Failed to start camera stream")
    
    def stop_streaming(self):
        """Stop camera streaming"""
        self.mutex.lock()
        self.running = False
        self.mutex.unlock()
        
        if self.camera.is_connected:
            self.camera.stop_stream()
        
        self.wait()
    
    def run(self):
        """Main camera acquisition loop"""
        while True:
            self.mutex.lock()
            should_run = self.running
            self.mutex.unlock()
            
            if not should_run:
                break
                
            if self.camera.is_connected and self.camera.is_streaming:
                frame = self.camera.get_frame()
                if frame is not None:
                    self.frame_ready.emit(frame)
                    
            self.msleep(33)  # ~30 FPS
    
    def set_exposure(self, exposure_us: int):
        """Set camera exposure time"""
        if self.camera.is_connected:
            self.camera.set_exposure(exposure_us)
    
    def set_gain(self, gain: int):
        """Set camera gain"""
        if self.camera.is_connected:
            self.camera.set_gain(gain)
    
    def set_resolution(self, width: int, height: int) -> Tuple[int, int]:
        """Change the camera resolution, safely pausing the acquisition thread.

        The QThread run-loop reads the camera buffer/dimensions, so it must be
        fully stopped before the image size changes and restarted afterwards.
        """
        was_streaming = self.running
        if was_streaming:
            self.stop_streaming()

        width, height = self.camera.set_resolution(width, height)

        if was_streaming:
            self.start_streaming()

        return width, height

    def get_camera_info(self) -> dict:
        """Get camera information"""
        if self.camera.is_connected:
            max_w, max_h = self.camera.get_max_resolution()
            return {
                'width': self.camera.img_width,
                'height': self.camera.img_height,
                'max_width': max_w,
                'max_height': max_h,
                'exposure': self.camera.get_exposure(),
                'gain': self.camera.get_gain(),
                'exposure_min_us': self.camera.exposure_min_us,
                'exposure_max_us': self.camera.exposure_max_us,
                'gain_min': self.camera.gain_min,
                'gain_max': self.camera.gain_max,
                'model': self.camera.camera_props.cameraModelName if self.camera.camera_props else 'Unknown'
            }
        return {}
    
    def cleanup(self):
        """Clean up camera resources"""
        self.stop_streaming()
        self.camera.disconnect()


class SpectrumProcessor:
    """Process camera frames to extract spectral data"""
    
    def __init__(self):
        # Linear λ calibration relative to current ROI X edges (None = use pixel indices)
        self.wavelength_start_nm = None
        self.wavelength_end_nm = None
        self.roi_start_y = 0
        self.roi_height = 480
        self.roi_start_x = 0
        self.roi_width = 6252
        self.dark_frame = None  # Full 2D grayscale frame for dark subtraction
        
    def set_roi(self, start_y: int, height: int, start_x: int = 0, width: int = 6252):
        """Set region of interest for spectrum extraction"""
        self.roi_start_y = start_y
        self.roi_height = height
        self.roi_start_x = start_x
        self.roi_width = width
    
    def set_wavelength_calibration(self, start_nm: float, end_nm: float):
        """Set linear wavelength calibration for ROI X edges (start → left, end → right)"""
        self.wavelength_start_nm = float(start_nm)
        self.wavelength_end_nm = float(end_nm)
    
    def clear_wavelength_calibration(self):
        """Clear wavelength calibration (spectrum X axis uses pixel indices)"""
        self.wavelength_start_nm = None
        self.wavelength_end_nm = None
    
    @staticmethod
    def _to_grayscale(frame: np.ndarray) -> np.ndarray:
        """Convert frame to 2D grayscale if needed"""
        if len(frame.shape) == 3:
            if frame.shape[2] == 3:
                return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            return frame[:, :, 0]
        return frame
    
    def set_dark_frame(self, frame: np.ndarray):
        """Store full frame for dark subtraction (ROI applied at process time)"""
        if frame is not None:
            self.dark_frame = self._to_grayscale(frame).astype(np.float64)
    
    def clear_dark_frame(self):
        """Clear stored dark frame"""
        self.dark_frame = None
    
    def process_frame(self, frame: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Extract spectrum from frame
        
        Returns:
            tuple: (wavelengths, intensities) or (pixels, intensities) if no calibration
        """
        frame = self._to_grayscale(frame)
        
        # Extract ROI with both x and y dimensions
        roi = frame[self.roi_start_y:self.roi_start_y + self.roi_height, 
                   self.roi_start_x:self.roi_start_x + self.roi_width]
        
        # Average vertically to get horizontal line spectrum
        spectrum = np.mean(roi, axis=0, dtype=np.float64)
        
        # Apply dark frame subtraction using current ROI
        if self.dark_frame is not None:
            dark_roi = self.dark_frame[self.roi_start_y:self.roi_start_y + self.roi_height,
                                       self.roi_start_x:self.roi_start_x + self.roi_width]
            spectrum = spectrum - np.mean(dark_roi, axis=0)
        
        # X axis: λ mapped to current ROI edges, or absolute pixel indices
        if self.wavelength_start_nm is not None and self.wavelength_end_nm is not None:
            x_axis = np.linspace(
                self.wavelength_start_nm, self.wavelength_end_nm, len(spectrum))
        else:
            x_axis = np.arange(self.roi_start_x, self.roi_start_x + len(spectrum))
        
        return x_axis, spectrum


class SpectrometerMainWindow(QMainWindow):
    """Main window for the spectrometer application"""

    # App-defined resolution presets as (label, width, height). A trailing
    # "Custom..." entry (width/height typed manually) is appended in setup_ui.
    # Number of steps of the logarithmic exposure slider
    EXPOSURE_SLIDER_STEPS = 1000

    RESOLUTION_PRESETS = [
        ("6252 x 480 (Spectrometer)", 6252, 480),
        ("6252 x 4176 (Full)", 6252, 4176),
        ("6252 x 240", 6252, 240),
        ("6252 x 1044", 6252, 1044),
        ("3072 x 480", 3072, 480),
    ]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CCD Camera Spectrometer")
        self.setGeometry(100, 100, 1400, 800)
        self.setStyleSheet("""
            QMainWindow {
                background-color: #262930;
                color: #ffffff;
            }
            QWidget {
                background-color: #262930;
                color: #ffffff;
            }
            QPushButton {
                background-color: #00d4aa;
                color: #262930;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 10pt;
            }
            QPushButton:hover {
                background-color: #00ffcc;
            }
            QPushButton:pressed {
                background-color: #009980;
            }
            QPushButton:disabled {
                background-color: #555555;
                color: #999999;
            }
            QGroupBox {
                color: #ffffff;
                border: 2px solid #555555;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
                color: #00d4aa;
            }
            QLabel {
                color: #ffffff;
            }
            QLineEdit {
                background-color: #3c3c3c;
                color: #ffffff;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 5px;
                font-size: 10pt;
            }
            QLineEdit:focus {
                border: 2px solid #00d4aa;
            }
            QTextEdit {
                background-color: #1e1e1e;
                color: #00ff00;
                border: 1px solid #555555;
                border-radius: 4px;
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 9pt;
            }
            QProgressBar {
                border: 1px solid #555555;
                border-radius: 4px;
                background-color: #3c3c3c;
                text-align: center;
                color: #ffffff;
            }
            QProgressBar::chunk {
                background-color: #00d4aa;
                border-radius: 3px;
            }
            QScrollArea {
                background-color: #262930;
                border: none;
            }
            QSplitter::handle {
                background-color: #555555;
            }
        """)
        # Initialize components
        self.camera_worker = CameraWorker()
        self.spectrum_processor = SpectrumProcessor()
        self.current_frame = None
        self.is_recording = False
        self.recorded_spectra = []

        # Current camera image dimensions (updated on connect / resolution change).
        self.img_width = 6252
        self.img_height = 480
        
        # Setup UI
        self.setup_ui()
        self.setup_connections()
        
        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready - Start camera, then use ROI button in camera view to select spectrum region")
        
        # Initialize camera
        self.initialize_camera()
    
    def setup_ui(self):
        """Setup the user interface"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        main_layout = QHBoxLayout(central_widget)
        
        # Create splitter for resizable panels
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)
        
        # Left panel - Camera view and controls
        left_widget = QWidget()
        # Cap the left panel width so it stays compact and the spectrum plot on
        # the right gets more room; the splitter can still be dragged.
        left_widget.setMaximumWidth(700)
        left_layout = QVBoxLayout(left_widget)
        
        # Camera view with performance optimizations
        self.camera_view = pg.ImageView()
        self.camera_view.setMinimumSize(420, 300)
        # Let the camera view grow when the window is maximized so the control
        # groups below keep their natural height (no empty gap, no inflated padding).
        self.camera_view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        
        # Optimize ImageView for better performance
        self.camera_view.ui.histogram.setEnabled(False)  # Disable histogram during resize
        self.camera_view.ui.roiBtn.setCheckable(True)    # Make ROI button toggle-able
        
        left_layout.addWidget(self.camera_view, 1)
        
        # Setup ROI button with simpler implementation
        self.setup_roi_button()
        
        # ROI controls (two pairs of label+spin per row to use the width)
        roi_group = QGroupBox("ROI Settings")
        roi_layout = QGridLayout(roi_group)
        roi_layout.setContentsMargins(12, 12, 12, 12)
        roi_layout.setSpacing(8)
        
        roi_layout.addWidget(QLabel("Start Y:"), 0, 0)
        self.roi_start_spinbox = QSpinBox()
        self.roi_start_spinbox.setRange(0, self.img_height)
        self.roi_start_spinbox.setValue(0)
        roi_layout.addWidget(self.roi_start_spinbox, 0, 1)
        
        roi_layout.addWidget(QLabel("Height:"), 0, 2)
        self.roi_height_spinbox = QSpinBox()
        self.roi_height_spinbox.setRange(1, self.img_height)
        self.roi_height_spinbox.setValue(self.img_height)
        roi_layout.addWidget(self.roi_height_spinbox, 0, 3)
        
        roi_layout.addWidget(QLabel("Start X:"), 1, 0)
        self.roi_start_x_spinbox = QSpinBox()
        self.roi_start_x_spinbox.setRange(0, self.img_width)
        self.roi_start_x_spinbox.setValue(0)
        roi_layout.addWidget(self.roi_start_x_spinbox, 1, 1)
        
        roi_layout.addWidget(QLabel("Width:"), 1, 2)
        self.roi_width_spinbox = QSpinBox()
        self.roi_width_spinbox.setRange(1, self.img_width)
        self.roi_width_spinbox.setValue(self.img_width)
        roi_layout.addWidget(self.roi_width_spinbox, 1, 3)
        
        # Add the ROI button after it's created
        if hasattr(self, 'apply_visual_roi_button'):
            roi_layout.addWidget(self.apply_visual_roi_button, 2, 0, 1, 4)
        
        # Camera controls
        camera_group = QGroupBox("Camera Controls")
        camera_layout = QGridLayout(camera_group)
        camera_layout.setContentsMargins(12, 12, 12, 12)
        camera_layout.setSpacing(8)
        
        # Exposure control
        camera_layout.addWidget(QLabel("Exposure:"), 0, 0)
        # Exposure range in ms (updated from the camera's real limits on connect).
        # The slider is logarithmic so the whole range (10 us - 2000 s) is usable;
        # the spin box allows typing an exact value.
        self.exposure_range_ms = (0.01, 2000000.0)
        self.exposure_ms = 50.0
        self.exposure_spinbox = QDoubleSpinBox()
        self.exposure_spinbox.setDecimals(3)
        self.exposure_spinbox.setRange(*self.exposure_range_ms)
        self.exposure_spinbox.setValue(self.exposure_ms)
        self.exposure_spinbox.setSuffix(" ms")
        self.exposure_spinbox.setKeyboardTracking(False)
        self.exposure_spinbox.setMinimumWidth(110)
        self.exposure_spinbox.setToolTip("Exposure time in milliseconds (type an exact value)")
        camera_layout.addWidget(self.exposure_spinbox, 0, 1)
        
        self.exposure_slider = QSlider(Qt.Orientation.Horizontal)
        self.exposure_slider.setRange(0, self.EXPOSURE_SLIDER_STEPS)
        self.exposure_slider.setValue(self._exposure_ms_to_slider(self.exposure_ms))
        self.exposure_slider.setToolTip("Exposure time (logarithmic scale)")
        camera_layout.addWidget(self.exposure_slider, 0, 2)
        
        # Gain control
        camera_layout.addWidget(QLabel("Gain:"), 1, 0)
        self.gain_label = QLabel("300")
        self.gain_label.setMinimumWidth(60)
        self.gain_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        camera_layout.addWidget(self.gain_label, 1, 1)
        
        self.gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.gain_slider.setRange(0, 1000)
        self.gain_slider.setValue(300)
        self.gain_slider.setToolTip("Camera gain (range is set from the camera on connect)")
        camera_layout.addWidget(self.gain_slider, 1, 2)
        
        # Control buttons
        button_layout = QHBoxLayout()
        self.start_button = QPushButton("Start Camera")
        self.stop_button = QPushButton("Stop Camera")
        self.stop_button.setEnabled(False)
        
        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.stop_button)
        camera_layout.addLayout(button_layout, 2, 0, 1, 3)
        
        # Resolution controls (preset dropdown + custom width/height)
        resolution_group = QGroupBox("Resolution")
        resolution_layout = QGridLayout(resolution_group)
        resolution_layout.setContentsMargins(12, 12, 12, 12)
        resolution_layout.setSpacing(8)
        
        resolution_layout.addWidget(QLabel("Preset:"), 0, 0)
        self.resolution_combo = QComboBox()
        for label, w, h in self.RESOLUTION_PRESETS:
            self.resolution_combo.addItem(label, (w, h))
        self.resolution_combo.addItem("Custom...", None)
        self.resolution_combo.setToolTip("Select a camera resolution preset, or 'Custom...' to type one")
        resolution_layout.addWidget(self.resolution_combo, 0, 1, 1, 3)
        
        resolution_layout.addWidget(QLabel("Width:"), 1, 0)
        self.res_width_spinbox = QSpinBox()
        self.res_width_spinbox.setRange(4, 6252)
        self.res_width_spinbox.setSingleStep(4)
        self.res_width_spinbox.setValue(6252)
        self.res_width_spinbox.setEnabled(False)
        self.res_width_spinbox.setToolTip("Custom width (multiple of 4, clamped to sensor max)")
        resolution_layout.addWidget(self.res_width_spinbox, 1, 1)
        
        resolution_layout.addWidget(QLabel("Height:"), 1, 2)
        self.res_height_spinbox = QSpinBox()
        self.res_height_spinbox.setRange(2, 4176)
        self.res_height_spinbox.setSingleStep(2)
        self.res_height_spinbox.setValue(480)
        self.res_height_spinbox.setEnabled(False)
        self.res_height_spinbox.setToolTip("Custom height (multiple of 2, clamped to sensor max)")
        resolution_layout.addWidget(self.res_height_spinbox, 1, 3)
        
        self.apply_resolution_button = QPushButton("Apply Resolution")
        self.apply_resolution_button.setToolTip("Change the camera resolution (resets ROI and calibration)")
        resolution_layout.addWidget(self.apply_resolution_button, 2, 0, 1, 4)
        
        # Calibration controls (wavelength + save/load of full setup)
        cal_group = QGroupBox("Calibration")
        cal_layout = QGridLayout(cal_group)
        cal_layout.setContentsMargins(12, 12, 12, 12)
        cal_layout.setSpacing(8)
        
        cal_layout.addWidget(QLabel("Start λ (nm):"), 0, 0)
        self.start_wavelength_spinbox = QDoubleSpinBox()
        self.start_wavelength_spinbox.setRange(200, 1000)
        self.start_wavelength_spinbox.setValue(400)
        self.start_wavelength_spinbox.setSuffix(" nm")
        self.start_wavelength_spinbox.setToolTip("Wavelength at the left edge of the ROI")
        cal_layout.addWidget(self.start_wavelength_spinbox, 0, 1)
        
        cal_layout.addWidget(QLabel("End λ (nm):"), 0, 2)
        self.end_wavelength_spinbox = QDoubleSpinBox()
        self.end_wavelength_spinbox.setRange(200, 1000)
        self.end_wavelength_spinbox.setValue(800)
        self.end_wavelength_spinbox.setSuffix(" nm")
        self.end_wavelength_spinbox.setToolTip("Wavelength at the right edge of the ROI")
        cal_layout.addWidget(self.end_wavelength_spinbox, 0, 3)
        
        self.apply_calibration_button = QPushButton("Apply Calibration")
        cal_layout.addWidget(self.apply_calibration_button, 1, 0, 1, 4)
        
        self.save_calibration_button = QPushButton("Save Calibration")
        self.save_calibration_button.setToolTip("Save ROI, camera controls, and wavelength range to a file")
        self.load_calibration_button = QPushButton("Load Calibration")
        self.load_calibration_button.setToolTip("Load ROI, camera controls, and wavelength range from a file")
        cal_layout.addWidget(self.save_calibration_button, 2, 0, 1, 2)
        cal_layout.addWidget(self.load_calibration_button, 2, 2, 1, 2)
        
        # Dark correction controls
        dark_group = QGroupBox("Dark Correction")
        dark_layout = QHBoxLayout(dark_group)
        dark_layout.setContentsMargins(12, 12, 12, 12)
        dark_layout.setSpacing(8)
        
        self.capture_dark_button = QPushButton("Capture Dark")
        self.capture_dark_button.setToolTip("Use current camera frame as dark (subtract from spectrum)")
        self.clear_dark_button = QPushButton("Clear Dark")
        self.clear_dark_button.setToolTip("Disable dark subtraction")
        self.clear_dark_button.setEnabled(False)
        dark_layout.addWidget(self.capture_dark_button)
        dark_layout.addWidget(self.clear_dark_button)
        
        # Arrange the control groups in a 2-column grid under the camera view so
        # they use the horizontal space instead of stacking into a tall column.
        # Row 0: geometry (ROI | Resolution); Row 1: acquisition | spectral
        # calibration; Row 2: dark correction spanning both columns.
        controls_grid = QGridLayout()
        controls_grid.setContentsMargins(0, 0, 0, 0)
        controls_grid.setHorizontalSpacing(10)
        controls_grid.setVerticalSpacing(8)
        controls_grid.addWidget(roi_group, 0, 0)
        controls_grid.addWidget(resolution_group, 0, 1)
        controls_grid.addWidget(camera_group, 1, 0, 1, 2)
        controls_grid.addWidget(cal_group, 2, 0, 1, 2)
        controls_grid.addWidget(dark_group, 3, 0, 1, 2)
        controls_grid.setColumnStretch(0, 1)
        controls_grid.setColumnStretch(1, 1)
        # Keep control groups at their natural height; only the camera view stretches.
        for group in (roi_group, resolution_group, camera_group, cal_group, dark_group):
            group.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        left_layout.addLayout(controls_grid)
        
        # Right panel - Spectrum display
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        
        # Spectrum plot
        self.spectrum_plot = pg.PlotWidget()
        self.spectrum_plot.setLabel('left', 'Intensity')
        self.spectrum_plot.setLabel('bottom', 'Wavelength (nm)')
        self.spectrum_plot.setTitle('Spectrum')
        self.spectrum_plot.showGrid(x=True, y=True)
        right_layout.addWidget(self.spectrum_plot)
        
        # Recording controls
        record_group = QGroupBox("Recording")
        record_layout = QHBoxLayout(record_group)
        record_layout.setContentsMargins(12, 12, 12, 12)
        record_layout.setSpacing(8)
        
        self.record_button = QPushButton("Start Recording")
        self.save_button = QPushButton("Save Spectrum")
        self.clear_button = QPushButton("Clear")
        
        record_layout.addWidget(self.record_button)
        record_layout.addWidget(self.save_button)
        record_layout.addWidget(self.clear_button)
        
        right_layout.addWidget(record_group)
        
        # Add panels to splitter
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([680, 720])
    
    def setup_connections(self):
        """Setup signal connections"""
        # Camera worker signals
        self.camera_worker.frame_ready.connect(self.update_frame)
        self.camera_worker.error_occurred.connect(self.handle_error)
        
        # Button connections
        self.start_button.clicked.connect(self.start_camera)
        self.stop_button.clicked.connect(self.stop_camera)
        self.resolution_combo.currentIndexChanged.connect(self._on_resolution_preset_changed)
        self.apply_resolution_button.clicked.connect(self.apply_resolution)
        self.apply_calibration_button.clicked.connect(self.apply_wavelength_calibration)
        self.save_calibration_button.clicked.connect(self.save_calibration)
        self.load_calibration_button.clicked.connect(self.load_calibration)
        self.capture_dark_button.clicked.connect(self.capture_dark)
        self.clear_dark_button.clicked.connect(self.clear_dark)
        
        # Control connections
        self.exposure_slider.valueChanged.connect(self.on_exposure_slider_changed)
        self.exposure_spinbox.valueChanged.connect(self.on_exposure_spinbox_changed)
        self.gain_slider.valueChanged.connect(self.update_gain)
        self.roi_start_spinbox.valueChanged.connect(self.update_roi)
        self.roi_height_spinbox.valueChanged.connect(self.update_roi)
        self.roi_start_x_spinbox.valueChanged.connect(self.update_roi)
        self.roi_width_spinbox.valueChanged.connect(self.update_roi)
        
        # Recording controls
        self.record_button.clicked.connect(self.toggle_recording)
        self.save_button.clicked.connect(self.save_spectrum)
        self.clear_button.clicked.connect(self.clear_spectrum)
    
    # ---- Exposure / gain helpers ----
    def _exposure_ms_to_slider(self, ms: float) -> int:
        """Map an exposure in ms to a position on the logarithmic slider."""
        lo, hi = self.exposure_range_ms
        ms = min(max(ms, lo), hi)
        frac = math.log(ms / lo) / math.log(hi / lo)
        return int(round(frac * self.EXPOSURE_SLIDER_STEPS))

    def _slider_to_exposure_ms(self, pos: int) -> float:
        """Map a logarithmic slider position to an exposure in ms."""
        lo, hi = self.exposure_range_ms
        frac = pos / self.EXPOSURE_SLIDER_STEPS
        return lo * (hi / lo) ** frac

    def _set_exposure_widgets(self, exposure_ms: float):
        """Show ``exposure_ms`` in the slider and spin box without sending it to the camera."""
        lo, hi = self.exposure_range_ms
        exposure_ms = min(max(exposure_ms, lo), hi)
        self.exposure_slider.blockSignals(True)
        self.exposure_spinbox.blockSignals(True)
        self.exposure_slider.setValue(self._exposure_ms_to_slider(exposure_ms))
        self.exposure_spinbox.setValue(exposure_ms)
        self.exposure_slider.blockSignals(False)
        self.exposure_spinbox.blockSignals(False)
        self.exposure_ms = exposure_ms

    def _apply_camera_limits(self, info: dict):
        """Set exposure/gain control ranges from the camera's real limits."""
        exp_min_us = info.get('exposure_min_us')
        exp_max_us = info.get('exposure_max_us')
        if exp_min_us and exp_max_us and exp_max_us > exp_min_us:
            self.exposure_range_ms = (exp_min_us / 1000.0, exp_max_us / 1000.0)
            self.exposure_spinbox.blockSignals(True)
            self.exposure_spinbox.setRange(*self.exposure_range_ms)
            self.exposure_spinbox.blockSignals(False)
            self._set_exposure_widgets(self.exposure_ms)

        gain_min = info.get('gain_min')
        gain_max = info.get('gain_max')
        if gain_min is not None and gain_max is not None and gain_max > gain_min:
            self.gain_slider.blockSignals(True)
            self.gain_slider.setRange(int(gain_min), int(gain_max))
            self.gain_slider.blockSignals(False)
            self.gain_slider.setToolTip(f"Camera gain ({int(gain_min)}-{int(gain_max)})")

    def sync_gui_with_camera_values(self):
        """Synchronize GUI controls with actual camera values"""
        try:
            # Wait a moment for camera to be ready
            time.sleep(0.1)

            info = self.camera_worker.get_camera_info()
            if info:
                # Ranges first, so the actual values fit inside the controls
                self._apply_camera_limits(info)

                # Get actual camera values
                actual_exposure_us = info.get('exposure', 50000)
                actual_gain = info.get('gain', 300)
                actual_exposure_ms = actual_exposure_us / 1000.0

                # Update GUI with actual values without re-triggering camera updates
                self._set_exposure_widgets(actual_exposure_ms)
                self.gain_slider.blockSignals(True)
                self.gain_slider.setValue(actual_gain)
                self.gain_slider.blockSignals(False)
                self.gain_label.setText(str(actual_gain))

                # Update status to show actual values
                self.status_bar.showMessage(
                    f"Camera ready - Exposure: {actual_exposure_ms:.3f} ms, Gain: {actual_gain}")

        except Exception as e:
            print(f"Error syncing GUI with camera values: {e}")

    def initialize_camera(self):
        """Initialize camera connection"""
        self.status_bar.showMessage("Initializing camera...")
        
        # Try to initialize camera
        if self.camera_worker.initialize_camera():
            info = self.camera_worker.get_camera_info()
            width = info.get('width', self.img_width)
            height = info.get('height', self.img_height)
            self.status_bar.showMessage(f"Camera ready: {info.get('model', 'Unknown')} - {width}x{height}")
            
            # Fix the custom-resolution spinbox maxima to the sensor limits.
            max_w = info.get('max_width', 0) or width
            max_h = info.get('max_height', 0) or height
            self.res_width_spinbox.setMaximum(max_w)
            self.res_height_spinbox.setMaximum(max_h)
            
            # Select the preset matching the current resolution (or Custom).
            self._select_resolution_in_combo(width, height)
            
            # Sync GUI with actual camera values
            self.sync_gui_with_camera_values()
            
            # Initialize all width/height-dependent UI (ROI ranges + calibration).
            self._update_dimensions(width, height)
        else:
            self.status_bar.showMessage("Failed to initialize camera")
    
    def start_camera(self):
        """Start camera streaming"""
        self.camera_worker.start_streaming()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        
        # Sync GUI with actual camera values after streaming starts
        self.sync_gui_with_camera_values()
        
        self.status_bar.showMessage("Camera streaming started")
    
    def stop_camera(self):
        """Stop camera streaming"""
        self.camera_worker.stop_streaming()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.status_bar.showMessage("Camera streaming stopped")
    
    def _on_resolution_preset_changed(self, index: int):
        """Enable custom width/height only for the 'Custom...' entry.

        For a concrete preset, mirror its dimensions into the spinboxes so the
        user can switch to Custom and tweak from there.
        """
        data = self.resolution_combo.itemData(index)
        is_custom = data is None
        self.res_width_spinbox.setEnabled(is_custom)
        self.res_height_spinbox.setEnabled(is_custom)
        if not is_custom:
            w, h = data
            self.res_width_spinbox.blockSignals(True)
            self.res_height_spinbox.blockSignals(True)
            self.res_width_spinbox.setValue(w)
            self.res_height_spinbox.setValue(h)
            self.res_width_spinbox.blockSignals(False)
            self.res_height_spinbox.blockSignals(False)

    def _select_resolution_in_combo(self, width: int, height: int):
        """Select the combo entry matching (width, height), else 'Custom...'."""
        self.resolution_combo.blockSignals(True)
        matched = -1
        for i in range(self.resolution_combo.count()):
            data = self.resolution_combo.itemData(i)
            if data is not None and tuple(data) == (width, height):
                matched = i
                break
        if matched >= 0:
            self.resolution_combo.setCurrentIndex(matched)
            self.res_width_spinbox.setEnabled(False)
            self.res_height_spinbox.setEnabled(False)
        else:
            # Custom... is the last item.
            self.resolution_combo.setCurrentIndex(self.resolution_combo.count() - 1)
            self.res_width_spinbox.setEnabled(True)
            self.res_height_spinbox.setEnabled(True)
        self.res_width_spinbox.setValue(width)
        self.res_height_spinbox.setValue(height)
        self.resolution_combo.blockSignals(False)

    def apply_resolution(self):
        """Change the camera resolution from the preset/custom selection."""
        if not self.camera_worker.camera.is_connected:
            self.status_bar.showMessage("Cannot change resolution: camera not connected")
            return

        data = self.resolution_combo.currentData()
        if data is None:
            width = self.res_width_spinbox.value()
            height = self.res_height_spinbox.value()
        else:
            width, height = data

        was_streaming = self.camera_worker.running
        actual_w, actual_h = self.camera_worker.set_resolution(width, height)

        # Keep Start/Stop button states consistent with the stream state.
        self.start_button.setEnabled(not self.camera_worker.running)
        self.stop_button.setEnabled(self.camera_worker.running)

        self._update_dimensions(actual_w, actual_h)
        self.status_bar.showMessage(
            f"Resolution set to {actual_w}x{actual_h} - ROI and calibration reset")

    def _update_dimensions(self, width: int, height: int):
        """Sync all width/height-dependent UI to the current camera image size.

        Resets the ROI to the full frame and re-applies the wavelength
        calibration over the new frame (both are reset on a resolution change).
        """
        self.img_width = int(width)
        self.img_height = int(height)

        # Update ROI spinbox ranges to the new frame, then reset ROI to full.
        for spinbox in (self.roi_start_spinbox, self.roi_height_spinbox,
                        self.roi_start_x_spinbox, self.roi_width_spinbox):
            spinbox.blockSignals(True)
        self.roi_start_spinbox.setRange(0, self.img_height)
        self.roi_height_spinbox.setRange(1, self.img_height)
        self.roi_start_x_spinbox.setRange(0, self.img_width)
        self.roi_width_spinbox.setRange(1, self.img_width)
        self.roi_start_spinbox.setValue(0)
        self.roi_height_spinbox.setValue(self.img_height)
        self.roi_start_x_spinbox.setValue(0)
        self.roi_width_spinbox.setValue(self.img_width)
        for spinbox in (self.roi_start_spinbox, self.roi_height_spinbox,
                        self.roi_start_x_spinbox, self.roi_width_spinbox):
            spinbox.blockSignals(False)

        # Push the full-frame ROI to the spectrum processor.
        self.update_roi()

        # Reset the wavelength calibration to defaults over the new full frame.
        self.start_wavelength_spinbox.setValue(400)
        self.end_wavelength_spinbox.setValue(800)
        self.apply_wavelength_calibration()

    def update_frame(self, frame: np.ndarray):
        """Update camera view and process spectrum"""
        self.current_frame = frame
        
        # Display frame in camera view
        self.camera_view.setImage(frame.T)
        
        # Process spectrum
        wavelengths, intensities = self.spectrum_processor.process_frame(frame)
        
        # Update spectrum plot
        self.spectrum_plot.clear()
        self.spectrum_plot.plot(wavelengths, intensities, pen='#00d4aa', width=2)
        
        # If recording, save spectrum
        if self.is_recording:
            self.recorded_spectra.append((wavelengths.copy(), intensities.copy()))
    
    def handle_error(self, error_msg: str):
        """Handle camera errors"""
        self.status_bar.showMessage(f"Error: {error_msg}")
        QMessageBox.critical(self, "Camera Error", error_msg)
    
    def on_exposure_slider_changed(self, pos: int):
        """Exposure slider moved (logarithmic scale)"""
        exposure_ms = self._slider_to_exposure_ms(pos)
        self._set_exposure_widgets(exposure_ms)
        self.camera_worker.set_exposure(int(round(exposure_ms * 1000)))

    def on_exposure_spinbox_changed(self, exposure_ms: float):
        """Exposure typed in the spin box (ms)"""
        self._set_exposure_widgets(exposure_ms)
        self.camera_worker.set_exposure(int(round(exposure_ms * 1000)))

    def update_gain(self, value: int):
        """Update camera gain from slider"""
        self.gain_label.setText(str(value))
        self.camera_worker.set_gain(value)
    

    
    def apply_wavelength_calibration(self):
        """Apply linear wavelength calibration to current ROI X edges"""
        start_wl = self.start_wavelength_spinbox.value()
        end_wl = self.end_wavelength_spinbox.value()
        
        self.spectrum_processor.set_wavelength_calibration(start_wl, end_wl)
        
        self.spectrum_plot.setLabel('bottom', 'Wavelength (nm)')
        self.status_bar.showMessage(
            f"ROI wavelength calibration applied: {start_wl}-{end_wl} nm "
            f"(ROI width={self.spectrum_processor.roi_width} px)"
        )
    
    def _get_calibration_dict(self) -> dict:
        """Collect current ROI, camera, and wavelength settings"""
        return {
            "version": 1,
            "resolution": {
                "width": self.img_width,
                "height": self.img_height,
            },
            "roi": {
                "start_y": self.roi_start_spinbox.value(),
                "height": self.roi_height_spinbox.value(),
                "start_x": self.roi_start_x_spinbox.value(),
                "width": self.roi_width_spinbox.value(),
            },
            "camera": {
                "exposure_ms": self.exposure_ms,
                "gain": self.gain_slider.value(),
            },
            "wavelength": {
                "start_nm": self.start_wavelength_spinbox.value(),
                "end_nm": self.end_wavelength_spinbox.value(),
            },
        }
    
    def save_calibration(self):
        """Save ROI, camera controls, and wavelength range to JSON"""
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save Calibration", "", "Calibration Files (*.json);;All Files (*)")
        if not filename:
            return
        if not filename.lower().endswith(".json"):
            filename += ".json"
        
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(self._get_calibration_dict(), f, indent=2)
            self.status_bar.showMessage(f"Calibration saved to {filename}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to save calibration: {str(e)}")
    
    def load_calibration(self):
        """Load ROI, camera controls, and wavelength range from JSON"""
        filename, _ = QFileDialog.getOpenFileName(
            self, "Load Calibration", "", "Calibration Files (*.json);;All Files (*)")
        if not filename:
            return
        
        try:
            with open(filename, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            resolution = data.get("resolution", {})
            roi = data.get("roi", {})
            camera = data.get("camera", {})
            wavelength = data.get("wavelength", {})
            
            # Resolution first: changing it resets the ROI to the full frame, so
            # the saved ROI below is applied on top of the correct dimensions.
            if "width" in resolution and "height" in resolution:
                target_w, target_h = int(resolution["width"]), int(resolution["height"])
                if (target_w, target_h) != (self.img_width, self.img_height):
                    if self.camera_worker.camera.is_connected:
                        actual_w, actual_h = self.camera_worker.set_resolution(target_w, target_h)
                        self.start_button.setEnabled(not self.camera_worker.running)
                        self.stop_button.setEnabled(self.camera_worker.running)
                        self._update_dimensions(actual_w, actual_h)
                        self._select_resolution_in_combo(actual_w, actual_h)
            
            # ROI (signals update spectrum processor)
            if "start_y" in roi:
                self.roi_start_spinbox.setValue(int(roi["start_y"]))
            if "height" in roi:
                self.roi_height_spinbox.setValue(int(roi["height"]))
            if "start_x" in roi:
                self.roi_start_x_spinbox.setValue(int(roi["start_x"]))
            if "width" in roi:
                self.roi_width_spinbox.setValue(int(roi["width"]))
            
            # Camera controls
            if "exposure_ms" in camera:
                exposure_ms = float(camera["exposure_ms"])
                # Spin box signal applies it to the camera (clamped to its range)
                self.exposure_spinbox.setValue(exposure_ms)
            if "gain" in camera:
                self.gain_slider.setValue(int(camera["gain"]))
            
            # Wavelength range + apply
            if "start_nm" in wavelength:
                self.start_wavelength_spinbox.setValue(float(wavelength["start_nm"]))
            if "end_nm" in wavelength:
                self.end_wavelength_spinbox.setValue(float(wavelength["end_nm"]))
            self.apply_wavelength_calibration()
            
            self.status_bar.showMessage(f"Calibration loaded from {filename}")
        except Exception as e:
            QMessageBox.critical(self, "Load Error", f"Failed to load calibration: {str(e)}")
    
    def capture_dark(self):
        """Capture current frame as dark for subtraction"""
        if self.current_frame is None:
            QMessageBox.warning(self, "Dark Capture", "No camera frame available. Start the camera first.")
            return
        
        self.spectrum_processor.set_dark_frame(self.current_frame)
        self.clear_dark_button.setEnabled(True)
        self.status_bar.showMessage("Dark captured — subtraction enabled")
    
    def clear_dark(self):
        """Clear dark frame and disable subtraction"""
        self.spectrum_processor.clear_dark_frame()
        self.clear_dark_button.setEnabled(False)
        self.status_bar.showMessage("Dark cleared — subtraction disabled")
    
    def toggle_recording(self):
        """Toggle spectrum recording"""
        if self.is_recording:
            self.is_recording = False
            self.record_button.setText("Start Recording")
            self.status_bar.showMessage(f"Recording stopped. {len(self.recorded_spectra)} spectra recorded")
        else:
            self.is_recording = True
            self.recorded_spectra = []
            self.record_button.setText("Stop Recording")
            self.status_bar.showMessage("Recording started")
    
    @staticmethod
    def _next_spectrum_base_path() -> str:
        """Return the next sequential base path (no extension) for a spectrum.

        Files go to ``data/<mmddyy>/Spectra/<mmddyy>###`` (same naming scheme as
        the confocal scans). The counter is shared by the .csv and .png outputs.
        """
        day = time.strftime("%m%d%y")
        folder = os.path.join(experiment_data_root(), day, "Spectra")
        os.makedirs(folder, exist_ok=True)
        bases = set()
        for f in os.listdir(folder):
            if f.startswith(day) and (f.endswith('.csv') or f.endswith('.png')):
                bases.add(os.path.splitext(f)[0])
        return os.path.join(folder, f"{day}{len(bases) + 1:03d}")

    def save_spectrum(self):
        """Auto-save current or recorded spectra (CSV + PNG) in data/<day>/Spectra"""
        if self.recorded_spectra:
            spectra = list(self.recorded_spectra)
        elif self.current_frame is not None:
            wavelengths, intensities = self.spectrum_processor.process_frame(self.current_frame)
            spectra = [(wavelengths, intensities)]
        else:
            self.status_bar.showMessage("No spectrum to save")
            return

        try:
            base = self._next_spectrum_base_path()
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to create data folder: {str(e)}")
            return

        csv_path = base + ".csv"
        png_path = base + ".png"
        if self.save_spectra_to_file(csv_path, spectra):
            self.save_spectrum_image(png_path, spectra)
            self.status_bar.showMessage(f"Spectrum saved: {csv_path} (+ .png)")

    def save_spectrum_image(self, filename: str, spectra: list) -> bool:
        """Save a PNG plot of the spectrum (or recorded spectra) to ``filename``"""
        try:
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_agg import FigureCanvasAgg

            fig = Figure(figsize=(8, 5), dpi=150)
            FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
            if len(spectra) == 1:
                w, y = spectra[0]
                ax.plot(w, y, color='#00a080', linewidth=1.2)
                ax.set_title("Spectrum")
            else:
                for w, y in spectra:
                    ax.plot(w, y, color='gray', alpha=0.25, linewidth=0.6)
                mean_y = np.mean([y for _, y in spectra], axis=0)
                ax.plot(spectra[0][0], mean_y, color='#00a080', linewidth=1.5, label='Mean')
                ax.legend()
                ax.set_title(f"Spectra ({len(spectra)} recorded)")
            ax.set_xlabel("Wavelength (nm)")
            ax.set_ylabel("Intensity")
            ax.grid(True, alpha=0.3)
            fig.tight_layout()
            fig.savefig(filename)
            return True
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to save image: {str(e)}")
            return False

    def save_spectra_to_file(self, filename: str, spectra: list) -> bool:
        """Save spectra to CSV file. Returns True on success."""
        try:
            with open(filename, 'w') as f:
                if len(spectra) == 1:
                    # Single spectrum
                    wavelengths, intensities = spectra[0]
                    f.write("Wavelength,Intensity\n")
                    for w, i in zip(wavelengths, intensities):
                        f.write(f"{w:.2f},{i:.6f}\n")
                else:
                    # Multiple spectra
                    f.write("Wavelength," + ",".join([f"Spectrum_{i+1}" for i in range(len(spectra))]) + "\n")
                    wavelengths = spectra[0][0]  # Assume all have same wavelength axis
                    for j, w in enumerate(wavelengths):
                        line = f"{w:.2f}"
                        for wavelengths_i, intensities_i in spectra:
                            line += f",{intensities_i[j]:.6f}"
                        f.write(line + "\n")
            
            self.status_bar.showMessage(f"Spectra saved to {filename}")
            return True
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to save file: {str(e)}")
            return False
    
    def clear_spectrum(self):
        """Clear spectrum plot"""
        self.spectrum_plot.clear()
        self.recorded_spectra = []
        self.status_bar.showMessage("Spectrum cleared")
    
    def setup_roi_button(self):
        """Setup ROI button with simplified functionality"""
        try:
            # Enable ROI functionality but don't auto-sync
            self.camera_view.ui.roiBtn.setEnabled(True)
            self.camera_view.ui.roiBtn.setToolTip("Click to enable visual ROI selection on camera image")
            
            # Connect ROI button to our initialization
            self.camera_view.ui.roiBtn.clicked.connect(self._handle_roi_button_click)
            
            # Create the apply button
            self.apply_visual_roi_button = QPushButton("Apply Visual ROI")
            self.apply_visual_roi_button.setToolTip("Apply the visual ROI selection to spectrum processing")
            self.apply_visual_roi_button.clicked.connect(self.apply_visual_roi)
            

            
        except Exception as e:
            print(f"ROI setup error: {e}")
            # Create disabled button as fallback
            self.apply_visual_roi_button = QPushButton("Visual ROI Unavailable")
            self.apply_visual_roi_button.setEnabled(False)
            self.status_bar.showMessage("ROI setup failed - using manual controls only")
    
    def initialize_visual_roi(self):
        """Initialize the visual ROI with proper size and position"""
        try:
            # Pause camera during ROI operations for better performance
            self._pause_camera_for_roi()
            
            # Give PyQtGraph time to create the ROI
            from qtpy.QtCore import QTimer
            QTimer.singleShot(100, self._setup_roi_rectangle)
            
        except Exception as e:
            print(f"Initialize visual ROI error: {e}")
            self.status_bar.showMessage("Error initializing visual ROI")
    
    def _setup_roi_rectangle(self):
        """Setup the ROI rectangle with proper dimensions and optimizations"""
        try:
            roi_item = self.camera_view.roi
            if roi_item is not None:
                # Disable real-time processing during setup to improve performance
                roi_item.setAcceptedMouseButtons(pg.QtCore.Qt.LeftButton)
                
                # Get current ROI settings or use defaults
                start_x = self.roi_start_x_spinbox.value()
                start_y = self.roi_start_spinbox.value()
                width = self.roi_width_spinbox.value()
                height = self.roi_height_spinbox.value()
                
                # Get image dimensions for proper sizing
                if self.current_frame is not None:
                    if len(self.current_frame.shape) == 3:
                        img_height, img_width = self.current_frame.shape[:2]
                    else:
                        img_height, img_width = self.current_frame.shape
                else:
                    # Use the current camera dimensions if no frame is available
                    img_width, img_height = self.img_width, self.img_height
                
                # Set ROI size from spinbox values
                roi_width = width
                roi_height = height
                
                # Position the ROI from spinbox values
                roi_x = start_x
                roi_y = start_y
                
                # Optimize ROI for better performance
                self._optimize_roi_performance(roi_item)
                
                # Set the ROI rectangle
                roi_item.setPos([roi_x, roi_y])
                roi_item.setSize([roi_width, roi_height])
                
                # Make sure it's visible
                roi_item.show()
                
                self.status_bar.showMessage(f"Visual ROI initialized: drag to move, resize with corners to select X/Y region. Camera paused for performance.")
                
            else:
                self.status_bar.showMessage("ROI not available yet - please try again")
                # Resume camera if ROI setup failed
                self._resume_camera_from_roi()
                
        except Exception as e:
            print(f"Setup ROI rectangle error: {e}")
            self.status_bar.showMessage("Error setting up ROI rectangle")
            # Resume camera on error
            self._resume_camera_from_roi()
    
    def _optimize_roi_performance(self, roi_item):
        """Optimize ROI for better performance during resize operations"""
        try:
            # Disable some computationally expensive features
            if hasattr(roi_item, 'setAcceptedMouseButtons'):
                roi_item.setAcceptedMouseButtons(pg.QtCore.Qt.LeftButton)
            
            # Connect signals for better performance management
            if hasattr(roi_item, 'sigRegionChanged'):
                roi_item.sigRegionChanged.connect(self._on_roi_changing)
            
            if hasattr(roi_item, 'sigRegionChangeFinished'):
                # Only update when resize is finished, not during
                roi_item.sigRegionChangeFinished.connect(self._on_roi_resize_finished)
            
            # Set reasonable bounds to prevent extreme resizing (current frame)
            if hasattr(roi_item, 'maxBounds'):
                roi_item.maxBounds = pg.QtCore.QRectF(0, 0, self.img_width, self.img_height)
            
            # Disable real-time ROI statistics computation
            if hasattr(roi_item, 'setVisible'):
                roi_item.setVisible(True)
            
        except Exception as e:
            print(f"ROI optimization error: {e}")
    
    def _on_roi_changing(self):
        """Handle ROI changes during resize (temporary performance optimization)"""
        try:
            # Temporarily reduce camera update frequency during ROI resize
            if hasattr(self, '_roi_resize_active'):
                return
                
            self._roi_resize_active = True
            self.status_bar.showMessage("Resizing ROI... (release mouse to finish)")
            
            # Use a timer to detect when resize is done
            if hasattr(self, '_roi_resize_timer'):
                self._roi_resize_timer.stop()
            
            from qtpy.QtCore import QTimer
            self._roi_resize_timer = QTimer()
            self._roi_resize_timer.setSingleShot(True)
            self._roi_resize_timer.timeout.connect(self._on_roi_resize_timeout)
            self._roi_resize_timer.start(500)  # 500ms delay
            
        except Exception as e:
            print(f"ROI changing error: {e}")
    
    def _on_roi_resize_timeout(self):
        """Handle ROI resize timeout"""
        try:
            self._roi_resize_active = False
            self.status_bar.showMessage("ROI resize completed - click 'Apply Visual ROI' to use")
        except Exception as e:
            print(f"ROI resize timeout error: {e}")
    
    def _on_roi_resize_finished(self):
        """Handle ROI resize completion"""
        try:
            # Update status when resize is complete
            self.status_bar.showMessage("ROI resize completed - click 'Apply Visual ROI' to use")
        except Exception as e:
            print(f"ROI resize finished error: {e}")
    
    def _pause_camera_for_roi(self):
        """Pause camera during ROI operations for better performance"""
        try:
            # Avoid double-pausing - check if flags exist AND are True
            if (hasattr(self, '_camera_was_streaming') and 
                hasattr(self, '_roi_pause_active') and 
                self._camera_was_streaming and 
                self._roi_pause_active):
                return  # Already paused
                
            # Check if camera worker thread is running (same approach as stop_camera button)
            is_camera_running = (self.camera_worker.isRunning() and 
                               self.camera_worker.camera.is_connected and 
                               hasattr(self, 'current_frame') and 
                               self.current_frame is not None)
            
            # Remember current state
            self._camera_was_streaming = is_camera_running
            self._roi_pause_active = True
            
            if self._camera_was_streaming:
                # Use exact same approach as stop_camera button
                self.camera_worker.stop_streaming()
                self.status_bar.showMessage("Camera paused for ROI operations...")
                
                # Update button states (same as stop_camera)
                self.start_button.setEnabled(True)
                self.stop_button.setEnabled(False)
            else:
                self.status_bar.showMessage("Camera already stopped - ROI ready")
                
        except Exception as e:
            print(f"Pause camera error: {e}")
            self._camera_was_streaming = False
            self._roi_pause_active = False
    
    def _resume_camera_from_roi(self):
        """Resume camera after ROI operations are complete"""
        try:
            # Only resume if camera was streaming before we paused it
            if (hasattr(self, '_camera_was_streaming') and 
                hasattr(self, '_roi_pause_active') and 
                self._camera_was_streaming and 
                self._roi_pause_active):
                
                if self.camera_worker.camera.is_connected:
                    # Use exact same approach as start_camera button
                    self.camera_worker.start_streaming()
                    self.status_bar.showMessage("Camera resumed after ROI operations")
                    
                    # Update button states (same as start_camera)
                    self.start_button.setEnabled(False)
                    self.stop_button.setEnabled(True)
                else:
                    self.status_bar.showMessage("Camera not connected - cannot resume")
                
            else:
                self.status_bar.showMessage("Camera state unchanged")
                
            # Reset the flags
            self._camera_was_streaming = False
            self._roi_pause_active = False
                
        except Exception as e:
            print(f"Resume camera error: {e}")
            self._camera_was_streaming = False
            self._roi_pause_active = False
    
    def _handle_roi_button_click(self):
        """Handle ROI button clicks (both enable and disable)"""
        try:
            # Check if ROI is being enabled or disabled
            if self.camera_view.ui.roiBtn.isChecked():
                # ROI is being enabled - initialize it
                self.initialize_visual_roi()
            else:
                # ROI is being disabled - resume camera
                self._resume_camera_from_roi()
                self.status_bar.showMessage("ROI disabled - camera resumed")
                
        except Exception as e:
            print(f"Handle ROI button click error: {e}")
            self.status_bar.showMessage("Error handling ROI button")
            # Try to resume camera on error
            self._resume_camera_from_roi()
    


    
    def apply_visual_roi(self):
        """Apply the current visual ROI selection to our spectrum processing"""
        try:
            roi_item = self.camera_view.roi
            if roi_item is not None and self.current_frame is not None:
                # Get ROI position and size
                pos = roi_item.pos()
                size = roi_item.size()
                
                # Extract X and Y position and size (roi uses [x, y] format)
                start_x = int(pos[0])
                start_y = int(pos[1])
                width = int(size[0])
                height = int(size[1])
                
                # Clamp values to valid ranges (current camera frame)
                start_x = max(0, min(start_x, self.img_width - 1))
                start_y = max(0, min(start_y, self.img_height - 1))
                width = max(1, min(width, self.img_width - start_x))
                height = max(1, min(height, self.img_height - start_y))
                
                # Update our controls
                self.roi_start_spinbox.setValue(start_y)
                self.roi_height_spinbox.setValue(height)
                self.roi_start_x_spinbox.setValue(start_x)
                self.roi_width_spinbox.setValue(width)
                
                # Update spectrum processor
                self.spectrum_processor.set_roi(start_y, height, start_x, width)
                
                # Close ROI view by unchecking the ROI button (temporarily disconnect to avoid double-resume)
                self.camera_view.ui.roiBtn.clicked.disconnect(self._handle_roi_button_click)
                self.camera_view.ui.roiBtn.setChecked(False)
                self.camera_view.ui.roiBtn.clicked.connect(self._handle_roi_button_click)
                
                # Resume camera after applying ROI
                self._resume_camera_from_roi()
                
                self.status_bar.showMessage(f"Visual ROI applied: X={start_x}, Y={start_y}, Width={width}, Height={height}. ROI closed, camera resumed.")
            else:
                self.status_bar.showMessage("No visual ROI active or no camera frame available")
                # Close ROI view and resume camera even if ROI application failed
                self.camera_view.ui.roiBtn.clicked.disconnect(self._handle_roi_button_click)
                self.camera_view.ui.roiBtn.setChecked(False)
                self.camera_view.ui.roiBtn.clicked.connect(self._handle_roi_button_click)
                self._resume_camera_from_roi()
        except Exception as e:
            print(f"Apply visual ROI error: {e}")
            self.status_bar.showMessage("Error applying visual ROI - using manual controls")
            # Close ROI view and resume camera on error
            self.camera_view.ui.roiBtn.clicked.disconnect(self._handle_roi_button_click)
            self.camera_view.ui.roiBtn.setChecked(False)
            self.camera_view.ui.roiBtn.clicked.connect(self._handle_roi_button_click)
            self._resume_camera_from_roi()
    

    def update_roi(self):
        """Update ROI settings"""
        start_y = self.roi_start_spinbox.value()
        height = self.roi_height_spinbox.value()
        start_x = self.roi_start_x_spinbox.value()
        width = self.roi_width_spinbox.value()
        self.spectrum_processor.set_roi(start_y, height, start_x, width)
    
    def closeEvent(self, event):
        """Handle application close"""
        self.camera_worker.cleanup()
        event.accept()


def main():
    """Main application entry point"""
    app = QApplication(sys.argv)
    # Note: Main styling is now applied in the SpectrometerMainWindow class
    # This ensures consistent styling across all components
    
    window = SpectrometerMainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main() 