"""RFSoC4x2 experiment runner.

Edit one block, then run ``python run_odmr_experiments.py``.  The shared
``default_config`` is an ``NVConfiguration``: time, frequency and phase use
qickdawg's suffixes (``_tns`` / ``_tus`` / ``_treg``, ``_fMHz`` / ``_fGHz`` /
``_freg``, ``_ftns`` / ``_ftus`` / ``_ftsamp``).  Each block copies that config,
changes only what that experiment sweeps, and calls the qickdawg program.
Linear sweeps use ``start``, ``stop`` and ``delta``.

This can run next to the confocal app: each experiment claims the board for the
duration of its sweep, so the app's live count plot goes quiet and resumes
afterwards.  A scan already in flight keeps the board, and the runner waits
``RFSOC_CLAIM_WAIT_S`` for it before giving up.  The live windows here are the
polite side of the same deal: they take the claim per update and skip an update
rather than interrupt a scan.
"""

from copy import copy
import sys

import numpy as np

from rfsoc.client import RFSoCSession
from rfsoc.config import base_nv_config, edge_counting_warnings_muted
from rfsoc.experiments import (
    coherence_result,
    counting_result,
    lockin_result,
    pulsed_result,
    rabi_result,
    t1_result,
    window_result,
)
from rfsoc.experiments.base import CountingResult, _array
from rfsoc.experiments.io import live_curve, live_scalar, plot_result, save_result
from rfsoc.experiments.odmr import _cw_contrast
from rfsoc.process_lock import RFSoCBusyError


def acquire_counts(session, program_name, kind, cfg):
    """Count one window with a qickdawg counting program already configured."""
    with edge_counting_warnings_muted():
        program = getattr(session.qd, program_name)(cfg)
    with session.acquisition():
        counts = int(program.acquire(progress=False))
    return counting_result(kind, cfg, counts)


def live_pl(session, cfg):
    """Trace photoluminescence until the window is closed or Ctrl+C is hit."""
    with edge_counting_warnings_muted():
        program = session.qd.PLIntensity(cfg)
    window_s = float(cfg.readout_integration_tns) * 1e-9
    norm = window_s * int(cfg.reps)

    def point():
        try:
            with session.acquisition(blocking=False):
                return int(program.acquire(progress=False)) / norm
        except RFSoCBusyError:
            return None

    live_scalar(
        point,
        ylabel="Count rate (cps)",
        title=f"PL intensity, {window_s * 1e3:.1f} ms window",
    )


def live_cwodmr(session, cfg):
    """Average CW ODMR passes into a live spectrum until the window is closed."""
    program = session.qd.LockinODMR(cfg)
    x_mhz = np.linspace(
        cfg.mw_start_fMHz, cfg.mw_end_fMHz, int(cfg.nsweep_points)
    )
    totals = {"signal": 0.0, "reference": 0.0}

    def one_pass():
        try:
            with session.acquisition(blocking=False):
                data = program.acquire(progress=False)
        except RFSoCBusyError:
            return None
        totals["signal"] = totals["signal"] + _array(data, "signal")
        totals["reference"] = totals["reference"] + _array(data, "reference")
        return _cw_contrast(totals["reference"], totals["signal"]) * 100

    live_curve(
        one_pass,
        x_mhz,
        xlabel="Frequency (MHz)",
        ylabel="Contrast (%)",
        title="CW ODMR",
    )


def hold_laser(session, cfg):
    """Leave the AOM gate open until Enter, then close it.

    The board claim covers the whole wait, so the confocal live count stands
    down instead of programming the same PMOD and dropping the gate.  Enter,
    or Ctrl+C, closes the gate before the claim is released.
    """
    qd = session.qd
    with session.acquisition():
        try:
            qd.laser_on(copy(cfg))
            input("Laser on. Press Enter to turn it off. ")
        finally:
            qd.laser_off(copy(cfg))


