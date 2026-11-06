"""DEER spectrum: a Hahn echo on the sensor NV, a pi pulse on a second tone.

The sensor echo ``pi/2 - tau - pi - tau - pi/2`` plays on ``mw_channel`` at a
fixed frequency.  A pi pulse on ``mw2_channel`` lands next to the sensor's pi
pulse and flips the target spin.  When the second tone is resonant with the
target, the dipolar field the sensor sees changes sign halfway through the
echo, the refocusing fails, and the echo loses photoluminescence.  Sweeping
the second tone's frequency in the FPGA traces out the target's spectrum.

Each point takes four readouts, in the order ``ReadoutHelpers`` already names
them: the echo with the target pulse (``signal1`` / ``reference1``), then the
same echo with the target generator's gain at zero (``signal2`` /
``reference2``).  The second pair is the Hahn echo the DEER signal is
normalised against, which is why ``get_reference`` is required.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

try:
    from qickdawg.finetimingsuite.readout_helpers import ReadoutHelpers
    from qickdawg.nvpulsing.nvaverageprogram import NVAveragerProgram
    from qickdawg.nvpulsing.nvqicksweep import NVQickSweep
except ImportError:  # Allows schedule/program tests without the hardware package.
    class ReadoutHelpers:  # type: ignore
        pass

    class NVAveragerProgram:  # type: ignore
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("QICK-DAWG is required to compile DEERSpectrumFineRes")

    NVQickSweep = None  # type: ignore

MAX_GAIN = 32_767
# QICK refuses arbitrary waveforms shorter than three fabric cycles.
MIN_WAVEFORM_CYCLES = 3


@dataclass(frozen=True)
class ScheduledPulse:
    """One pulse placed on the echo timeline, in DAC samples from its start."""

    name: str
    role: str  # "sensor" or "target"
    start_samp: int
    length_samp: int
    samps_per_clk: int

    @property
    def coarse(self):
        return self.start_samp // self.samps_per_clk

    @property
    def fine(self):
        return self.start_samp % self.samps_per_clk

    @property
    def waveform_cycles(self):
        cycles = -(-(self.fine + self.length_samp) // self.samps_per_clk)
        return max(cycles, MIN_WAVEFORM_CYCLES)

    def envelope(self, maxv):
        data = np.zeros(self.waveform_cycles * self.samps_per_clk)
        data[self.fine:self.fine + self.length_samp] = maxv
        return data


def deer_schedule(
    pi2_ftsamp, mw2_pi_ftsamp, tau_ftsamp, mw2_offset_ftsamp, samps_per_clk
):
    """Place the three sensor pulses and the target pi pulse.

    ``tau`` runs from the end of one sensor pulse to the start of the next, as
    in ``CPMGXYFineRes``, and the sensor pi pulse is twice ``pi2`` as it is
    there.  ``mw2_offset`` moves the target pulse from the start of the sensor
    pi pulse; it is also where the skew between the two DACs is taken out.

    Each start splits into a tProc delay and a sub-cycle offset baked into the
    envelope, because ``tau`` is fixed for the whole sweep.
    """
    pi2, tau = int(pi2_ftsamp), int(tau_ftsamp)
    target_len, offset = int(mw2_pi_ftsamp), int(mw2_offset_ftsamp)
    if pi2 < 1 or target_len < 1:
        raise ValueError("mw_pi2 and mw2_pi must each be at least one DAC sample")
    if tau < 0:
        raise ValueError("tau must not be negative")
    pi_start = pi2 + tau
    sensor = [
        ScheduledPulse("sensor_pi2_a", "sensor", 0, pi2, samps_per_clk),
        ScheduledPulse("sensor_pi", "sensor", pi_start, 2 * pi2, samps_per_clk),
        ScheduledPulse(
            "sensor_pi2_b", "sensor", pi_start + 2 * pi2 + tau, pi2, samps_per_clk
        ),
    ]
    if pi_start + offset < 0:
        raise ValueError(
            f"mw2_offset {offset} samples puts the target pulse before the echo starts"
        )
    target = ScheduledPulse(
        "target_pi", "target", pi_start + offset, target_len, samps_per_clk
    )
    for earlier, later in zip(sensor, sensor[1:]):
        if later.coarse < earlier.coarse + earlier.waveform_cycles:
            raise ValueError(
                f"tau {tau} samples is too short: {later.name} would start "
                f"inside the waveform buffer of {earlier.name}"
            )
    return sensor + [target]


class DEERSpectrumFineRes(ReadoutHelpers, NVAveragerProgram):
    """Hahn echo on ``mw_channel`` with a swept pi pulse on ``mw2_channel``."""

    required_cfg = [
        # Channels and pmods
        "mw_channel",
        "mw2_channel",
        "adc_channel",
        "laser_gate_pmod",

        # Sensor echo
        "mw_pi2_ftsamp",
        "mw_nqz",
        "mw_freg",
        "mw_gain",
        "tau_ftsamp",

        # Target pi pulse and its frequency sweep
        "mw2_pi_ftsamp",
        "mw2_nqz",
        "mw2_gain",
        "mw2_offset_ftsamp",
        "mw2_start_fMHz",
        "mw2_end_fMHz",
        "nsweep_points",

        # Readout and delays
        "mw_to_laser_delay_treg",
        "relax_delay_treg",
        "laser_on_treg",
        "readout_reference_start_treg",
        "readout_integration_treg",
        "laser_readout_offset_treg",

        # Other
        "reps",
        "pre_init",
        "get_reference",
    ]

    def initialize(self):
        self.check_cfg()
        cfg = self.cfg
        if not cfg.get_reference:
            raise ValueError(
                "DEER needs get_reference=True: the echo without the target "
                "pulse is what the DEER signal is normalised against"
            )
        for name in ("mw_gain", "mw2_gain"):
            if not 0 <= int(cfg[name]) <= MAX_GAIN:
                raise ValueError(f"{name} must be within 0..{MAX_GAIN}")
        if cfg.mw_channel == cfg.mw2_channel:
            raise ValueError("the sensor and target tones need different generators")

        gens = self.soccfg["gens"]
        self.samps_per_clk = gens[cfg.mw_channel]["samps_per_clk"]
        if gens[cfg.mw2_channel]["samps_per_clk"] != self.samps_per_clk:
            raise ValueError("both generators must share samples per clock")

        self.declare_gen(ch=cfg.mw_channel, nqz=cfg.mw_nqz)
        self.declare_gen(ch=cfg.mw2_channel, nqz=cfg.mw2_nqz)
        self.setup_helper_registers(cfg.mw_channel)
        self.setup_readout()

        self.schedule = deer_schedule(
            cfg.mw_pi2_ftsamp,
            cfg.mw2_pi_ftsamp,
            cfg.tau_ftsamp,
            cfg.mw2_offset_ftsamp,
            self.samps_per_clk,
        )
        for pulse in self.schedule:
            ch = self._channel(pulse)
            data = pulse.envelope(self.soccfg.get_maxv(ch))
            self.add_envelope(ch=ch, name=pulse.name, idata=data, qdata=data)

        self.default_pulse_registers(
            ch=cfg.mw_channel,
            style="arb",
            freq=cfg.mw_freg,
            gain=cfg.mw_gain,
            phase=0,
        )
        # The target generator plays one waveform, so its registers are written
        # once here and never again: rewriting them per pulse would overwrite
        # the frequency the sweep steps.
        self.default_pulse_registers(
            ch=cfg.mw2_channel,
            style="arb",
            freq=self.freq2reg(cfg.mw2_start_fMHz, gen_ch=cfg.mw2_channel),
            gain=cfg.mw2_gain,
            phase=0,
            waveform="target_pi",
        )
        self.set_pulse_registers(ch=cfg.mw2_channel)

        self.mw2_gain_register = self.get_gen_reg(cfg.mw2_channel, "gain")
        self.mw2_frequency_register = self.get_gen_reg(cfg.mw2_channel, "freq")
        self.add_sweep(NVQickSweep(
            self,
            self.mw2_frequency_register,
            cfg.mw2_start_fMHz,
            cfg.mw2_end_fMHz,
            cfg.nsweep_points,
        ))

        self.pre_init()

    def _channel(self, pulse):
        return self.cfg.mw2_channel if pulse.role == "target" else self.cfg.mw_channel

    def program_pulses(self):
        """Queue the echo and the target pulse against one time reference."""
        for pulse in self.schedule:
            if pulse.role == "sensor":
                self.set_pulse_registers(ch=self.cfg.mw_channel, waveform=pulse.name)
            self.pulse(ch=self._channel(pulse), t=pulse.coarse)
        self.sync_all()

    def body(self):
        self.initialize_spin()
        self.program_pulses()
        self.nv_readout()

        self.mw2_gain_register.set_to(0, physical_unit=False)
        self.initialize_spin()
        self.program_pulses()
        self.nv_readout()
        self.mw2_gain_register.set_to(self.cfg.mw2_gain, physical_unit=False)

    def acquire(self, raw_data=False, *arg, **kwarg):
        # self.acquire --> ReadoutHelpers.acquire --> NVAveragerProgram.acquire
        return super().acquire(raw_data=raw_data, sweep_param="mw2_fMHz", *arg, **kwarg)
