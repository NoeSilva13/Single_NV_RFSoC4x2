"""Structural checks on the pulse sequence DEERSpectrumFineRes emits.

As for ConfocalFrame, the tProc calls are recorded through stubs instead of
being compiled, so the sequence can be checked without the board.
"""

import numpy as np
import pytest

import rfsoc.deer_program as deer
from rfsoc.deer_program import DEERSpectrumFineRes, deer_schedule
from rfsoc.diagnostics import check_second_generator

SAMPS_PER_CLK = 16
MAXV = 32_766


class FakeSocCfg(dict):
    def get_maxv(self, ch):
        return MAXV


SOCCFG = FakeSocCfg(gens=[
    {"samps_per_clk": SAMPS_PER_CLK, "f_dds": 4915.2},
    {"samps_per_clk": SAMPS_PER_CLK, "f_dds": 4915.2},
])


class FakeConfig(dict):
    __getattr__ = dict.__getitem__

    def __setattr__(self, key, value):
        self[key] = value


def deer_config(**overrides):
    cfg = FakeConfig(
        mw_channel=0,
        mw2_channel=1,
        mw_nqz=2,
        mw2_nqz=2,
        mw_freg=123,
        mw_gain=30_000,
        mw2_gain=20_000,
        mw_pi2_ftsamp=123,
        mw2_pi_ftsamp=250,
        tau_ftsamp=1_000,
        mw2_offset_ftsamp=0,
        mw2_start_fMHz=2800.0,
        mw2_end_fMHz=2950.0,
        nsweep_points=80,
        get_reference=True,
    )
    cfg.update(overrides)
    return cfg


class Register:
    def __init__(self, ch, name, asm):
        self.ch, self.name, self.asm = ch, name, asm

    def set_to(self, value, physical_unit=True):
        self.asm.append(("set_to", (self.ch, self.name, value), {}))


def recorded_program(cfg, monkeypatch):
    """Build the program with every QICK call recorded, then emit one body."""
    program = DEERSpectrumFineRes.__new__(DEERSpectrumFineRes)
    program.cfg = cfg
    program.soccfg = SOCCFG
    asm = []

    def record(call, *args, **kwargs):
        asm.append((call, args, kwargs))

    for name in (
        "check_cfg", "declare_gen", "setup_helper_registers", "setup_readout",
        "add_envelope", "default_pulse_registers", "set_pulse_registers",
        "add_sweep", "pre_init", "initialize_spin", "pulse", "sync_all",
        "nv_readout",
    ):
        setattr(program, name, lambda *a, _n=name, **k: record(_n, *a, **k))
    program.freq2reg = lambda f, gen_ch=0: ("freg", f, gen_ch)
    program.get_gen_reg = lambda ch, name: Register(ch, name, asm)
    monkeypatch.setattr(
        deer, "NVQickSweep",
        lambda prog, reg, start, stop, n: ("sweep", reg.ch, reg.name, start, stop, n),
    )

    program.initialize()
    start_body = len(asm)
    program.body()
    return program, asm[:start_body], asm[start_body:]


def test_pulses_land_on_the_echo_timeline_in_coarse_and_fine_parts():
    pulses = {p.name: p for p in deer_schedule(123, 250, 1_000, 40, SAMPS_PER_CLK)}
    pi_start = 123 + 1_000
    expected = {
        "sensor_pi2_a": 0,
        "sensor_pi": pi_start,
        "sensor_pi2_b": pi_start + 246 + 1_000,
        "target_pi": pi_start + 40,
    }
    for name, start in expected.items():
        assert pulses[name].start_samp == start
        assert pulses[name].coarse == start // SAMPS_PER_CLK
        assert pulses[name].fine == start % SAMPS_PER_CLK
    assert pulses["sensor_pi"].length_samp == 2 * 123


