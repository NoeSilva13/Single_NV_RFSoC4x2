"""FPGA-swept CW and pulsed ODMR."""

from __future__ import annotations

import numpy as np

from .base import (
    ExperimentResult,
    _array,
    configuration_record,
    executed_configuration,
    fit_curve,
    normalized_result,
)


def _lorentzian(f_hz, amplitude, center_hz, hwhm_hz, offset):
    return amplitude / (1 + ((f_hz - center_hz) / hwhm_hz) ** 2) + offset


def _fit_resonance(result):
    """Fit the ODMR line as one Lorentzian and report where the NV resonates.

    One line, so a spectrum split by strain or by a magnetic field is fitted as
    the deeper of its two dips; sweep each half separately to get both.
    """
    f_hz = np.asarray(result.x, dtype=float)
    y = np.asarray(result.contrast, dtype=float)
    baseline = float(np.median(y))
    deepest = int(np.argmax(np.abs(y - baseline)))
    span_hz = abs(float(f_hz[-1] - f_hz[0])) if f_hz.size > 1 else 1e6
    popt, errors = fit_curve(
        _lorentzian,
        f_hz,
        y,
        p0=[
            float(y[deepest] - baseline),
            float(f_hz[deepest]),
            max(span_hz / 20, 1e3),
            baseline,
        ],
        bounds=(
            [-np.inf, float(np.min(f_hz)), 1e3, -np.inf],
            [np.inf, float(np.max(f_hz)), np.inf, np.inf],
        ),
    )
    if popt is None:
        result.fit = None
        return result
    result.fit = {
        "amplitude": float(popt[0]),
        "resonance_hz": float(popt[1]),
        "resonance_error_hz": float(errors[1]),
        "linewidth_hz": 2 * abs(float(popt[2])),
        "linewidth_error_hz": 2 * float(errors[2]),
        "offset": float(popt[3]),
    }
    return result


def fitted_curve(result):
    """Sample the fitted line densely, and label where the NV resonates."""
    fit = result.fit
    if not fit or "resonance_hz" not in fit:
        return None
    x = np.linspace(float(result.x[0]), float(result.x[-1]), 512)
    y = _lorentzian(
        x,
        fit["amplitude"],
        fit["resonance_hz"],
        fit["linewidth_hz"] / 2,
        fit["offset"],
    )
    label = (
        f"resonance {fit['resonance_hz'] / 1e9:.6f} GHz\n"
        f"linewidth {fit['linewidth_hz'] / 1e6:.3f} MHz"
    )
    return x, y, label


def _cw_contrast(reference, signal):
    return np.divide(
        reference - signal,
        reference,
        out=np.zeros_like(reference),
        where=reference != 0,
    )


def _integration_seconds(cfg):
    return float(cfg.readout_integration_tns) * 1e-9


def lockin_result(cfg, data):
    """Turn one ``LockinODMR.acquire()`` payload into a fitted spectrum."""
    signal = _array(data, "signal")
    reference = _array(data, "reference")
    x_mhz = _array(data, "frequencies")
    norm = _integration_seconds(cfg) * int(cfg.reps)
    result = ExperimentResult(
        kind="CW_ODMR",
        x_name="Frequency",
        x_unit="Hz",
        x=x_mhz * 1e6,
        signal_counts=signal,
        reference_counts=reference,
        signal_rate_cps=signal / norm,
        reference_rate_cps=reference / norm,
        contrast=_cw_contrast(reference, signal),
        requested=configuration_record(cfg),
        executed=executed_configuration(cfg, frequencies=x_mhz),
    )
    return _fit_resonance(result)


def pulsed_result(cfg, data):
    """Turn one ``PODMRFineRes.acquire()`` payload into a fitted spectrum."""
    x_mhz = _array(data, "mw_fMHz")
    result = normalized_result(
        kind="Pulsed_ODMR",
        x_name="Frequency",
        x_unit="Hz",
        x=x_mhz * 1e6,
        data=data,
        integration_seconds=_integration_seconds(cfg),
        reps=cfg.reps,
        requested=configuration_record(cfg),
        executed=executed_configuration(cfg, mw_fMHz=x_mhz),
    )
    return _fit_resonance(result)
