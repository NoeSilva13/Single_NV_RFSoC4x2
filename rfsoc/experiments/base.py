"""Shared configuration and result types for native RFSoC experiments."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import curve_fit
from scipy.signal import find_peaks


@dataclass
class ExperimentResult:
    kind: str
    x_name: str
    x_unit: str
    x: np.ndarray
    signal_counts: np.ndarray
    reference_counts: np.ndarray
    signal_rate_cps: np.ndarray
    reference_rate_cps: np.ndarray
    contrast: np.ndarray
    requested: dict[str, Any] = field(default_factory=dict)
    executed: dict[str, Any] = field(default_factory=dict)
    fit: dict[str, float] | None = None
    saved_files: dict[str, str] = field(default_factory=dict)


@dataclass
class CountingResult:
    """One counted point rather than a sweep: PL intensity or dark counts."""

    kind: str
    counts: int
    window_seconds: float
    reps: int
    requested: dict[str, Any] = field(default_factory=dict)
    executed: dict[str, Any] = field(default_factory=dict)

    @property
    def rate_cps(self):
        return self.counts / (self.window_seconds * self.reps)


_SKIP = object()


def _plain(value):
    """A JSON-friendly copy of a config value, or ``_SKIP`` when it is not one."""
    if isinstance(value, np.ndarray):
        return _plain(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (list, tuple)):
        plain = []
        for item in value:
            converted = _plain(item)
            if converted is _SKIP:
                return _SKIP
            plain.append(converted)
        return plain
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return _SKIP


def _config_names(cfg):
    keys = getattr(cfg, "keys", None)
    if callable(keys):
        try:
            return list(keys())
        except TypeError:
            pass
    return [name for name in vars(cfg) if not str(name).startswith("_")]


def configuration_record(cfg):
    """Copy an NVConfiguration in the names qickdawg already uses.

    Unit assignment fills the sibling suffixes (``_tns`` / ``_tus`` / ``_treg``,
    and the frequency and fine-time equivalents).  Those converted values are
    what the program runs, so the record keeps them instead of a renamed set.
    Objects such as ``soccfg`` are left out: the CSV and NPZ store numbers.
    """
    record = {}
    for name in _config_names(cfg):
        if str(name).startswith("_"):
            continue
        try:
            value = cfg[name]
        except (KeyError, TypeError, AttributeError):
            value = getattr(cfg, name, _SKIP)
        plain = _plain(value)
        if plain is _SKIP:
            continue
        record[str(name)] = plain
    return record


def executed_configuration(cfg, **axis):
    """The configuration record plus the axis ``acquire()`` actually returned."""
    executed = configuration_record(cfg)
    for name, value in axis.items():
        plain = _plain(value)
        executed[name] = np.asarray(value).tolist() if plain is _SKIP else plain
    return executed


def fit_curve(model, x, y, p0, bounds=None, maxfev=20_000):
    """Fit *model*, reporting ``(None, None)`` instead of raising.

    A fit is a convenience laid over the measurement: a sweep that is short,
    flat or full of noise still has to be saved and plotted, so a fit that does
    not converge is an absent number rather than a failed acquisition.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size <= len(p0) or not np.all(np.isfinite(y)) or np.ptp(y) == 0:
        return None, None
    kwargs = {"maxfev": maxfev}
    if bounds is not None:
        kwargs["bounds"] = bounds
    try:
        popt, pcov = curve_fit(model, x, y, p0=p0, **kwargs)
    except (RuntimeError, ValueError, TypeError):
        return None, None
    return popt, np.sqrt(np.diag(pcov))


def oscillation_spectrum(x_ns, y):
    """Amplitude spectrum of a trace sampled in nanoseconds, and its peaks.

    Returns the frequency axis in hertz, the spectrum, and the peak frequencies
    ordered by height.  A decaying-cosine fit only converges when it starts near
    the right frequency, and this is where that first guess comes from.  The
    window is Hanning, as in the QICK-DAWG demo, so a trace that does not end on
    a whole period does not smear across the spectrum.
    """
    x_ns = np.asarray(x_ns, dtype=float)
    y = np.asarray(y, dtype=float)
    steps = np.diff(x_ns)
    if y.size < 4 or steps.size == 0 or not np.all(np.isfinite(y)):
        return np.zeros(0), np.zeros(0), np.zeros(0)
    spectrum = np.abs(np.fft.rfft((y - np.mean(y)) * np.hanning(y.size)))
    frequencies_hz = np.fft.rfftfreq(y.size, d=float(np.mean(steps)) * 1e-9)
    if not spectrum.size or spectrum.max() <= 0:
        return frequencies_hz, spectrum, np.zeros(0)
    peaks, _ = find_peaks(spectrum, height=0.2 * spectrum.max())
    ordered = peaks[np.argsort(spectrum[peaks])[::-1]]
    return frequencies_hz, spectrum, frequencies_hz[ordered]


def _array(data, name):
    try:
        return np.asarray(data[name], dtype=float)
    except (KeyError, TypeError):
        return np.asarray(getattr(data, name), dtype=float)


def _optional_array(data, name):
    try:
        return _array(data, name)
    except AttributeError:
        return None


def normalized_result(
    *,
    kind,
    x_name,
    x_unit,
    x,
    data,
    integration_seconds,
    reps,
    requested,
    executed,
    t1_ratio=False,
):
    """Normalize QICK-DAWG's four-readout result into a stable native schema."""
    signal_on = _array(data, "signal1")
    laser_ref_on = _array(data, "reference1")
    signal_off = _optional_array(data, "signal2")
    on_norm = np.divide(
        signal_on, laser_ref_on,
        out=np.zeros_like(signal_on), where=laser_ref_on != 0,
    )
    if signal_off is None:
        # get_reference=False: the MW-off sequence was never run, so the only
        # normalisation left is the steady state at the end of the same laser
        # pulse, which is what reference1 already is.
        return ExperimentResult(
            kind=kind,
            x_name=x_name,
            x_unit=x_unit,
            x=np.asarray(x, dtype=float),
            signal_counts=signal_on,
            reference_counts=laser_ref_on,
            signal_rate_cps=signal_on / (float(integration_seconds) * int(reps)),
            reference_rate_cps=laser_ref_on / (float(integration_seconds) * int(reps)),
            contrast=on_norm,
            requested=requested,
            executed=executed,
        )
    laser_ref_off = _array(data, "reference2")
    off_norm = np.divide(
        signal_off, laser_ref_off,
        out=np.zeros_like(signal_off), where=laser_ref_off != 0,
    )
    if t1_ratio:
        contrast = np.divide(
            on_norm, off_norm, out=np.zeros_like(on_norm), where=off_norm != 0
        )
    else:
        contrast = np.divide(
            off_norm - on_norm,
            off_norm,
            out=np.zeros_like(off_norm),
            where=off_norm != 0,
        )
    norm = float(integration_seconds) * int(reps)
    return ExperimentResult(
        kind=kind,
        x_name=x_name,
        x_unit=x_unit,
        x=np.asarray(x, dtype=float),
        signal_counts=signal_on,
        reference_counts=signal_off,
        signal_rate_cps=signal_on / norm,
        reference_rate_cps=signal_off / norm,
        contrast=contrast,
        requested=requested,
        executed=executed,
    )
