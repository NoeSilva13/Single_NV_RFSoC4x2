import importlib
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pandas as pd
import pytest


class _StubModule(ModuleType):
    """A module whose every attribute is a do-nothing class (Qt, OpenCV)."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        stub = type(name, (), {"__init__": lambda self, *a, **k: None})
        setattr(self, name, stub)
        return stub


def _import_spectrometer_app():
    """Import the app, stubbing Qt/OpenCV when they are not installed.

    Only the CSV helpers are exercised here, so a machine without the GUI
    stack (or without the camera) can still run these tests.
    """
    try:
        return importlib.import_module("spectrometer_app")
    except Exception:
        pass
    for name in [m for m in sys.modules if m == "spectrometer_app" or m.split(".")[0] == "Camera"]:
        del sys.modules[name]
    for name in ("qtpy", "qtpy.QtWidgets", "qtpy.QtCore", "pyqtgraph", "cv2"):
        sys.modules[name] = _StubModule(name)
    sys.modules["qtpy"].QtWidgets = sys.modules["qtpy.QtWidgets"]
    sys.modules["qtpy"].QtCore = sys.modules["qtpy.QtCore"]
    return importlib.import_module("spectrometer_app")


@pytest.fixture(scope="module")
def spectrometer_app():
    owned = ("spectrometer_app", "Camera", "qtpy", "pyqtgraph", "cv2")
    saved = {m: sys.modules[m] for m in sys.modules if m.split(".")[0] in owned}
    try:
        yield _import_spectrometer_app()
    finally:
        # Leave no stubs behind for other test modules.
        for name in [m for m in sys.modules if m.split(".")[0] in owned]:
            del sys.modules[name]
        sys.modules.update(saved)


def full_config(**overrides):
    config = {
        "measurement_time": "2026-11-23 14:30:12",
        "file": "112326001",
        "camera": {
            "model": "Cam",
            "serial_number": "SN123",
            "sensor": "IMX",
            "pixel_size_um": 3.76,
            "bit_depth": 12,
            "image_format": "POA_RAW8",
            "exposure_ms": 50.0,
            "gain": 300,
        },
        "resolution": {"width_px": 6252, "height_px": 480},
        "roi": {"start_x_px": 0, "width_px": 6252, "start_y_px": 10, "height_px": 20},
        "wavelength_calibration": {
            "enabled": True,
            "start_nm": 400.0,
            "end_nm": 800.0,
            "x_axis": "wavelength_nm",
        },
        "dark_correction": {
            "subtracted": True,
            "captured_at": "2026-11-23 14:28:40",
            "exposure_ms": 50.0,
            "gain": 300,
        },
        "spectra": {"count": 1, "recorded": False},
    }
    config.update(overrides)
    return config


def test_header_lists_every_section_as_comment_lines(spectrometer_app):
    lines = spectrometer_app.spectrum_csv_header_lines(full_config())
    text = "\n".join(lines)
    assert all(line.startswith("#") for line in lines)
    for expected in (
        "# Measurement Time: 2026-11-23 14:30:12",
        "# File: 112326001",
        "#   serial_number: SN123",
        "#   exposure_ms: 50",
        "#   width_px: 6252",
        "#   start_y_px: 10",
        "#   start_nm: 400",
        "#   x_axis: wavelength_nm",
        "#   subtracted: True",
        "#   dark_captured_at: 2026-11-23 14:28:40",
        "#   count: 1",
        "#   recorded: False",
    ):
        assert expected in text


def test_missing_information_is_written_as_unknown(spectrometer_app):
    lines = spectrometer_app.spectrum_csv_header_lines({})
    text = "\n".join(lines)
    assert "#   model: unknown" in text
    assert "#   width_px: unknown" in text
    assert "#   subtracted: False" in text
    assert "dark_captured_at" not in text


def test_recording_header_includes_when_it_started(spectrometer_app):
    config = full_config(
        spectra={"count": 3, "recorded": True, "recording_started": "2026-11-23 14:00:00"}
    )
    text = "\n".join(spectrometer_app.spectrum_csv_header_lines(config))
    assert "#   recording_started: 2026-11-23 14:00:00" in text


def save(app, path, spectra, config):
    window = SimpleNamespace(
        status_bar=SimpleNamespace(showMessage=lambda message: None)
    )
    assert app.SpectrometerMainWindow.save_spectra_to_file(
        window, str(path), spectra, config
    )


def test_single_spectrum_csv_keeps_its_table_under_the_header(
    tmp_path, spectrometer_app
):
    wavelengths = np.array([400.0, 500.0, 600.0])
    intensities = np.array([1.0, 2.0, 3.0])
    path = tmp_path / "112326001.csv"
    save(spectrometer_app, path, [(wavelengths, intensities)], full_config())

    assert path.read_text(encoding="utf-8").startswith("# Measurement Time:")
    table = pd.read_csv(path, comment="#")
    assert list(table.columns) == ["Wavelength", "Intensity"]
    np.testing.assert_allclose(table["Wavelength"], wavelengths)
    np.testing.assert_allclose(table["Intensity"], intensities)


def test_recorded_spectra_csv_keeps_one_column_per_spectrum(
    tmp_path, spectrometer_app
):
    wavelengths = np.array([400.0, 500.0])
    spectra = [(wavelengths, np.array([1.0, 2.0])), (wavelengths, np.array([3.0, 4.0]))]
    path = tmp_path / "112326002.csv"
    config = full_config(
        spectra={"count": 2, "recorded": True, "recording_started": "2026-11-23 14:00:00"}
    )
    config.pop("measurement_time")
    config.pop("file")
    save(spectrometer_app, path, spectra, config)

    text = path.read_text(encoding="utf-8")
    # Time and file name are filled in when the caller does not provide them.
    assert "# File: 112326002" in text
    table = pd.read_csv(path, comment="#")
    assert list(table.columns) == ["Wavelength", "Spectrum_1", "Spectrum_2"]
    np.testing.assert_allclose(table["Spectrum_2"], [3.0, 4.0])
