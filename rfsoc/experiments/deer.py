"""DEER spectrum: where the second tone flips the target spin."""

from __future__ import annotations

from .base import (
    _array,
    configuration_record,
    executed_configuration,
    normalized_result,
)
from .odmr import _fit_resonance


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
