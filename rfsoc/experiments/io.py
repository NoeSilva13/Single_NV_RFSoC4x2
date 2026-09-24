"""Versioned persistence, plotting and live windows for RFSoC experiments."""

from __future__ import annotations

import json
import os
import time

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common.utils import experiment_data_root
from .cpmg import fitted_curve as _cpmg_curve, ramsey_spectrum
from .odmr import fitted_curve as _odmr_curve
from .rabi import fitted_curve as _rabi_curve
from .readout_window import fitted_curve as _window_curve
from .t1 import fitted_curve as _t1_curve


FORMAT_VERSION = 1


def _scalar(value):
    if isinstance(value, np.generic):
        return value.item()
    return value


def _format_csv_value(value):
    """Render a metadata value for a ``#`` comment line.

    Sweep axes are summarised as ``start to end (N points)`` so a 150-point
    frequency list does not become a comment longer than the table it labels.
    The executed axis is already the first data column.
    """
    if isinstance(value, (list, tuple, np.ndarray)):
        arr = np.asarray(value)
        if arr.size == 0:
            return "[]"
        if arr.ndim != 1 or arr.size > 4:
            return f"{_scalar(arr.flat[0])} to {_scalar(arr.flat[-1])} ({arr.size} points)"
        return ", ".join(str(_scalar(item)) for item in arr.tolist())
    return _scalar(value)


def _csv_header_lines(result):
    lines = [
        f"# Measurement Time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"# Kind: {result.kind}",
    ]
    for section, payload in (
        ("Requested", result.requested),
        ("Executed", result.executed),
        ("Fit", result.fit or {}),
    ):
        if not payload:
            continue
        lines.append(f"# {section}:")
        for key, value in payload.items():
            lines.append(f"#   {key}: {_format_csv_value(value)}")
    lines.append("#")
    return lines


def _next_stem(kind):
    day = time.strftime("%m%d%y")
    folder = os.path.join(experiment_data_root(), day, f"RFSoC_{kind}")
    os.makedirs(folder, exist_ok=True)
    existing = [
        name for name in os.listdir(folder)
        if name.startswith(day) and name.endswith(".npz")
    ]
    return os.path.join(folder, f"{day}{len(existing) + 1:03d}_{kind}")


def save_result(result):
    stem = _next_stem(result.kind)
    metadata = {
        "format_version": FORMAT_VERSION,
        "kind": result.kind,
        "x_name": result.x_name,
        "x_unit": result.x_unit,
        "requested": result.requested,
        "executed": result.executed,
        "fit": result.fit,
    }
    npz_path = stem + ".npz"
    np.savez_compressed(
        npz_path,
        x=result.x,
        signal_counts=result.signal_counts,
        reference_counts=result.reference_counts,
        signal_rate_cps=result.signal_rate_cps,
        reference_rate_cps=result.reference_rate_cps,
        contrast=result.contrast,
        metadata_json=json.dumps(metadata, default=float),
    )
    csv_path = stem + ".csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        handle.write("\n".join(_csv_header_lines(result)) + "\n")
        pd.DataFrame(
            {
                f"{result.x_name}_{result.x_unit}": result.x,
                "Signal_counts": result.signal_counts,
                "Reference_counts": result.reference_counts,
                "Signal_cps": result.signal_rate_cps,
                "Reference_cps": result.reference_rate_cps,
                "Contrast": result.contrast,
            }
        ).to_csv(handle, index=False)
    result.saved_files.update(npz=npz_path, csv=csv_path)
    return result


def fitted_curve(result):
    """Ask the experiment that produced *result* to sample its own fit.

    Each experiment keeps its model next to its acquisition, so the plot asks
    for the curve instead of knowing how any of them are shaped.  Returns
    ``(x, y, label)`` or ``None`` when there is nothing fitted to draw.
    """
    kind = result.kind
    if kind in ("CW_ODMR", "Pulsed_ODMR"):
        return _odmr_curve(result)
    if kind == "Rabi":
        return _rabi_curve(result)
    if kind in ("Ramsey", "Hahn_Echo") or kind.startswith("CPMG_"):
        return _cpmg_curve(result)
    if kind == "T1":
        return _t1_curve(result)
    if kind == "Readout_Window":
        return _window_curve(result)
    return None


def _contrast_for_plot(kind, contrast):
    """Scale a stored contrast onto the axis the figure should show.

    The files and the fits keep a fraction (0.04 is a 4% dip).  T1 is a
    signal/reference ratio near one, so that axis stays in those units.
    """
    y = np.asarray(contrast, dtype=float)
    if kind == "T1":
        return y, "Signal/reference"
    if kind == "Readout_Window":
        return y * 100, "Spin contrast (%)"
    return y * 100, "Contrast (%)"


