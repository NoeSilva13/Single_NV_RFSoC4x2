import time
import numpy as np
import nidaqmx
from nidaqmx.constants import (TerminalConfiguration, Edge, CountDirection, AcquisitionType, SampleTimingType)
from nidaqmx.errors import DaqNotFoundError, DaqError
import csv
from typing import Generator, Tuple, Dict, Any

class GalvoScannerController:
    """
    Controller for galvo mirror scanning system with NI DAQ.
    
    Features:
    - Voltage control for X/Y galvo mirrors
    - Position feedback reading
    - SPD photon counting
    - Real-time scanning with visualization
    - Buffered scanning for improved performance
    """
    def __init__(self):
        """Initialize the controller with DAQ channels from common.utils."""
        from common.utils import (
            DAQ_GALVO_X, DAQ_GALVO_Y, DAQ_SPD_COUNTER,
            DAQ_XOUT_VOLTAGE, DAQ_YOUT_VOLTAGE,
        )
        try:
            # Test DAQ connection
            with nidaqmx.Task() as test_task:
                test_task.ao_channels.add_ao_voltage_chan(DAQ_GALVO_X)
            
            # DAQ channel configuration
            self.spd_counter = DAQ_SPD_COUNTER
            self.xin_control = DAQ_GALVO_X
            self.yin_control = DAQ_GALVO_Y
            self.xout_voltage = DAQ_XOUT_VOLTAGE
            self.yout_voltage = DAQ_YOUT_VOLTAGE

            # Voltage ranges
            self.control_range = (-10.0, 10.0)      # Output voltage range
            self.output_range = (-3.75, 3.75)       # Input voltage range

            # Calibration factors
            self.x_calibration = 1.0
            self.y_calibration = 1.0
            
            # Scanning parameters
            self.sample_rate = 1000  # Hz
            self.samples_per_point = 10
            self.settling_time = 0.001  # seconds
            
            self.set_voltages(0, 0)
            print("Successfully initialized DAQ connection")
                        
        except DaqNotFoundError:
            raise RuntimeError(
                "NI-DAQmx not found. Please install NI-DAQmx from National Instruments website: "
                "https://www.ni.com/en/support/downloads/drivers/download.ni-daqmx.html"
            )
        except DaqError as e:
            raise RuntimeError(f"Error initializing DAQ: {str(e)}")
        except Exception as e:
            raise RuntimeError(f"Unexpected error during initialization: {str(e)}")

    # --------------------------
    # Basic Control Methods
    # --------------------------

    def set_voltages(self, x_voltage, y_voltage):
        """
        Set X and Y mirror voltages.
        
        Args:
            x_voltage (float): X-axis voltage (-10 to 10V)
            y_voltage (float): Y-axis voltage (-10 to 10V)
        """
        try:
            x_voltage = np.clip(x_voltage, *self.control_range)
            y_voltage = np.clip(y_voltage, *self.control_range)

            with nidaqmx.Task() as ao_task:
                ao_task.ao_channels.add_ao_voltage_chan(self.xin_control)
                ao_task.ao_channels.add_ao_voltage_chan(self.yin_control)
                ao_task.write([x_voltage, y_voltage], auto_start=True)
        except DaqError as e:
            raise RuntimeError(f"Error setting voltages: {str(e)}")

    def read_voltages(self):
        """
        Read actual mirror positions from feedback signals.
        
        Returns:
            tuple: (x_voltage, y_voltage) in volts
        """
        with nidaqmx.Task() as ai_task:
            ai_task.ai_channels.add_ai_voltage_chan(self.xout_voltage, terminal_config=TerminalConfiguration.RSE)
            ai_task.ai_channels.add_ai_voltage_chan(self.yout_voltage, terminal_config=TerminalConfiguration.RSE)
            voltages = ai_task.read()
            return voltages[0], voltages[1]

    def read_spd_count(self, sampling_time=0.1):
        """
        Read photon counts from SPD.
        
        Args:
            sampling_time (float): Time to count photons in seconds
            
        Returns:
            int: Number of counts during sampling period
        """
        with nidaqmx.Task() as counter_task:
            counter_task.ci_channels.add_ci_count_edges_chan(
                self.spd_counter, edge=Edge.RISING, initial_count=0
            )
            counter_task.ci_channels[0].ci_count_edges_term = self.spd_edge_source
            counter_task.start()
            time.sleep(sampling_time)
            count = counter_task.read()
            counter_task.stop()
        return count
    