def test_the_envelope_carries_the_sub_cycle_offset():
    pulse = deer_schedule(10, 20, 50, 0, SAMPS_PER_CLK)[1]
    assert (pulse.coarse, pulse.fine) == (3, 12)
    data = pulse.envelope(MAXV)
    assert data.size == pulse.waveform_cycles * SAMPS_PER_CLK
    assert data.size >= 3 * SAMPS_PER_CLK
    np.testing.assert_array_equal(np.flatnonzero(data), np.arange(12, 12 + 20))


def test_a_tau_shorter_than_a_waveform_buffer_is_refused():
    with pytest.raises(ValueError, match="too short"):
        deer_schedule(10, 10, 0, 0, SAMPS_PER_CLK)


def test_a_target_pulse_before_the_echo_is_refused():
    with pytest.raises(ValueError, match="before the echo"):
        deer_schedule(10, 10, 100, -200, SAMPS_PER_CLK)


def test_both_generators_are_declared_and_the_sweep_steps_the_target_frequency(
    monkeypatch,
):
    _program, init, _body = recorded_program(deer_config(), monkeypatch)
    declared = [call[2] for call in init if call[0] == "declare_gen"]
    assert declared == [{"ch": 0, "nqz": 2}, {"ch": 1, "nqz": 2}]
    sweeps = [call[1][0] for call in init if call[0] == "add_sweep"]
    assert sweeps == [("sweep", 1, "freq", 2800.0, 2950.0, 80)]
    target_defaults = [
        call[2] for call in init
        if call[0] == "default_pulse_registers" and call[2]["ch"] == 1
    ]
    assert target_defaults[0]["freq"] == ("freg", 2800.0, 1)
    assert target_defaults[0]["waveform"] == "target_pi"
    envelopes = {call[2]["name"]: call[2]["ch"] for call in init if call[0] == "add_envelope"}
    assert envelopes == {
        "sensor_pi2_a": 0, "sensor_pi": 0, "sensor_pi2_b": 0, "target_pi": 1,
    }


def test_the_target_registers_are_never_rewritten_inside_the_body(monkeypatch):
    _program, _init, body = recorded_program(deer_config(), monkeypatch)
    rewritten = [
        call for call in body
        if call[0] == "set_pulse_registers" and call[2]["ch"] == 1
    ]
    assert rewritten == []


def test_each_point_reads_the_echo_with_and_without_the_target_pulse(monkeypatch):
    program, _init, body = recorded_program(deer_config(), monkeypatch)
    pulses = [call[2] for call in body if call[0] == "pulse"]
    one_echo = [{"ch": program._channel(p), "t": p.coarse} for p in program.schedule]
    assert pulses == one_echo + one_echo

    names = [call[0] for call in body]
    readouts = [i for i, name in enumerate(names) if name == "nv_readout"]
    gain_writes = [call for call in body if call[0] == "set_to"]
    assert len(readouts) == 2
    # Only the target generator is silenced for the reference echo.
    assert [call[1] for call in gain_writes] == [(1, "gain", 0), (1, "gain", 20_000)]
    zero, restore = (body.index(call) for call in gain_writes)
    assert readouts[0] < zero < readouts[1] < restore


def test_deer_without_the_reference_echo_is_refused(monkeypatch):
    with pytest.raises(ValueError, match="get_reference"):
        recorded_program(deer_config(get_reference=False), monkeypatch)


def test_a_gain_out_of_range_is_refused(monkeypatch):
    with pytest.raises(ValueError, match="mw2_gain"):
        recorded_program(deer_config(mw2_gain=40_000), monkeypatch)


def test_the_second_generator_must_exist_and_time_like_the_first():
    assert check_second_generator(SOCCFG["gens"], 0, 1)["channel"] == 1
    with pytest.raises(ValueError, match="not in this firmware"):
        check_second_generator(SOCCFG["gens"][:1], 0, 1)
    mismatched = [SOCCFG["gens"][0], {"samps_per_clk": 8, "f_dds": 4915.2}]
    with pytest.raises(ValueError, match="samps_per_clk"):
        check_second_generator(mismatched, 0, 1)
