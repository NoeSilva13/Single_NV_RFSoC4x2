import numpy as np
import pandas as pd
import pytest

from rfsoc.experiments.base import (
    CountingResult,
    ExperimentResult,
    configuration_record,
    normalized_result,
)
from rfsoc.experiments.cpmg import _fit_decay, _fit_ramsey, ramsey_spectrum
from rfsoc.experiments.io import fitted_curve, plot_result, save_result
from rfsoc.experiments.odmr import _fit_resonance, lockin_result
from rfsoc.experiments.rabi import _fit_rabi
from rfsoc.experiments.readout_window import _fit_window
from rfsoc.experiments.t1 import _fit_t1


class FakeConfig(dict):
    """Stands in for NVConfiguration where only the written keys matter."""

    def __setitem__(self, key, value):
        super().__setitem__(key, value)

    def __setattr__(self, key, value):
        self[key] = value

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


def sweep_result(kind, x, y, x_unit="ns"):
    """An ExperimentResult carrying a synthetic curve to fit."""
    zeros = np.zeros_like(np.asarray(x, dtype=float))
    return ExperimentResult(
        kind=kind,
        x_name="x",
        x_unit=x_unit,
        x=np.asarray(x, dtype=float),
        signal_counts=zeros,
        reference_counts=zeros,
        signal_rate_cps=zeros,
        reference_rate_cps=zeros,
        contrast=np.asarray(y, dtype=float),
    )


def test_native_contrast_uses_laser_normalized_signal_and_reference():
    data = {
        "signal1": np.array([80, 45]),
        "reference1": np.array([100, 50]),
        "signal2": np.array([100, 50]),
        "reference2": np.array([100, 50]),
    }
    result = normalized_result(
        kind="Rabi",
        x_name="duration",
        x_unit="ns",
        x=[0, 1],
        data=data,
        integration_seconds=1e-3,
        reps=10,
        requested={},
        executed={},
    )
    np.testing.assert_allclose(result.contrast, [0.2, 0.1])
    np.testing.assert_allclose(result.signal_rate_cps, [8000, 4500])


def test_t1_result_is_signal_over_reference():
    data = {
        "signal1": np.array([50]),
        "reference1": np.array([100]),
        "signal2": np.array([100]),
        "reference2": np.array([100]),
    }
    result = normalized_result(
        kind="T1",
        x_name="delay",
        x_unit="ns",
        x=[1],
        data=data,
        integration_seconds=1,
        reps=1,
        requested={},
        executed={},
        t1_ratio=True,
    )
    np.testing.assert_allclose(result.contrast, [0.5])


def test_a_sweep_without_the_mw_off_reference_falls_back_to_the_laser_reference():
    # get_reference=False halves the sweep by dropping the MW-off readouts, so
    # signal2/reference2 are simply absent from what the program returns.
    data = {"signal1": np.array([80.0, 45.0]), "reference1": np.array([100.0, 50.0])}
    result = normalized_result(
        kind="Rabi",
        x_name="duration",
        x_unit="ns",
        x=[0, 1],
        data=data,
        integration_seconds=1e-3,
        reps=10,
        requested={},
        executed={},
    )
    np.testing.assert_allclose(result.contrast, [0.8, 0.9])
    np.testing.assert_allclose(result.reference_counts, [100.0, 50.0])


def test_hold_laser_keeps_the_claim_until_the_gate_is_closed(monkeypatch):
    from run_odmr_experiments import hold_laser

    events = []

    class Board:
        def __init__(self):
            self.qd = self

        def acquisition(self):
            board = self

            class Claim:
                def __enter__(self):
                    events.append("claim")
                    return board

                def __exit__(self, *args):
                    events.append("release")

            return Claim()

        def laser_on(self, cfg):
            events.append(("on", cfg["marker"]))
            cfg["marker"] = "mutated"

        def laser_off(self, cfg):
            events.append(("off", cfg["marker"]))

    monkeypatch.setattr("builtins.input", lambda prompt: "")
    cfg = {"marker": "shared"}
    hold_laser(Board(), cfg)
    assert events == ["claim", ("on", "shared"), ("off", "shared"), "release"]
    assert cfg["marker"] == "shared"


