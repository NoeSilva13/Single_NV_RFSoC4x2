"""Fine-resolution FPGA Rabi sweep and the pulse lengths it calibrates."""

from __future__ import annotations

import numpy as np

from .base import (
    _array,
    configuration_record,
    executed_configuration,
    fit_curve,
    normalized_result,
    oscillation_spectrum,
)


def _rabi_model(t_ns, amplitude, period_ns, decay_ns, offset):
    return (
        amplitude
        * np.cos(2 * np.pi * t_ns / period_ns)
        * np.exp(-t_ns / decay_ns)
        + offset
    )


def _fit_rabi(result):
    """Fit the oscillation and report the pulse lengths it implies.

    Everything pulsed downstream is measured in these two numbers: a pi/2 pulse
    is a quarter of the Rabi period and a pi pulse is half of it.
    """
    t_ns = np.asarray(result.x, dtype=float)
    y = np.asarray(result.contrast, dtype=float)
    _, _, peaks_hz = oscillation_spectrum(t_ns, y)
    span_ns = float(t_ns[-1] - t_ns[0]) if t_ns.size > 1 else 1.0
    period_guess_ns = 1e9 / float(peaks_hz[0]) if peaks_hz.size else span_ns / 2
    popt, errors = fit_curve(
        _rabi_model,
        t_ns,
        y,
        p0=[
            float(np.ptp(y) / 2),
            period_guess_ns,
            max(span_ns, 1.0),
            float(np.mean(y)),
        ],
        bounds=(
            [-np.inf, 1e-3, 1e-3, -np.inf],
            [np.inf, np.inf, np.inf, np.inf],
        ),
    )
    if popt is None:
        result.fit = None
        return result
    period_ns = float(popt[1])
    period_error_ns = float(errors[1])
    result.fit = {
        "amplitude": float(popt[0]),
        "rabi_period_ns": period_ns,
        "rabi_period_error_ns": period_error_ns,
        "rabi_frequency_hz": 1e9 / period_ns,
        "mw_pi2_ftns": period_ns / 4,
        "mw_pi2_error_ftns": period_error_ns / 4,
        "mw_pi_ftns": period_ns / 2,
        "mw_pi_error_ftns": period_error_ns / 2,
        "decay_ns": float(popt[2]),
        "offset": float(popt[3]),
    }
    return result


def fitted_curve(result):
    """Sample the fitted oscillation, and label the calibrated pulse lengths."""
    fit = result.fit
    if not fit:
        return None
    x = np.linspace(float(result.x[0]), float(result.x[-1]), 512)
    y = _rabi_model(
        x, fit["amplitude"], fit["rabi_period_ns"], fit["decay_ns"], fit["offset"]
    )
    label = (
        f"mw_pi2_ftns {fit['mw_pi2_ftns']:.2f}\n"
        f"mw_pi_ftns {fit['mw_pi_ftns']:.2f}\n"
        f"Rabi {fit['rabi_frequency_hz'] / 1e6:.3f} MHz"
    )
    return x, y, label


def rabi_result(cfg, data):
    """Turn one ``RabiFineRes.acquire()`` payload into a fitted oscillation."""
    x_ns = _array(data, "mw_duration_ftns")
    result = normalized_result(
        kind="Rabi",
        x_name="MW_duration",
        x_unit="ns",
        x=x_ns,
        data=data,
        integration_seconds=float(cfg.readout_integration_tns) * 1e-9,
        reps=cfg.reps,
        requested=configuration_record(cfg),
        executed=executed_configuration(cfg, mw_duration_ftns=x_ns),
    )
    return _fit_rabi(result)