def report(result):
    """Save, plot and print whatever the experiment returned."""
    if result is None:
        return None
    if isinstance(result, CountingResult):
        print(
            f"{result.kind}: {result.counts} counts in "
            f"{result.window_seconds * 1e3:.3f} ms x {result.reps} reps "
            f"= {result.rate_cps:.0f} cps ({result.rate_cps / 1e6:.4f} Mcps)"
        )
        return result
    plot_result(result)
    if result.fit:
        print("Fit:")
        for key, value in result.fit.items():
            print(f"  {key}: {value}")
    if result.saved_files:
        print("Saved:", result.saved_files)
    return result


def main():
    session = RFSoCSession().connect()
    qd = session.qd
    from qickdawg.finetimingsuite.counting_duration_fine_res import (
        CountingDurationFineRes,
    )
    from qickdawg.finetimingsuite.cpmg_xy_fine_res import CPMGXYFineRes
    from qickdawg.finetimingsuite.podmr_fine_res import PODMRFineRes
    from qickdawg.finetimingsuite.rabi_fine_res import RabiFineRes
    from qickdawg.finetimingsuite.t1_fine_res import T1FineRes

    # Shared pulsed settings.  Channels, thresholds and NQZ come from this bench.
    # Each block copies this object so a sweep does not leak into the next one.
    default_config = base_nv_config(session)
    default_config.mw_fMHz = 2846
    default_config.mw_gain = 32_767
    default_config.mw_pi2_ftns = 25
    default_config.mw_pi_ftns = 50
    default_config.mw_to_laser_delay_tns = 555
    default_config.relax_delay_tus = 2
    default_config.laser_on_tus = 6
    default_config.readout_reference_start_tus = 5
    default_config.readout_integration_tns = 633
    default_config.laser_readout_offset_tus = 1.159
    default_config.reps = 10_000
    default_config.get_reference = True

    result = None

    # 0. Laser on until Enter.  The confocal live count stands down, then resumes.
    # hold_laser(session, default_config)

    # 1. PL Intensity.  200_000 us is 0.2 s.  Uncomment live_pl to keep a trace
    # open until Ctrl+C.
    # cfg = copy(default_config)
    # cfg.readout_integration_tus = 200_000
    # cfg.reps = 1
    # cfg.relax_delay_tns = 50
    # result = acquire_counts(session, "PLIntensity", "PL_Intensity", cfg)
    # live_pl(session, cfg)

    # 2. Dark Counts
    # cfg = copy(default_config)
    # cfg.readout_integration_tus = 200_000
    # cfg.reps = 10
    # cfg.relax_delay_tns = 50
    # result = acquire_counts(session, "DarkCounts", "Dark_Counts", cfg)

    # 3. CW ODMR.  Uncomment live_cwodmr, and set reps to 900, to average passes.
    # cfg = copy(default_config)
    # cfg.readout_integration_tus = 213
    # cfg.relax_delay_tus = 1
    # cfg.mw_gain = 30_000
    # cfg.reps = 5_000
    # center_mhz, width_mhz = 2875, 75
    # cfg.add_linear_sweep(
    #     "mw", "fMHz",
    #     start=center_mhz - width_mhz,
    #     stop=center_mhz + width_mhz,
    #     delta=150 / 79,
    # )
    # with session.acquisition():
    #     data = qd.LockinODMR(cfg).acquire(progress=True)
    # result = lockin_result(cfg, data)
    # Instead of the acquire above: cfg.reps = 900, then average passes.
    # live_cwodmr(session, cfg)

    # 4. Pulsed ODMR
    # cfg = copy(default_config)
    # cfg.reps = 50_000
    # cfg.add_linear_sweep("mw", "fMHz", start=2800, stop=2950, delta=150 / 79)
    # cfg.mw_gain = 15_000
    # cfg.mw_pi_ftns = 50
    # with session.acquisition():
    #     data = PODMRFineRes(cfg).acquire(progress=True)
    # result = pulsed_result(cfg, data)

    # 5. Calibrate the readout window.  CountingDurationFineRes always takes the microwave-off readouts, so get_reference stays True.
    # cfg = copy(default_config)
    # cfg.mw_fMHz = 2898.58
    # cfg.mw_gain = 32_767
    # cfg.mw_pi_ftns = 204
    # cfg.readout_integration_tns = 100
    # cfg.reps = 10_000
    # cfg.get_reference = True
    # offsets = np.arange(40) * cfg.readout_integration_tns
    # signal_on = np.zeros(offsets.size)
    # signal_off = np.zeros(offsets.size)
    # with session.acquisition():
    #     for index, offset in enumerate(offsets):
    #         cfg.laser_readout_offset_tns = float(offset)
    #         data = CountingDurationFineRes(cfg).acquire(progress=False)
    #         signal_on[index] = float(_array(data, "signal1"))
    #         signal_off[index] = float(_array(data, "signal2"))
    #         print(
    #             f"readout window {index + 1}/{offsets.size} "
    #             f"offset {cfg.laser_readout_offset_tns:.0f} ns",
    #             end="\r",
    #             flush=True,
    #         )
    #     print()
    # result = window_result(cfg, offsets, signal_on, signal_off)

    # 6. Rabi.  Two nanoseconds per step: ten DAC samples.
    # cfg = copy(default_config)
    # cfg.mw_gain = 32_767
    # cfg.mw_fMHz = 2898.58
    # cfg.reps = 400_000
    # cfg.pre_init = True
    # cfg.get_reference = True
    # cfg.readout_integration_tns = 300
    # cfg.laser_readout_offset_tns = 500
    # cfg.laser_on_tus = 4.5
    # cfg.readout_reference_start_tus = 4.15
    # cfg.relax_delay_tns = 500
    # cfg.mw_to_laser_delay_tns = 0
    # cfg.add_linear_sweep("mw_duration", "ftns", start=0, stop=1008, delta=8)
    # with session.acquisition():
    #     data = RabiFineRes(cfg).acquire(progress=True)
    # result = rabi_result(cfg, data)

    # 7. Ramsey.  n_cpmg = 0 is free precession between two pi/2 pulses.
    # cfg = copy(default_config)
    # cfg.reps = 50_000
    # cfg.n_cpmg = 0
    # cfg.add_linear_sweep("tau", "ftns", start=100, stop=15_000, delta=100)
    # with session.acquisition():
    #     data = CPMGXYFineRes(cfg).acquire(progress=True)
    # result = coherence_result(cfg, data, kind="Ramsey")

    # 8. Hahn Echo.  n_cpmg = 1.  0.5 us to 2000 us.
    # cfg = copy(default_config)
    # cfg.reps = 50_000
    # cfg.n_cpmg = 1
    # cfg.add_exponential_sweep(
    #     "tau", "ftus", start=0.5, stop=2_000, scaling_factor="3/2"
    # )
    # with session.acquisition():
    #     data = CPMGXYFineRes(cfg).acquire(progress=True)
    # result = coherence_result(cfg, data, kind="Hahn_Echo")

    # 9. CPMG-N.  0.5 us to 500 us.
    # cfg = copy(default_config)
    # cfg.reps = 50_000
    # cfg.n_cpmg = 32
    # cfg.add_exponential_sweep(
    #     "tau", "ftus", start=0.5, stop=500, scaling_factor="3/2"
    # )
    # with session.acquisition():
    #     data = CPMGXYFineRes(cfg).acquire(progress=True)
    # result = coherence_result(cfg, data, kind="CPMG_32")

    # 10. T1.  1 us to 30 ms.  default_config already sets mw_pi_ftns and
    # mw_pi2_ftns; T1FineRes checks the pi/2 name and plays the pi pulse.
    # cfg = copy(default_config)
    # cfg.reps = 3_000
    # cfg.laser_on_tus = 50
    # cfg.readout_reference_start_tus = 40
    # cfg.add_exponential_sweep(
    #     "delay", "tus", start=1, stop=30_000, scaling_factor="9/8"
    # )
    # with session.acquisition():
    #     data = T1FineRes(cfg).acquire(progress=True)
    # result = t1_result(cfg, data)

    return report(result)


if __name__ == "__main__":
    try:
        main()
    except RFSoCBusyError as exc:
        sys.exit(f"RFSoC unavailable: {exc}")
    except KeyboardInterrupt:
        sys.exit("Stopped")
