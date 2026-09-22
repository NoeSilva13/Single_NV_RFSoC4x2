"""Readout window calibration: where to count, for how long, and how long to pump.

``CountingDurationFineRes`` counts a single narrow window per acquire, so the
window is walked across the laser pulse from the host, one program per offset.
With the microwave pulse on and off, the difference between the two traces is the
spin contrast, and it decays as the laser repumps the NV into ms=0.  That time
constant sets the three numbers every pulsed experiment depends on: the counting
window, the offset that puts it where the photoluminescence has risen, and the
pumping time.
"""

from __future__ import annotations

import numpy as np

from .base import ExperimentResult, configuration_record, executed_configuration, fit_curve

# Fraction of the peak photoluminescence that counts as "the laser is on".
PL_RISE_FRACTION = 0.8
# Pumping time recommended as a multiple of the measured repump constant.
LASER_ON_TAU_MULTIPLE = 5.0


def _exponential(t, amplitude, tau, offset):
    return amplitude * np.exp(-t / tau) + offset


def _fit_window(offsets_ns, contrast, pl_cps):
    """Turn a window walk into the numbers the other experiments need.

    The fractional contrast is what decays cleanly: both traces carry the same
    photoluminescence rise, so dividing it out leaves the repump exponential on
    its own.  The fit starts at the peak of that contrast, because everything
    before it is the laser still turning on.
    """
    peak = int(np.argmax(contrast))
    t = offsets_ns[peak:] - offsets_ns[peak]
    y = contrast[peak:]
    fit = {"peak_contrast_offset_tns": float(offsets_ns[peak])}

    rise = np.flatnonzero(pl_cps >= PL_RISE_FRACTION * np.max(pl_cps))
    if rise.size:
        fit["recommended_laser_readout_offset_tns"] = float(offsets_ns[rise[0]])

    popt, errors = fit_curve(
        _exponential,
        t,
        y,
        p0=[float(y[0] - y[-1]), max(float(np.median(t)), 1.0), float(y[-1])],
        bounds=([-np.inf, 1e-3, -np.inf], [np.inf, np.inf, np.inf]),
    )
    if popt is None:
        return fit
    tau = float(popt[1])
    fit.update(
        amplitude=float(popt[0]),
        tau_ns=tau,
        tau_error_ns=float(errors[1]),
        offset=float(popt[2]),
        recommended_readout_integration_tns=tau,
        recommended_laser_on_tns=LASER_ON_TAU_MULTIPLE * tau,
    )
    return fit


def fitted_curve(result):
    """Sample the fitted repump decay, and label the numbers it recommends."""
    fit = result.fit
    if not fit or "tau_ns" not in fit:
        return None
    peak_ns = fit["peak_contrast_offset_tns"]
    x = np.linspace(peak_ns, float(result.x[-1]), 512)
    y = _exponential(x - peak_ns, fit["amplitude"], fit["tau_ns"], fit["offset"])
    label = (
        f"repump tau {fit['tau_ns']:.0f} ns\n"
        f"readout_integration_tns {fit['recommended_readout_integration_tns']:.0f}\n"
        f"laser_on_tns {fit['recommended_laser_on_tns']:.0f}"
    )
    offset = fit.get("recommended_laser_readout_offset_tns")
    if offset is not None:
        label += f"\nlaser_readout_offset_tns {offset:.0f}"
    return x, y, label


def window_result(cfg, offsets_tns, signal_on, signal_off):
    """Fit the host-side walk of ``CountingDurationFineRes``.

    Each acquire counts one offset, so the script stacks ``signal1`` and
    ``signal2`` and passes those traces here.  The contrast is the microwave-on
    rate against the microwave-off rate.
    """
    offsets = np.asarray(offsets_tns, dtype=float)
    signal_on = np.asarray(signal_on, dtype=float)
    signal_off = np.asarray(signal_off, dtype=float)
    norm = float(cfg.readout_integration_tns) * 1e-9 * int(cfg.reps)
    rate_on = signal_on / norm
    rate_off = signal_off / norm
    contrast = np.divide(
        rate_off - rate_on, rate_off, out=np.zeros_like(rate_off), where=rate_off != 0
    )
    result = ExperimentResult(
        kind="Readout_Window",
        x_name="Laser_readout_offset",
        x_unit="ns",
        x=offsets,
        signal_counts=signal_on,
        reference_counts=signal_off,
        signal_rate_cps=rate_on,
        reference_rate_cps=rate_off,
        contrast=contrast,
        requested=configuration_record(cfg),
        executed=executed_configuration(cfg, laser_readout_offset_tns=offsets),
    )
    result.fit = _fit_window(offsets, contrast, rate_off)
    return result
