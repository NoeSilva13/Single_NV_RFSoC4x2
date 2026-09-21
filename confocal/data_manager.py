import os
import time
import pandas as pd
from common.utils import experiment_data_root

class DataManager:
    def __init__(self):
        """
        Initialize the DataManager.
        """
        pass

    def _next_index(self, daily_folder, day):
        """Next sequence number based on distinct scan basenames in the folder.

        Counts unique ``mmddyy###`` basenames across both ``.csv`` and ``.npz``
        outputs so that 2D scans (which write .csv + .npz) and 3D scans (which
        write only .npz) share a single, collision-free counter.

        ``day`` is the date prefix (``mmddyy``), not the folder name. Confocal
        files live in ``RFSoC_confocal``, whose basename is not that prefix.
        """
        bases = set()
        for f in os.listdir(daily_folder):
            if f.startswith(day) and (f.endswith('.csv') or f.endswith('.npz')):
                bases.add(os.path.splitext(f)[0])
        return len(bases) + 1

    def next_base_path(self, ext=''):
        """Create today's RFSoC confocal folder and return the next sequential path.

        Scans acquired on this branch are written under
        ``data/<mmddyy>/RFSoC_confocal/`` so they stay distinct from confocal
        files saved by the TimeTagger / Pulse Streamer / Rigol branch, which
        land directly in ``data/<mmddyy>/``.

        Args:
            ext: Optional extension (e.g. ``'.npz'``). If empty, returns the
                base path without extension.
        """
        day = time.strftime("%m%d%y")
        daily_folder = os.path.join(experiment_data_root(), day, "RFSoC_confocal")
        os.makedirs(daily_folder, exist_ok=True)
        seq_str = f"{self._next_index(daily_folder, day):03d}"
        base = os.path.join(daily_folder, f"{day}{seq_str}")
        return base + ext if ext else base

    def save_scan_data(self, scan_data, scan_params):
        """
        Save scan data to a CSV file in a daily folder.
        
        Args:
            scan_data: Dictionary containing 'image' (2D array), 'x_points', 'y_points', 'scale_x', 'scale_y' arrays
            scan_params: Dictionary containing scan parameters (optional)
            
        Returns:
            str: The path to the saved file
        """
        # Create filename using the shared counter (counts both .csv and .npz)
        # so 2D and 3D scans never reuse the same sequence number.
        filename = self.next_base_path('.csv')

        # Get the image and points from scan_data
        image = scan_data['image']
        x_points = scan_data['x_points']
        y_points = scan_data['y_points']
        scale_x = scan_data.get('scale_x', None)
        scale_y = scan_data.get('scale_y', None)

        # Real axis names for this scan (fast axis -> columns, slow axis -> rows).
        # Defaults keep older XY-only callers working.
        axis_names = scan_params.get('axis_names', ('x', 'y'))
        fast_name = str(axis_names[0]).upper()
        slow_name = str(axis_names[1]).upper()

        # Create a DataFrame with the image data; the index name encodes the
        # real slow\\fast axes (e.g. "Z\\X" for an XZ scan).
        df = pd.DataFrame(image, index=y_points, columns=x_points)
        df.index.name = f"{slow_name}\\{fast_name}"

        # Write config and data to file
        with open(filename, 'w', encoding='utf-8', newline='') as f:
            # Write measurement date and time as the first line
            measurement_time = time.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"# Measurement Time: {measurement_time}\n")

            # Write the scan mode if provided
            scan_mode = scan_params.get('scan_mode', None)
            if scan_mode is not None:
                f.write(f"# Scan Mode: {scan_mode}\n")

            # Write scale information (fast x slow)
            f.write(f"# Scale: {scale_x:.6f} x {scale_y:.6f} µm/pixel "
                    f"({fast_name} x {slow_name})\n")

            # Write scan ranges (µm), labelled with the real axes
            x_range = scan_params['scan_range']['x']
            y_range = scan_params['scan_range']['y']
            f.write(f"# {fast_name} Range: {x_range[0]:.6f} to {x_range[1]:.6f} µm\n")
            f.write(f"# {slow_name} Range: {y_range[0]:.6f} to {y_range[1]:.6f} µm\n")

            # Write resolutions
            x_res = scan_params['resolution']['x']
            y_res = scan_params['resolution']['y']
            f.write(f"# {fast_name} Resolution: {x_res} pixels\n")
            f.write(f"# {slow_name} Resolution: {y_res} pixels\n")

            # Write dwell time(s). A Z-involving scan has a separate Z dwell.
            dwell_time = scan_params['dwell_time']
            f.write(f"# Dwell Time: {dwell_time:.6f} s\n")
            z_dwell_time = scan_params.get('z_dwell_time', None)
            if z_dwell_time is not None:
                f.write(f"# Z Dwell Time: {z_dwell_time:.6f} s\n")

            # Write total scan time (acquisition duration) if available
            scan_time = scan_params.get('scan_time', None)
            if scan_time is not None:
                f.write(f"# Scan Time: {scan_time:.2f} s\n")

            # Write calibration constants for reproducibility
            microns_per_volt = scan_params.get('microns_per_volt', None)
            if microns_per_volt is not None:
                f.write(f"# Microns Per Volt (galvo): {microns_per_volt:.6f} µm/V\n")
            z_um_per_volt = scan_params.get('z_um_per_volt', None)
            if z_um_per_volt is not None:
                f.write(f"# Microns Per Volt (piezo Z): {z_um_per_volt:.6f} µm/V\n")

            # Write scan points information
            f.write(f"# {fast_name} Points: {len(x_points)} points from "
                    f"{x_points[0]:.6f}µm to {x_points[-1]:.6f}µm\n")
            f.write(f"# {slow_name} Points: {len(y_points)} points from "
                    f"{y_points[0]:.6f}µm to {y_points[-1]:.6f}µm\n")

            f.write("#\n")  # Empty line to separate header from data

            # Write the data matrix
            df.to_csv(f, float_format='%.7f')

        return filename