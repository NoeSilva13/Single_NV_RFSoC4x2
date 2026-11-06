"""Manual RFSoC4x2 bring-up checks.

Run individual checks at the bench; they intentionally never guess wiring or
silently continue after a firmware mismatch.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math

from common import utils
from .client import RFSoCSession
from .config import (
    expected_nqz,
    readout_clock_hz,
    validate_hardware_settings,
)

# One PLIntensity shot is an ADC trigger at a fixed offset, the counting window,
# and the gap that follows it. QICK encodes each of those instants as a 31-bit
# tProc immediate, so a longer shot cannot be expressed at all.
TPROC_MAX_IMMEDIATE_TREG = 2 ** 31
_ADC_TRIG_OFFSET_TREG = 270  # QickProgram.trigger default, which PLIntensity takes
# Gap held between windows: far longer than the buffer re-arm, yet negligible
# next to the multi-second windows at the top of the ladder.
PROBE_GAP_SECONDS = 100e-6


def connection_report(session=None):
    session = session or RFSoCSession()
    session.connect()
    cfg = session.soc.get_cfg()
    report = {
        "host": session.host,
        "server_name": session.server_name,
        "expected_firmware": utils.RFSOC_FIRMWARE,
        "adc_channel": utils.RFSOC_ADC_CHANNEL,
        "readout_clock_hz": readout_clock_hz(
            session.soccfg, utils.RFSOC_ADC_CHANNEL
        ),
        "mw_channel": utils.RFSOC_MW_CHANNEL,
        "mw_nqz": utils.RFSOC_MW_NQZ,
        "mw2_channel": utils.RFSOC_MW2_CHANNEL,
        "mw2_nqz": utils.RFSOC_MW2_NQZ,
        "aom_pmod": utils.RFSOC_AOM_PMOD,
        "pixel_clock_pmod": utils.RFSOC_PIXEL_CLOCK_PMOD,
        "soc_cfg": cfg,
    }
    try:
        report["mw2_generator"] = check_second_generator(session.soccfg["gens"])
    except ValueError as exc:
        report["mw2_generator"] = f"unusable: {exc}"
    return report


def check_second_generator(
    gens, mw_channel=utils.RFSOC_MW_CHANNEL, mw2_channel=utils.RFSOC_MW2_CHANNEL
):
    """Confirm the DEER target generator exists and times like the sensor one.

    NVConfiguration converts every ``_ftns`` and ``_fMHz`` value with generator
    0's samples per clock and DDS clock, so a second generator that differs in
    either would play its pulse at the wrong length or the wrong frequency.
    """
    if mw2_channel == mw_channel:
        raise ValueError("the two tones must use different generators")
    if not 0 <= mw2_channel < len(gens):
        raise ValueError(
            f"generator {mw2_channel} is not in this firmware ({len(gens)} generators)"
        )
    first, second = gens[mw_channel], gens[mw2_channel]
    for key in ("samps_per_clk", "f_dds"):
        if first.get(key) != second.get(key):
            raise ValueError(
                f"generator {mw2_channel} {key} {second.get(key)} differs from "
                f"generator {mw_channel} {first.get(key)}"
            )
    return {
        "channel": mw2_channel,
        "samps_per_clk": second.get("samps_per_clk"),
        "f_dds_mhz": second.get("f_dds"),
    }


def validate_mw_frequency(frequency_hz=2.87e9):
    validate_hardware_settings(frequency_hz)
    return {
        "frequency_hz": frequency_hz,
        "firmware": utils.RFSOC_FIRMWARE,
        "expected_nqz": expected_nqz(utils.RFSOC_FIRMWARE, frequency_hz),
    }


def edge_count_rate(integration_seconds=None, reps=10, session=None):
    """Collect repeated PL edge counts for threshold/linearity bench tests.

    Every rep is one window, so the integration has to stay within
    ``RFSOC_MAX_COUNTING_WINDOW_S``. Defaults to 100 µs when
    *integration_seconds* is omitted.
    """
    from .config import base_nv_config, readout_plan

    session = session or RFSoCSession()
    session.connect()
    with session.acquisition():
        cfg = base_nv_config(session, reps=reps)
        clock = readout_clock_hz(session.soccfg, cfg.adc_channel)
        if integration_seconds is None:
            integration_seconds = min(100e-6, utils.RFSOC_MAX_COUNTING_WINDOW_S)
        plan = readout_plan(integration_seconds, clock)
        cfg.readout_integration_treg = plan.samples
        cfg.relax_delay_treg = 1
        total = int(session.qd.PLIntensity(cfg).acquire(progress=False))
    return {
        "total_counts": total,
        "reps": reps,
        "integration_seconds": plan.seconds,
        "count_rate_cps": total / (reps * plan.seconds),
        "high_threshold": cfg.high_threshold,
        "low_threshold": cfg.low_threshold,
    }


def _pl_counts(session, integration_treg, reps, gap_treg=None):
    """Total edge counts for *reps* windows of *integration_treg* each.

    The gap defaults to one full window so consecutive windows can never be
    back-to-back; that isolates window length from re-arm behaviour.
    """
    from .config import base_nv_config

    cfg = base_nv_config(session, reps=int(reps))
    cfg.readout_integration_treg = int(integration_treg)
    cfg.relax_delay_treg = int(gap_treg if gap_treg is not None else integration_treg)
    return int(session.qd.PLIntensity(cfg).acquire(progress=False))


@dataclass(frozen=True)
class WindowProbe:
    integration_treg: int
    reps: int
    seconds: float


def probe_window_ladder(clock_hz, max_seconds=5.0):
    """Window lengths bracketing every counter width the firmware could have.

    A register narrower than the value written wraps at a power of two, so the
    ladder steps through them from 2**12 upwards and finishes on the requested
    ceiling. A wrap shows up as a rate that stops matching the short-window
    reference, which is the only symptom available: nothing in QICK reports the
    width of that counter, and writing past it raises no error.
    """
    top = int(round(float(max_seconds) * float(clock_hz)))
    if top < 2 ** 12:
        raise ValueError(
            f"a {max_seconds:.6g} s ceiling is shorter than the first window"
        )
    lengths = [2 ** n for n in range(12, top.bit_length()) if 2 ** n <= top]
    if lengths[-1] != top:
        lengths.append(top)
    return lengths


def window_probe_plan(
    lengths, clock_hz, gap_treg, budget_seconds=4.0, max_reps=8192
):
    """Pair every window with a shot count, and reject the unschedulable ones.

    Shots are spent where they buy precision. A multi-second window counts
    enough photons on its own, while the short end needs thousands of
    repetitions before the reference stops being the noisiest point of the
    sweep; spending a fixed count everywhere would either starve the short
    windows or spend an hour on the long ones. Each length is also checked
    against the tProc immediate here, because overrunning it otherwise raises
    inside QICK's assembler with a message that never mentions the window.
    """
    gap_treg = int(gap_treg)
    plan = []
    for length in lengths:
        length = int(length)
        if length < 1:
            raise ValueError("a counting window needs at least one sample")
        shot_treg = _ADC_TRIG_OFFSET_TREG + length + gap_treg
        if shot_treg > TPROC_MAX_IMMEDIATE_TREG:
            raise ValueError(
                f"a {length / float(clock_hz):.6g} s window plus its gap needs "
                f"{shot_treg} tProc cycles, beyond the 31-bit immediate that "
                f"carries the trigger and the sync after it "
                f"(at most {TPROC_MAX_IMMEDIATE_TREG / float(clock_hz):.6g} s "
                f"per shot)"
            )
        seconds = length / float(clock_hz)
        shot_seconds = shot_treg / float(clock_hz)
        reps = max(
            1,
            min(int(max_reps), math.floor(float(budget_seconds) / shot_seconds)),
        )
        plan.append(WindowProbe(length, reps, seconds))
    return plan


def window_linearity(
    lengths=None,
    max_seconds=5.0,
    budget_seconds=4.0,
    max_reps=8192,
    session=None,
):
    """Measure count rate against requested single-window length.

    This probe is what justifies ``RFSOC_MAX_COUNTING_WINDOW_S``: feed a stable
    pulse source and look for the first length where the rate stops matching the
    short-window reference. Everything below that point can be used as one ADC
    window per pixel, which removes the need to split a dwell at all.

    QICK warns above 2**16 samples about overflowing the sum buffer, but that
    warning is about accumulating 15-bit analog samples in a 32-bit word and
    does not apply to an edge count. A run against an 80 kHz source stayed
    proportional from 13 µs to 5 s, with no sign of the 16-bit truncation that
    would have zeroed the 131072-sample point; that measurement is what
    ``RFSOC_MAX_COUNTING_WINDOW_S`` records.

    The ladder reaches *max_seconds*, 5 s by default, which is most of what a
    single shot can schedule: past roughly 7 s the window no longer fits the
    tProc immediate. Each window costs at most *budget_seconds*, so expect the
    default sweep to run a minute or two. The gap between windows is a fixed
    100 µs rather than a second copy of the window, which at these lengths would
    both double the sweep and overrun that same immediate.

    Read ``ratio_to_first`` knowing that the reference is the shortest and
    therefore coarsest window: with a periodic source its own quantisation
    biases every ratio by a fixed factor. Check ``counts_per_shot`` before
    blaming a falling ratio on the window, because the accumulator is 32-bit
    signed and a bright source saturating it looks exactly like a counter that
    wraps.
    """
    session = session or RFSoCSession()
    session.connect()
    clock = readout_clock_hz(session.soccfg, utils.RFSOC_ADC_CHANNEL)
    gap_treg = max(1, int(round(PROBE_GAP_SECONDS * clock)))
    if lengths is None:
        lengths = probe_window_ladder(clock, max_seconds)
    plan = window_probe_plan(
        lengths,
        clock,
        gap_treg,
        budget_seconds=budget_seconds,
        max_reps=max_reps,
    )
    rows = []
    with session.acquisition():
        reference = None
        for probe in plan:
            counts = _pl_counts(
                session, probe.integration_treg, probe.reps, gap_treg=gap_treg
            )
            rate = counts / (probe.reps * probe.seconds)
            if reference is None:
                reference = rate
            rows.append({
                "integration_treg": probe.integration_treg,
                "integration_s": probe.seconds,
                "reps": probe.reps,
                "total_counts": counts,
                "counts_per_shot": counts / probe.reps,
                "count_rate_cps": rate,
                "ratio_to_first": rate / reference if reference else float("nan"),
            })
    return {
        "readout_clock_hz": clock,
        "gap_treg": gap_treg,
        "accumulator_limit_counts": 2 ** 31 - 1,
        "windows": rows,
    }


def acquire_overhead(shot_counts=(1, 10, 100, 1000), integration_treg=4096,
                     session=None):
    """Split acquire wall time into fixed cost per call and cost per shot.

    Confocal scan time used to be dominated by the number of ``acquire()`` calls
    rather than by integration. The fitted intercept is what the whole-frame
    program now pays once per image instead of once per line.
    """
    import time

    session = session or RFSoCSession()
    session.connect()
    rows = []
    with session.acquisition():
        clock = readout_clock_hz(session.soccfg, utils.RFSOC_ADC_CHANNEL)
        shot_seconds = 2 * int(integration_treg) / clock
        for reps in shot_counts:
            start = time.perf_counter()
            _pl_counts(session, integration_treg, reps)
            elapsed = time.perf_counter() - start
            rows.append({
                "reps": int(reps),
                "elapsed_s": elapsed,
                "hardware_s": int(reps) * shot_seconds,
                "overhead_s": elapsed - int(reps) * shot_seconds,
            })
    n = len(rows)
    x = [row["reps"] for row in rows]
    y = [row["elapsed_s"] for row in rows]
    sx, sy = sum(x), sum(y)
    sxx = sum(i * i for i in x)
    sxy = sum(i * j for i, j in zip(x, y))
    denom = n * sxx - sx * sx
    slope = (n * sxy - sx * sy) / denom if denom else float("nan")
    return {
        "fixed_cost_s": (sy - slope * sx) / n if denom else float("nan"),
        "per_shot_s": slope,
        "expected_per_shot_s": shot_seconds,
        "measurements": rows,
    }


def main():
    validate_hardware_settings()
    print(json.dumps(connection_report(), indent=2, default=str))
    print(json.dumps(validate_mw_frequency(), indent=2))
    print(
        "Next bench checks: inspect AOM/pixel-clock PMOD with an oscilloscope; "
        "then run edge_count_rate() while sweeping safe conditioned TTL levels."
    )


if __name__ == "__main__":
    main()
