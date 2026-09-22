"""Counted photoluminescence: one PL point or dark counts.

``PLIntensity`` gates the laser while it counts.  ``DarkCounts`` triggers only
the ADC and leaves the laser gate low, so the difference between them is the
detector and room background.  The script compiles the program and passes the
count this function turns into a rate.
"""

from __future__ import annotations

from .base import CountingResult, configuration_record, executed_configuration


def counting_result(kind, cfg, counts):
    """One counted point, as counts and as a rate over the window and the reps.

    The window is ``readout_integration_tns`` after qickdawg has converted
    whichever suffix was assigned.
    """
    window_seconds = float(cfg.readout_integration_tns) * 1e-9
    reps = int(cfg.reps)
    return CountingResult(
        kind=kind,
        counts=int(counts),
        window_seconds=window_seconds,
        reps=reps,
        requested=configuration_record(cfg),
        executed=executed_configuration(cfg),
    )