def plot_result(result, save=True, show=True):
    spectrum = None
    if result.kind == "Ramsey":
        frequencies_hz, amplitudes, peaks_hz = ramsey_spectrum(result)
        if amplitudes.size:
            spectrum = (frequencies_hz, amplitudes, peaks_hz)

    fig = plt.figure(figsize=(8, 10) if spectrum else (8, 7))
    if spectrum:
        ax_rates, ax_y, ax_fft = fig.subplots(3, 1)
    else:
        ax_rates, ax_y = fig.subplots(2, 1)
        ax_fft = None
    ax_rates.sharex(ax_y)
    ax_rates.tick_params(labelbottom=False)

    ax_rates.plot(result.x, result.reference_rate_cps, label="reference")
    ax_rates.plot(result.x, result.signal_rate_cps, label="signal")
    ax_rates.set_ylabel("Count rate (cps)")
    ax_rates.legend()
    ax_rates.grid(True, alpha=0.3)

    plotted, ylabel = _contrast_for_plot(result.kind, result.contrast)
    ax_y.plot(result.x, plotted, "o", markersize=3, label="measured")
    ax_y.set_xlabel(f"{result.x_name} ({result.x_unit})")
    ax_y.set_ylabel(ylabel)
    ax_y.grid(True, alpha=0.3)

    curve = fitted_curve(result)
    if curve is not None:
        fit_x, fit_y, label = curve
        fit_y, _ = _contrast_for_plot(result.kind, fit_y)
        ax_y.plot(fit_x, fit_y, "-", color="tab:red", label="fit")
        ax_y.text(
            0.02,
            0.04,
            label,
            transform=ax_y.transAxes,
            fontsize=9,
            va="bottom",
            ha="left",
            bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.8},
        )
    ax_y.legend(loc="upper right")

    if ax_fft is not None:
        frequencies_hz, amplitudes, peaks_hz = spectrum
        ax_fft.plot(frequencies_hz / 1e6, amplitudes)
        for peak_hz in peaks_hz[:3]:
            ax_fft.axvline(peak_hz / 1e6, color="tab:red", alpha=0.4, linestyle="--")
        ax_fft.set_xlabel("Precession frequency (MHz)")
        ax_fft.set_ylabel("Amplitude (a.u.)")
        ax_fft.grid(True, alpha=0.3)

    fig.suptitle(f"RFSoC4x2 {result.kind}")
    fig.tight_layout()
    if save:
        if not result.saved_files:
            save_result(result)
        pdf_path = os.path.splitext(result.saved_files["npz"])[0] + ".pdf"
        fig.savefig(pdf_path)
        result.saved_files["pdf"] = pdf_path
    if show:
        plt.show()
    else:
        plt.close(fig)
    return result


def live_scalar(point, *, ylabel, title, interval=0.1, history=300):
    """Trace a scalar measurement until the window is closed or Ctrl+C is hit.

    *point* is called for each sample and returns ``None`` when the board is
    busy with a scan or an experiment; those samples become gaps in the trace,
    so the window shows that it stood down instead of quietly interpolating
    over it.
    """
    plt.ion()
    fig, ax = plt.subplots(figsize=(9, 4))
    line, = ax.plot([], [], "-o", markersize=3)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    times, values = [], []
    start = time.monotonic()
    try:
        while plt.fignum_exists(fig.number):
            value = point()
            times.append(time.monotonic() - start)
            values.append(np.nan if value is None else value)
            line.set_data(times[-history:], values[-history:])
            ax.relim()
            ax.autoscale_view()
            plt.pause(interval)
    except KeyboardInterrupt:
        pass
    finally:
        plt.ioff()
        plt.close(fig)
    return np.asarray(times), np.asarray(values, dtype=float)


def live_curve(one_pass, x, *, xlabel, ylabel, title, interval=0.05, busy_wait=0.5):
    """Redraw a swept curve after every pass until the window is closed.

    *one_pass* runs one more sweep and returns the average so far, or ``None``
    while another process holds the board, in which case the window keeps the
    curve it has and looks again shortly.
    """
    x = np.asarray(x, dtype=float)
    plt.ion()
    fig, ax = plt.subplots(figsize=(9, 5))
    line, = ax.plot(x, np.full(x.size, np.nan), "-o", markersize=3)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)
    passes = 0
    y = np.full(x.size, np.nan)
    try:
        while plt.fignum_exists(fig.number):
            values = one_pass()
            if values is None:
                ax.set_title(f"{title} — {passes} passes, board busy")
                plt.pause(busy_wait)
                continue
            passes += 1
            y = np.asarray(values, dtype=float)
            line.set_ydata(y)
            ax.set_title(f"{title} — {passes} passes")
            ax.relim()
            ax.autoscale_view()
            plt.pause(interval)
    except KeyboardInterrupt:
        pass
    finally:
        plt.ioff()
        plt.close(fig)
    return x, y, passes
