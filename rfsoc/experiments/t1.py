"""FPGA delay-swept T1 acquisition and host-side fit."""

from __future__ import annotations

import numpy as np

from .base import (
    _array,
    configuration_record,
    executed_configuration,
    fit_curve,
    normalized_result,
)


def _t1_model(t_s, amplitude, t1_s, stretch, offset):
    return amplitude * np.exp(-np.power(t_s / t1_s, stretch)) + offset


def _fit_t1(result):
    popt, errors = fit_curve(
        _t1_model,
        result.x * 1e-9,
        result.contrast,
        p0=[
            float(result.contrast[0] - result.contrast[-1]),
            max(float(np.median(result.x * 1e-9)), 1e-9),
            1.0,
            float(result.contrast[-1]),
        ],
        bounds=([-np.inf, 1e-12, 0.1, -np.inf], [np.inf, np.inf, 1.0, np.inf]),
    )
    if popt is None:
        result.fit = None
        return result
    result.fit = {
        "amplitude": float(popt[0]),
        "t1_seconds": float(popt[1]),
        "stretch": float(popt[2]),
        "offset": float(popt[3]),
        "t1_error_seconds": float(errors[1]),
    }
    return result


def fitted_curve(result):
    """Sample the fitted stretched exponential, and label T1."""
    fit = result.fit
    if not fit:
        return None
    x_ns = np.linspace(float(result.x[0]), float(result.x[-1]), 512)
    y = _t1_model(
        x_ns * 1e-9,
        fit["amplitude"],
        fit["t1_seconds"],
        fit["stretch"],
        fit["offset"],
    )
    label = f"T1 {fit['t1_seconds'] * 1e3:.3f} ms\nstretch {fit['stretch']:.2f}"
    return x_ns, y, label


def t1_result(cfg, data):
    """Turn one ``T1FineRes.acquire()`` payload into a fitted relaxation."""
    x_ns = _array(data, "delay_tns")
    result = normalized_result(
        kind="T1",
        x_name="Delay",
        x_unit="ns",
        x=x_ns,
        data=data,
        integration_seconds=float(cfg.readout_integration_tns) * 1e-9,
        reps=cfg.reps,
        requested=configuration_record(cfg),
        executed=executed_configuration(cfg, delay_tns=x_ns),
        t1_ratio=True,
    )
    return _fit_t1(result)