def test_hold_laser_closes_the_gate_when_the_wait_is_interrupted(monkeypatch):
    from run_odmr_experiments import hold_laser

    events = []

    class Board:
        def __init__(self):
            self.qd = self

        def acquisition(self):
            board = self

            class Claim:
                def __enter__(self):
                    events.append("claim")
                    return board

                def __exit__(self, *args):
                    events.append("release")

            return Claim()

        def laser_on(self, cfg):
            events.append("on")

        def laser_off(self, cfg):
            events.append("off")

    def interrupt(prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    with pytest.raises(KeyboardInterrupt):
        hold_laser(Board(), {"marker": "shared"})
    assert events == ["claim", "on", "off", "release"]


def test_a_counted_point_reports_a_rate_per_window_and_rep():
    result = CountingResult(
        kind="PL_Intensity", counts=6_000, window_seconds=0.2, reps=3
    )
    assert result.rate_cps == pytest.approx(10_000)


def test_configuration_record_keeps_qickdawg_names_and_skips_objects():
    cfg = FakeConfig()
    cfg.readout_integration_tns = 633
    cfg.relax_delay_tus = 2
    cfg.soccfg = object()
    record = configuration_record(cfg)
    assert record["readout_integration_tns"] == 633
    assert record["relax_delay_tus"] == 2
    assert "soccfg" not in record
    assert "readout_ns" not in record


def test_a_sweep_result_records_the_configuration_in_qickdawg_names():
    cfg = FakeConfig()
    cfg.readout_integration_tns = 100_000
    cfg.reps = 10
    cfg.mw_gain = 5_000
    data = {
        "signal": np.array([80.0, 40.0]),
        "reference": np.array([100.0, 100.0]),
        "frequencies": np.array([2800.0, 2950.0]),
    }
    result = lockin_result(cfg, data)
    assert result.requested["readout_integration_tns"] == 100_000
    assert "readout_ns" not in result.requested
    assert result.executed["frequencies"] == [2800.0, 2950.0]
    np.testing.assert_allclose(result.x, [2.8e9, 2.95e9])


def test_the_rabi_fit_recovers_the_pi_pulse_from_a_damped_oscillation():
    durations_ns = np.linspace(0, 500, 251)
    contrast = (
        -0.15
        * np.cos(2 * np.pi * durations_ns / 100)
        * np.exp(-durations_ns / 800)
        + 0.15
    )
    fit = _fit_rabi(sweep_result("Rabi", durations_ns, contrast)).fit
    assert fit["rabi_period_ns"] == pytest.approx(100, rel=0.01)
    assert fit["mw_pi_ftns"] == pytest.approx(50, rel=0.01)
    assert fit["mw_pi2_ftns"] == pytest.approx(25, rel=0.01)
    assert fit["rabi_frequency_hz"] == pytest.approx(10e6, rel=0.01)


def test_the_ramsey_fit_recovers_the_detuning_and_t2_star():
    taus_ns = np.linspace(100, 15_000, 150)
    contrast = (
        0.1 * np.cos(2 * np.pi * 2e-3 * taus_ns) * np.exp(-taus_ns / 3_000) + 0.2
    )
    result = _fit_ramsey(sweep_result("Ramsey", taus_ns, contrast))
    assert result.fit["detuning_hz"] == pytest.approx(2e6, rel=0.02)
    assert result.fit["t2_star_seconds"] == pytest.approx(3e-6, rel=0.1)
    frequencies_hz, spectrum, peaks_hz = ramsey_spectrum(result)
    assert spectrum.size == frequencies_hz.size
    assert peaks_hz[0] == pytest.approx(2e6, rel=0.2)


def test_the_hahn_echo_fit_recovers_t2():
    taus_ns = np.geomspace(500, 2e6, 40)
    contrast = 0.2 * np.exp(-taus_ns / 2e5) + 0.05
    fit = _fit_decay(sweep_result("Hahn_Echo", taus_ns, contrast)).fit
    assert fit["t2_seconds"] == pytest.approx(2e-4, rel=0.01)


def test_the_odmr_fit_recovers_the_resonance_and_the_linewidth():
    frequencies_hz = np.linspace(2.80e9, 2.95e9, 80)
    contrast = 0.2 / (1 + ((frequencies_hz - 2.87e9) / 5e6) ** 2) + 0.01
    fit = _fit_resonance(
        sweep_result("CW_ODMR", frequencies_hz, contrast, x_unit="Hz")
    ).fit
    assert fit["resonance_hz"] == pytest.approx(2.87e9, abs=1e6)
    assert fit["linewidth_hz"] == pytest.approx(10e6, rel=0.05)


def test_the_deer_spectrum_is_the_echo_the_target_pulse_destroys():
    from rfsoc.experiments.deer import deer_spectrum_result

    cfg = FakeConfig()
    cfg.readout_integration_tns = 300
    cfg.reps = 1_000
    frequencies_mhz = np.linspace(2800, 2950, 80)
    # The echo alone sits at 1000 counts; on resonance the target pulse costs
    # it a fifth of that.
    lost = 0.2 / (1 + ((frequencies_mhz - 2890) / 3) ** 2)
    hahn = np.full(frequencies_mhz.size, 1000.0)
    laser = np.full(frequencies_mhz.size, 1200.0)
    data = {
        "signal1": hahn * (1 - lost),
        "reference1": laser,
        "signal2": hahn,
        "reference2": laser,
        "mw2_fMHz": frequencies_mhz,
    }
    result = deer_spectrum_result(cfg, data)
    assert result.kind == "DEER_Spectrum"
    np.testing.assert_allclose(result.contrast, lost)
    assert result.fit["resonance_hz"] == pytest.approx(2.890e9, abs=0.2e6)
    assert result.fit["linewidth_hz"] == pytest.approx(6e6, rel=0.05)
    assert result.executed["mw2_fMHz"][0] == pytest.approx(2800)
    x, y, label = fitted_curve(result)
    assert x.size == y.size
    assert "resonance" in label


def test_a_coupling_converts_to_the_distance_of_two_electron_spins():
    from rfsoc.experiments.deer import dipolar_distance_nm

    assert dipolar_distance_nm(52.04e6) == pytest.approx(1.0)
    # 52 kHz is ten times farther: the coupling falls as r**-3.
    assert dipolar_distance_nm(52.04e3) == pytest.approx(10.0)
    assert dipolar_distance_nm(52.04e3, angular_factor=2) == pytest.approx(
        10.0 * 2 ** (1 / 3)
    )


def test_the_deer_tau_fit_recovers_the_coupling_and_the_distance():
    from rfsoc.experiments.deer import deer_tau_result

    cfg = FakeConfig()
    cfg.readout_integration_tns = 300
    cfg.reps = 1_000
    taus_ns = np.arange(200, 20_000, 200.0)
    coupling_mhz = 0.2
    lost = 0.3 / 2 * (
        1 - np.cos(2 * np.pi * coupling_mhz * 1e-3 * taus_ns) * np.exp(-taus_ns / 30_000)
    )
    hahn = np.full(taus_ns.size, 1000.0)
    laser = np.full(taus_ns.size, 1200.0)
    readouts = {
        "signal1": hahn * (1 - lost),
        "reference1": laser,
        "signal2": hahn,
        "reference2": laser,
    }
    result = deer_tau_result(cfg, taus_ns, readouts)
    assert result.kind == "DEER_Tau"
    np.testing.assert_allclose(result.contrast, lost)
    fit = result.fit
    assert fit["dipolar_coupling_hz"] == pytest.approx(0.2e6, rel=0.01)
    assert fit["decay_ns"] == pytest.approx(30_000, rel=0.05)
    assert fit["distance_nm"] == pytest.approx((52.04 / 0.2) ** (1 / 3), rel=0.01)
    assert fit["max_distance_nm"] > fit["distance_nm"]
    x, y, label = fitted_curve(result)
    assert x.size == y.size
    assert "nu_dd" in label


def test_the_deer_tau_walk_holds_the_target_and_steps_tau(monkeypatch):
    from run_odmr_experiments import deer_tau_walk

    claims = []

    class Board:
        def acquisition(self):
            class Claim:
                def __enter__(self):
                    claims.append("claim")

                def __exit__(self, *args):
                    claims.append("release")

            return Claim()

    seen = []

    class Program:
        def __init__(self, cfg):
            seen.append(
                (cfg.tau_ftns, cfg.mw2_start_fMHz, cfg.mw2_end_fMHz, cfg.nsweep_points)
            )
            self.tau = cfg.tau_ftns

        def acquire(self, progress=False):
            # Two identical frequency points per acquire, averaged by the walk.
            return {
                "signal1": np.array([self.tau, self.tau + 2]),
                "reference1": np.array([10.0, 10.0]),
                "signal2": np.array([20.0, 20.0]),
                "reference2": np.array([30.0, 30.0]),
            }

    cfg = FakeConfig()
    taus, readouts = deer_tau_walk(Board(), cfg, [100, 300], 2890.0, program=Program)
    assert claims == ["claim", "release"]
    assert seen == [(100.0, 2890.0, 2890.0, 2), (300.0, 2890.0, 2890.0, 2)]
    np.testing.assert_allclose(taus, [100, 300])
    np.testing.assert_allclose(readouts["signal1"], [101, 301])
    np.testing.assert_allclose(readouts["reference2"], [30, 30])


def test_the_t1_fit_recovers_t1():
    delays_ns = np.geomspace(1e3, 3e7, 50)
    contrast = 0.5 * np.exp(-delays_ns * 1e-9 / 5e-3) + 0.5
    fit = _fit_t1(sweep_result("T1", delays_ns, contrast)).fit
    assert fit["t1_seconds"] == pytest.approx(5e-3, rel=0.02)
    assert fit["stretch"] == pytest.approx(1.0, rel=0.05)


def test_the_readout_window_fit_recommends_the_window_it_measured():
    offsets_ns = np.arange(0, 4_000, 100.0)
    photoluminescence_cps = 1e5 * (1 - np.exp(-offsets_ns / 120))
    contrast = 0.3 * np.exp(-offsets_ns / 400)
    # Before the laser has turned on there is no photoluminescence to compare,
    # which is exactly the region the fit has to stay out of.
    contrast[photoluminescence_cps < 0.05 * photoluminescence_cps.max()] = 0.0
    fit = _fit_window(offsets_ns, contrast, photoluminescence_cps)
    assert fit["tau_ns"] == pytest.approx(400, rel=0.02)
    assert fit["recommended_readout_integration_tns"] == pytest.approx(400, rel=0.02)
    assert fit["recommended_laser_on_tns"] == pytest.approx(2_000, rel=0.02)
    assert fit["recommended_laser_readout_offset_tns"] == pytest.approx(200)


def test_a_fit_that_cannot_converge_is_reported_as_no_fit():
    flat = sweep_result("Hahn_Echo", np.arange(10.0), np.zeros(10))
    assert _fit_decay(flat).fit is None


def test_the_fitted_curve_of_a_kind_is_drawable_through_the_registry():
    durations_ns = np.linspace(0, 500, 251)
    result = _fit_rabi(
        sweep_result(
            "Rabi",
            durations_ns,
            -0.15 * np.cos(2 * np.pi * durations_ns / 100) + 0.15,
        )
    )
    x, y, label = fitted_curve(result)
    assert x.size == y.size
    assert "mw_pi_ftns" in label


def test_the_figure_draws_contrast_as_a_percentage(monkeypatch):
    import matplotlib.pyplot as plt

    closed = []
    close_figure = plt.close
    monkeypatch.setattr("rfsoc.experiments.io.plt.show", lambda: None)
    monkeypatch.setattr("rfsoc.experiments.io.plt.close", lambda fig: closed.append(fig))
    result = sweep_result(
        "CW_ODMR", np.array([1e9, 3e9]), np.array([0.04, 0.02]), x_unit="Hz"
    )
    result.fit = {
        "amplitude": -0.03,
        "resonance_hz": 2e9,
        "linewidth_hz": 1e7,
        "offset": 0.01,
    }
    plot_result(result, save=False, show=False)
    ax_y = closed[0].axes[1]
    assert ax_y.get_ylabel() == "Contrast (%)"
    np.testing.assert_allclose(ax_y.lines[0].get_ydata(), [4.0, 2.0])
    # Off resonance the Lorentzian sits on the 0.01 offset, drawn as 1%.
    assert ax_y.lines[1].get_ydata().max() == pytest.approx(1.0, abs=0.01)

    t1 = sweep_result("T1", np.array([1.0, 2.0]), np.array([1.2, 0.8]))
    plot_result(t1, save=False, show=False)
    ax_t1 = closed[1].axes[1]
    assert ax_t1.get_ylabel() == "Signal/reference"
    np.testing.assert_allclose(ax_t1.lines[0].get_ydata(), [1.2, 0.8])

    for fig in closed:
        close_figure(fig)


def test_save_result_writes_configuration_in_the_csv_header(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "rfsoc.experiments.io._next_stem",
        lambda kind: str(tmp_path / f"001_{kind}"),
    )
    result = ExperimentResult(
        kind="Rabi",
        x_name="MW_duration",
        x_unit="ns",
        x=np.array([4.0, 6.0]),
        signal_counts=np.array([1.0, 2.0]),
        reference_counts=np.array([3.0, 4.0]),
        signal_rate_cps=np.array([10.0, 20.0]),
        reference_rate_cps=np.array([30.0, 40.0]),
        contrast=np.array([0.1, 0.2]),
        requested={"laser_on_tns": 6_000, "mw_duration_ftns": list(range(249))},
        executed={"laser_on_tns": 6_000, "reps": 10_000},
        fit={"mw_pi_ftns": 50.0},
    )
    save_result(result)
    text = (tmp_path / "001_Rabi.csv").read_text(encoding="utf-8")
    assert text.startswith("# Measurement Time:")
    assert "# Kind: Rabi" in text
    assert "# Requested:" in text
    assert "#   laser_on_tns: 6000" in text
    assert "#   mw_duration_ftns: 0 to 248 (249 points)" in text
    assert "# Executed:" in text
    assert "# Fit:" in text
    assert "#   mw_pi_ftns: 50.0" in text
    table = pd.read_csv(tmp_path / "001_Rabi.csv", comment="#")
    assert list(table.columns) == [
        "MW_duration_ns",
        "Signal_counts",
        "Reference_counts",
        "Signal_cps",
        "Reference_cps",
        "Contrast",
    ]
    np.testing.assert_allclose(table["Contrast"], [0.1, 0.2])
