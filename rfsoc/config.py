"""Validation and unit conversion for RFSoC4x2 programs."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import logging
from typing import Iterable

import numpy as np

from common import utils


@dataclass(frozen=True)
class ReadoutPlan:
    seconds: float
    samples: int


def expected_nqz(firmware: str, frequency_hz: float) -> int:
    """Return the RF-DAC Nyquist zone expected for a positive frequency."""
    sample_rate_hz = 9.8304e9 if firmware == "photon_counting_9ghz" else 4.9152e9
    if not 0 < frequency_hz < sample_rate_hz:
        raise ValueError(
            f"{frequency_hz / 1e9:.6g} GHz is outside the configured DAC range"
        )
    return int(frequency_hz // (sample_rate_hz / 2.0)) + 1


def validate_tone(frequency_hz: float, nqz: int) -> None:
    """Refuse a tone whose Nyquist zone does not match the one configured."""
    zone = expected_nqz(utils.RFSOC_FIRMWARE, frequency_hz)
    if zone != int(nqz):
        raise ValueError(
            f"{frequency_hz / 1e9:.6g} GHz requires NQZ {zone} with "
            f"{utils.RFSOC_FIRMWARE}, configured {int(nqz)}"
        )


def validate_hardware_settings(
    frequency_hz: float | None = None,
    mw2_frequency_hz: float | None = None,
) -> None:
    if utils.RFSOC_FIRMWARE not in {"photon_counting", "photon_counting_9ghz"}:
        raise ValueError(f"Unsupported RFSoC firmware: {utils.RFSOC_FIRMWARE}")
    if utils.RFSOC_EDGE_LOW_THRESHOLD >= utils.RFSOC_EDGE_HIGH_THRESHOLD:
        raise ValueError("RFSoC edge low threshold must be below high threshold")
    if utils.RFSOC_AOM_PMOD == utils.RFSOC_PIXEL_CLOCK_PMOD:
        raise ValueError("AOM and pixel clock must use different PMOD pins")
    if utils.RFSOC_MW_CHANNEL == utils.RFSOC_MW2_CHANNEL:
        raise ValueError("the two microwave tones must use different DAC channels")
    if frequency_hz is not None:
        validate_tone(frequency_hz, utils.RFSOC_MW_NQZ)
    if mw2_frequency_hz is not None:
        validate_tone(mw2_frequency_hz, utils.RFSOC_MW2_NQZ)


def readout_plan(dwell_seconds: float, readout_clock_hz: float) -> ReadoutPlan:
    """Express a dwell as the single ADC window that will count it.

    Every pixel holds the edge counter for its whole dwell.  A dwell past the
    window measured on this board is refused rather than split: splitting one
    dwell into several windows means either several readouts per pixel or
    several passes over the image, and both cost more than they buy.
    """
    if dwell_seconds <= 0 or readout_clock_hz <= 0:
        raise ValueError("dwell and readout clock must be positive")
    if dwell_seconds > utils.RFSOC_MAX_COUNTING_WINDOW_S:
        raise ValueError(
            f"dwell {dwell_seconds:.6g} s is longer than the counting window "
            f"measured on this board ({utils.RFSOC_MAX_COUNTING_WINDOW_S:.6g} s); "
            "re-run rfsoc.diagnostics.window_linearity() to justify raising it"
        )
    samples = max(1, int(round(dwell_seconds * readout_clock_hz)))
    return ReadoutPlan(seconds=samples / readout_clock_hz, samples=samples)


def validate_linear_sweep(values: Iterable[float], name: str) -> np.ndarray:
    values = np.asarray(list(values), dtype=float)
    if values.ndim != 1 or values.size < 2 or not np.all(np.isfinite(values)):
        raise ValueError(f"{name} must contain at least two finite values")
    delta = np.diff(values)
    if not np.allclose(delta, delta[0], rtol=1e-7, atol=1e-12):
        raise ValueError(f"{name} must be linear for a single FPGA acquire")
    return values


def readout_clock_hz(soccfg, adc_channel: int = utils.RFSOC_ADC_CHANNEL) -> float:
    """Extract the edge-counter sample clock from QickConfig."""
    cfg = soccfg["readouts"][adc_channel]
    for key in ("f_output", "fs"):
        if key in cfg:
            return float(cfg[key]) * 1e6
    raise KeyError("QickConfig does not expose the readout clock")


_SUM_BUFFER_WARNING = "sum buffer will overflow"


class _SumBufferFilter(logging.Filter):
    def filter(self, record):
        return _SUM_BUFFER_WARNING not in record.getMessage()


@contextmanager
def edge_counting_warnings_muted():
    """Drop QICK's sum-buffer warning while an edge-counting program compiles.

    ``declare_readout`` warns for every window longer than 2**16 samples, since
    summing 15-bit analog samples that long can overflow the 32-bit accumulator.
    An edge count increments once per pulse instead of summing samples, so the
    arithmetic behind the warning does not apply, and confocal dwells are past
    that length by design: ``window_linearity()`` measured counts proportional to
    the window up to ``RFSOC_MAX_COUNTING_WINDOW_S``.  Only this one message is
    filtered, and only while the program is being built.
    """
    logger = logging.getLogger("qick.qick_asm")
    sum_buffer_filter = _SumBufferFilter()
    logger.addFilter(sum_buffer_filter)
    try:
        yield
    finally:
        logger.removeFilter(sum_buffer_filter)


def base_nv_config(session, *, reps: int = 1):
    """Build a validated NVConfiguration after the client is connected."""
    validate_hardware_settings()
    cfg = session.new_config()
    cfg.adc_channel = utils.RFSOC_ADC_CHANNEL
    cfg.edge_counting = True
    cfg.high_threshold = utils.RFSOC_EDGE_HIGH_THRESHOLD
    cfg.low_threshold = utils.RFSOC_EDGE_LOW_THRESHOLD
    cfg.laser_gate_pmod = utils.RFSOC_AOM_PMOD
    cfg.mw_channel = utils.RFSOC_MW_CHANNEL
    cfg.mw_nqz = utils.RFSOC_MW_NQZ
    cfg.mw2_channel = utils.RFSOC_MW2_CHANNEL
    cfg.mw2_nqz = utils.RFSOC_MW2_NQZ
    # NVAveragerProgram.check_cfg accesses mw_gain for every subclass.
    cfg.mw_gain = 0
    cfg.mw2_gain = 0
    cfg.reps = int(reps)
    cfg.soft_avgs = 1
    cfg.pre_init = True
    return cfg
