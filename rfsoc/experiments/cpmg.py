"""Coherence sweeps on CPMGXYFineRes: Ramsey, Hahn echo and CPMG-N.

One program covers all three, because they are the same sequence with a
different number of refocusing pulses between the two pi/2 pulses: none for
Ramsey, which decays in T2*, one for the Hahn echo, which decays in T2, and N
for CPMG, which pushes T2 out by refocusing faster noise.  The refocusing
pulses alternate X and Y in blocks of eight, so a pulse-length error that would
otherwise accumulate over N pulses partly cancels.
"""

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


def _decay_model(t_ns, amplitude, t2_ns, offset):
    return amplitude * np.exp(-t_ns / t2_ns) + offset


def _ramsey_model(t_ns, amplitude, detuning_mhz, t2_star_ns, offset):
    phase = 2 * np.pi * detuning_mhz * 1e-3 * t_ns
    return amplitude * np.cos(phase) * np.exp(-t_ns / t2_star_ns) + offset


def ramsey_spectrum(result):
    """Spectrum of a Ramsey trace: the detuning, and any resolved hyperfine.

    The precession is read out as a beat between the drive and the transition,
    so the spectrum peaks at the detuning, and at the detuning plus the nitrogen
    hyperfine splitting when the sweep is long enough to resolve it.
    """
    return oscillation_spectrum(result.x, result.contrast)


def _fit_ramsey(result):
    t_ns = np.asarray(result.x, dtype=float)
    y = np.asarray(result.contrast, dtype=float)
    frequencies_hz, _, peaks_hz = ramsey_spectrum(result)
    # The spectrum is a far better first guess for the frequency than anything
    # a decaying cosine fit would find on its own.
    guess_mhz = float(peaks_hz[0]) / 1e6 if peaks_hz.size else 1.0
    popt, errors = fit_curve(
        _ramsey_model,
        t_ns,
        y,
        p0=[
            float(np.ptp(y) / 2),
            guess_mhz,
            max(float(t_ns[-1]), 1.0),
            float(np.mean(y)),
        ],
        bounds=(
            [-np.inf, 0.0, 1e-3, -np.inf],
            [np.inf, np.inf, np.inf, np.inf],
        ),
    )
    fit = {"fft_peaks_hz": [float(peak) for peak in peaks_hz[:4]]}
    if frequencies_hz.size:
        fit["fft_resolution_hz"] = float(frequencies_hz[1])
    if popt is not None:
        fit.update(
            amplitude=float(popt[0]),
            detuning_hz=float(popt[1]) * 1e6,
            detuning_error_hz=float(errors[1]) * 1e6,
            t2_star_seconds=float(popt[2]) * 1e-9,
            t2_star_error_seconds=float(errors[2]) * 1e-9,
            offset=float(popt[3]),
        )
    result.fit = fit
    return result


def _fit_decay(result):
    t_ns = np.asarray(result.x, dtype=float)
    y = np.asarray(result.contrast, dtype=float)
    popt, errors = fit_curve(
        _decay_model,
        t_ns,
        y,
        p0=[float(y[0] - y[-1]), max(float(np.median(t_ns)), 1.0), float(y[-1])],
        bounds=([-np.inf, 1e-3, -np.inf], [np.inf, np.inf, np.inf]),
    )
    if popt is None:
        result.fit = None
        return result
    result.fit = {
        "amplitude": float(popt[0]),
        "t2_seconds": float(popt[1]) * 1e-9,
        "t2_error_seconds": float(errors[1]) * 1e-9,
        "offset": float(popt[2]),
    }
    return result


def fitted_curve(result):
    """Sample the fitted coherence decay, and label T2 or T2* and the detuning."""
    fit = result.fit
    if not fit:
        return None
    x = np.linspace(float(result.x[0]), float(result.x[-1]), 512)
    if "t2_star_seconds" in fit:
        y = _ramsey_model(
            x,
            fit["amplitude"],
            fit["detuning_hz"] / 1e6,
            fit["t2_star_seconds"] * 1e9,
            fit["offset"],
        )
        label = (
            f"T2* {fit['t2_star_seconds'] * 1e6:.3f} us\n"
            f"detuning {fit['detuning_hz'] / 1e6:.3f} MHz"
        )
        return x, y, label
    if "t2_seconds" in fit:
        y = _decay_model(x, fit["amplitude"], fit["t2_seconds"] * 1e9, fit["offset"])
        return x, y, f"T2 {fit['t2_seconds'] * 1e6:.3f} us"
    return None


def coherence_result(cfg, data, *, kind):
    """Turn one ``CPMGXYFineRes.acquire()`` payload into a fitted decay.

    Ramsey (``n_cpmg`` 0) is the oscillating decay.  Hahn echo and CPMG-N are
    the plain exponential.  ``kind`` selects which of those the plot asks for.
    """
    x_ns = _array(data, "tau_ftns")
    result = normalized_result(
        kind=kind,
        x_name="Tau",
        x_unit="ns",
        x=x_ns,
        data=data,
        integration_seconds=float(cfg.readout_integration_tns) * 1e-9,
        reps=cfg.reps,
        requested=configuration_record(cfg),
        executed=executed_configuration(cfg, tau_ftns=x_ns),
    )
    n_cpmg = int(getattr(cfg, "n_cpmg", 0))
    return _fit_ramsey(result) if n_cpmg == 0 else _fit_decay(result)
