"""
plot_scan.py
------------
Publication-quality heatmap for live confocal scan results.

Thread-safe: uses Figure + FigureCanvasAgg only — no pyplot global state,
safe to call from a background acquisition thread.
"""

import numpy as np
import matplotlib
import matplotlib.ticker as ticker
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import Normalize
from pathlib import Path

# ── Style (mirrors nv_style.py) — applied only inside plot_scan_results ───────
# Use rc_context so importing this module does not mutate global rcParams
# (which would affect live-plot widgets in confocal_main_control).
_SCAN_RCPARAMS = {
    "font.family":          "serif",
    "font.size":            8,
    "axes.labelsize":       8,
    "axes.titlesize":       8,
    "xtick.labelsize":      7,
    "ytick.labelsize":      7,
    "legend.fontsize":      7,
    "axes.linewidth":       0.8,
    "xtick.direction":      "in",
    "ytick.direction":      "in",
    "xtick.major.width":    0.8,
    "ytick.major.width":    0.8,
    "xtick.minor.width":    0.5,
    "ytick.minor.width":    0.5,
    "xtick.major.size":     3.0,
    "ytick.major.size":     3.0,
    "xtick.minor.size":     1.5,
    "ytick.minor.size":     1.5,
    "lines.linewidth":      1.2,
    "savefig.dpi":          300,
    "savefig.bbox":         "tight",
    "savefig.pad_inches":   0.02,
}

# Figure dimensions: single-column width (inches) and aspect ratio for images
_W1     = 3.5
_ASPECT = 1.1   # height = width × aspect

# ── Display range ─────────────────────────────────────────────────────────────
VMIN_PCT = 1    # lower percentile for color clipping (robust vs. hot pixels)
VMAX_PCT = 99   # upper percentile

# ── Colorbar label ────────────────────────────────────────────────────────────
CBLABEL = "SPD counts"


def plot_scan_results(scan_data: dict, save_path: str | Path, *,
                      title: str | None = None,
                      show_title: bool = True,
                      show_scalebar: bool = True,
                      axis_labels: tuple[str, str] = ("x", "y")) -> Path:
    """
    Save a 2D confocal scan as a publication-quality PNG.

    Thread-safe: uses Figure + FigureCanvasAgg — no pyplot global state.

    Args:
        scan_data:      dict with keys ``x_points``, ``y_points``, ``image``
                        x_points / y_points : 1D arrays of galvo voltages (V)
                        image               : 2D array (ny, nx) in raw counts.
                        Row 0 of image must correspond to the smallest y value.
        save_path:      output path — extension is replaced with .png / .pdf
        title:          figure title; defaults to the file name of ``save_path``
                        without extension (shown only when show_title is True)
        show_title:     set to False for title-less publication panels
        show_scalebar:  draw a white scale bar on the image
        axis_labels:    (horizontal, vertical) axis names, e.g. ``("x", "z")``
                        for an XZ scan. Horizontal = fast axis (columns),
                        vertical = slow axis (rows).

    Returns:
        Path to the saved PNG.
    """
    save_path = Path(save_path)
    if title is None:
        title = save_path.stem

    x_grid = np.asarray(scan_data["x_points"])   # 1D, micrometers
    y_grid = np.asarray(scan_data["y_points"])   # 1D, micrometers
    image  = np.asarray(scan_data["image"])       # 2D (ny, nx), counts

    # ── Physical field of view (µm) ───────────────────────────────────────────
    # Points are already in micrometers (canonical unit).
    scan_width_um  = float(abs(x_grid[-1] - x_grid[0]))
    scan_height_um = float(abs(y_grid[-1] - y_grid[0]))
    x_range = (0.0, scan_width_um)
    y_range = (0.0, scan_height_um)

    print(f"[scan] image {image.shape}  "
          f"FOV: {scan_width_um:.2f} × {scan_height_um:.2f} µm  "
          f"counts: {image.min():.0f} – {image.max():.0f}")

    # ── Color range (percentile clip) ─────────────────────────────────────────
    vmin = float(np.percentile(image, VMIN_PCT))
    vmax = float(np.percentile(image, VMAX_PCT))
    print(f"[display] {VMIN_PCT}th={vmin:.3g} – {VMAX_PCT}th={vmax:.3g} counts")

    # Style is scoped so global rcParams (and other GUI widgets) are unchanged.
    with matplotlib.rc_context(_SCAN_RCPARAMS):
        # ── Figure ────────────────────────────────────────────────────────────
        fig = Figure(figsize=(_W1, _W1 * _ASPECT))
        FigureCanvasAgg(fig)
        ax = fig.add_subplot(111)

        extent = [x_range[0], x_range[1], y_range[0], y_range[1]]
        im = ax.imshow(
            image,
            extent=extent,
            origin="lower",
            cmap="inferno",
            norm=Normalize(vmin=vmin, vmax=vmax),
            aspect="equal",
            interpolation="nearest",
            rasterized=True,
        )

        # ── Colorbar ──────────────────────────────────────────────────────────
        cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        cb.set_label(CBLABEL, fontsize=7)
        cb.ax.tick_params(labelsize=6)

        # ── Axes style ────────────────────────────────────────────────────────
        ax.spines["top"].set_visible(True)
        ax.spines["right"].set_visible(True)
        ax.tick_params(which="both", direction="out", top=False, right=False)
        ax.xaxis.set_minor_locator(ticker.AutoMinorLocator())
        ax.yaxis.set_minor_locator(ticker.AutoMinorLocator())

        ax.set_xlabel(rf"${axis_labels[0]}$ ($\mu$m)")
        ax.set_ylabel(rf"${axis_labels[1]}$ ($\mu$m)")
        if show_title:
            ax.set_title(title)

        # ── Scale bar (~20 % of FOV, bottom-left corner) ──────────────────────
        if show_scalebar:
            scale_len = round(scan_width_um / 5)
            pad       = scan_width_um * 0.08
            x0 = x_range[0] + pad
            y0 = y_range[0] + pad
            ax.plot([x0, x0 + scale_len], [y0, y0],
                    "-", color="white", linewidth=1.8, solid_capstyle="butt")
            ax.text(x0 + scale_len / 2, y0 + scan_width_um * 0.04,
                    fr"${scale_len}\,\mu$m",
                    ha="center", va="bottom", color="white", fontsize=6)

        # ── Save ──────────────────────────────────────────────────────────────
        save_path.parent.mkdir(parents=True, exist_ok=True)
        png_path = save_path.with_suffix(".png")
        # pdf_path = save_path.with_suffix(".pdf")
        fig.savefig(png_path, format="png", dpi=300, bbox_inches="tight", pad_inches=0.02)
        # fig.savefig(pdf_path, format="pdf",          bbox_inches="tight", pad_inches=0.02)
        print(f"[saved] {png_path.name}")
    return png_path
