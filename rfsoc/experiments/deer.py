"""DEER: where the second tone flips the target spin, and how strongly they couple."""

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
from .odmr import _fit_resonance

# mu0 h gamma_e**2 / (4 pi) for two electron spins whose transitions each change
# m_s by one: the dipolar frequency at 1 nm when |1 - 3 cos^2 theta| = 1.
DIPOLAR_HZ_NM3 = 52.04e6
# |1 - 3 cos^2 theta| reaches 2 with the pair along the field, so no pair
# coupling at a given frequency can sit farther apart than this factor allows.
MAX_ANGULAR_FACTOR = 2.0


def deer_spectrum_result(cfg, data):
    """Turn one ``DEERSpectrumFineRes.acquire()`` payload into a fitted spectrum.

    ``signal1`` is the echo with the target pulse and ``signal2`` the same echo
    without it, so the contrast is the fraction of the echo the target pulse
    destroys: a dip-shaped line at the target resonance, fitted as in ODMR.
    """
    x_mhz = _array(data, "mw2_fMHz")
    result = normalized_result(
        kind="DEER_Spectrum",
        x_name="Frequency",
        x_unit="Hz",
        x=x_mhz * 1e6,
        data=data,
        integration_seconds=float(cfg.readout_integration_tns) * 1e-9,
        reps=cfg.reps,
        requested=configuration_record(cfg),
        executed=executed_configuration(cfg, mw2_fMHz=x_mhz),
    )
    return _fit_resonance(result)


def dipolar_distance_nm(coupling_hz, angular_factor=1.0):
    """Separation of two electron spins coupled at *coupling_hz*.

    ``angular_factor`` is ``|1 - 3 cos^2 theta|`` for the angle between the
    pair and the field; a single DEER trace does not measure it.
    """
    coupling_hz = abs(float(coupling_hz))
    if coupling_hz == 0:
        return float("inf")
    return float((DIPOLAR_HZ_NM3 * float(angular_factor) / coupling_hz) ** (1 / 3))


def _dipolar_model(tau_ns, amplitude, coupling_mhz, decay_ns, offset):
    phase = 2 * np.pi * coupling_mhz * 1e-3 * tau_ns
    return amplitude / 2 * (1 - np.cos(phase) * np.exp(-tau_ns / decay_ns)) + offset


def _fit_dipolar(result):
    """Fit the echo the target pulse removes as one coupled spin.

    Flipping the target at the echo's pi pulse leaves the sensor a phase of
    ``2 pi nu_dd tau``, where ``nu_dd`` is how far the target's two states
    shift the sensor's transition, so the lost fraction oscillates as
    ``(1 - cos)/2`` in tau.  The oscillation damps as the pair dephases, and a
    bath of targets with no single coupling shows only that damping.
    """
    tau_ns = np.asarray(result.x, dtype=float)
    y = np.asarray(result.contrast, dtype=float)
    _, _, peaks_hz = oscillation_spectrum(tau_ns, y)
    span_ns = float(tau_ns[-1] - tau_ns[0]) if tau_ns.size > 1 else 1.0
    guess_mhz = float(peaks_hz[0]) / 1e6 if peaks_hz.size else 1e3 / max(2 * span_ns, 1.0)
    popt, errors = fit_curve(
        _dipolar_model,
        tau_ns,
        y,
        p0=[
            float(np.ptp(y)),
            guess_mhz,
            max(float(tau_ns[-1]), 1.0),
            float(np.min(y)),
        ],
        bounds=(
            [-np.inf, 0.0, 1e-3, -np.inf],
            [np.inf, np.inf, np.inf, np.inf],
        ),
    )
    if popt is None:
        result.fit = None
        return result
    coupling_hz = float(popt[1]) * 1e6
    coupling_error_hz = float(errors[1]) * 1e6
    distance_nm = dipolar_distance_nm(coupling_hz)
    relative = coupling_error_hz / coupling_hz if coupling_hz else float("inf")
    result.fit = {
        "amplitude": float(popt[0]),
        "dipolar_coupling_hz": coupling_hz,
        "dipolar_coupling_error_hz": coupling_error_hz,
        "decay_ns": float(popt[2]),
        "offset": float(popt[3]),
        # r scales as nu**(-1/3), so its relative error is a third of nu's.
        "distance_nm": distance_nm,
        "distance_error_nm": distance_nm * relative / 3,
        "max_distance_nm": dipolar_distance_nm(coupling_hz, MAX_ANGULAR_FACTOR),
    }
    return result


def fitted_curve(result):
    """Sample the fitted dipolar oscillation, and label coupling and distance."""
    fit = result.fit
    if not fit or "dipolar_coupling_hz" not in fit:
        return None
    x = np.linspace(float(result.x[0]), float(result.x[-1]), 512)
    y = _dipolar_model(
        x,
        fit["amplitude"],
        fit["dipolar_coupling_hz"] / 1e6,
        fit["decay_ns"],
        fit["offset"],
    )
    label = (
        f"nu_dd {fit['dipolar_coupling_hz'] / 1e3:.1f} kHz\n"
        f"r {fit['distance_nm']:.2f} nm at |1-3cos^2| = 1\n"
        f"r <= {fit['max_distance_nm']:.2f} nm"
    )
    return x, y, label


def deer_tau_result(cfg, taus_ns, readouts):
    """Fit the host-side tau walk of ``DEERSpectrumFineRes``.

    Each acquire holds the target frequency and one tau, so the script stacks
    the four readouts per tau and passes them here, keyed as the program names
    them (``signal1``, ``reference1``, ``signal2``, ``reference2``).
    """
    taus_ns = np.asarray(taus_ns, dtype=float)
    result = normalized_result(
        kind="DEER_Tau",
        x_name="Tau",
        x_unit="ns",
        x=taus_ns,
        data={key: np.asarray(value, dtype=float) for key, value in readouts.items()},
        integration_seconds=float(cfg.readout_integration_tns) * 1e-9,
        reps=cfg.reps,
        requested=configuration_record(cfg),
        executed=executed_configuration(cfg, tau_ftns=taus_ns),
    )
    return _fit_dipolar(result)
