# RFSoC4x2 setup and bring-up

This repository targets the RFSoC4x2 with QICK-DAWG. The TimeTagger, Pulse
Streamer 8/2, and Rigol DSG836 implementation lives in
[Single_NV_Scanning_Microscopy](https://github.com/NoeSilva13/Single_NV_Scanning_Microscopy).

## Software

Use the same QICK version on the PC and board:

```powershell
python -m pip install -r requirements.txt
```

The requirements pin QICK 0.2.302 and QICK-DAWG commit `4bc384f4`. On the board,
load either `firmware/photon_counting/qick_4x2.bit` or the 9 GHz variant, then
start the Pyro name server and `qick_daemon` with server name `myqick`.

Set the board address without editing code:

```powershell
$env:NV_RFSOC_IP = "192.168.3.1"
$env:NV_RFSOC_SERVER_NAME = "myqick"
$env:NV_RFSOC_FIRMWARE = "photon_counting"
```

Run `python -m rfsoc.diagnostics` before either application.

## Required wiring

- Condition and attenuate the SPCM TTL output before the selected RF ADC.
  Never assume a 3.3/5 V TTL signal is safe for an RFSoC ADC input.
- RFSoC PMOD AOM output -> suitable logic buffer/driver -> AOM gate.
- RFSoC PMOD pixel-clock output -> logic buffer -> `DAQ_RFSOC_CLOCK_INPUT`
  (`/Dev1/PFI8` by default).
- Connect a common signal ground. Verify levels and polarity at every receiving
  connector before connecting the instruments.
- RF DAC channel 0 -> filtered/amplified microwave chain. Measure unwanted
  Nyquist images before connecting the device under test.
- DEER only: RF DAC channel 1 (`RFSOC_MW2_CHANNEL`) -> one input of a power
  combiner whose other input is DAC channel 0, ahead of the same amplifier.
  Check both tones and their images on the spectrum analyzer with the combiner
  in place, and confirm `connection_report()["mw2_generator"]` reports the
  second generator rather than `unusable`.

The pin indices in `common/utils.py` are QICK output-pin indices, not connector
labels. Confirm them against the printed `QickConfig`.

## Nyquist-zone rule

At 2.87 GHz:

- `photon_counting` (4.9152 GS/s DAC): configure NQZ 2.
- `photon_counting_9ghz` (9.8304 GS/s DAC): configure NQZ 1.

The software rejects an inconsistent firmware/frequency/NQZ combination, but
that check does not replace a spectrum-analyzer measurement.

## Confocal timing

The NI device remains responsible for `ao0`, `ao1`, and `ao2`, including all DC
writes. During a scan:

1. The host buffers the whole raster and arms one finite AO task using PFI8 as an
   external rising-edge sample clock. The task is started after the program is
   uploaded and before the readout job is queued, because QICK's streamer is
   what starts the tProc.
2. One `ConfocalFrame` emits a PMOD edge for each sample of the frame, with the
   pixel loop nested inside a line loop.
3. After every imaging edge the program waits for galvo/piezo settle, then
   integrates one edge-counting window.
4. Lead-in clocks before the first line and flyback clocks after each line carry
   no ADC trigger, so the accumulated buffer holds exactly one readout per pixel.
5. `stream_counts()` drains that buffer while the tProc keeps running, a stride
   at a time, and hands each stride to napari. `RFSOC_STREAM_UPDATE_SECONDS`
   sets the stride: one raster line at ordinary dwells, single pixels once one
   pixel outlasts an update. Only the *unread* backlog is bounded by
   `avg_maxlen`, so a whole image is one acquire at any resolution.

Edge counting has no 16-bit window limit. QICK warns above 65536 readout samples,
but that warning is about summing 15-bit analog samples into a 32-bit
accumulator, which a photon count cannot overflow. `window_linearity()` measured
counts proportional to the window from 13 us to 5 s, which is the value in
`RFSOC_MAX_COUNTING_WINDOW_S`. Every pixel integrates its whole dwell in one
window: a dwell past that measurement is refused rather than split, and the next
ceiling up is the 31-bit tProc immediate carrying the window, 6.99 s. Count
rates use the integrated window only, excluding settle and flyback.

Stop lands between two readout strides: within a line at ordinary dwells, within
a pixel at long ones. It stops the tProc directly, and the next `start_readout`
tears down the streamer, which is QICK's own cleanup path.

## Bench acceptance sequence

1. Run `connection_report()` and compare firmware, channel map, PMOD pins and
   client/server versions.
2. Feed conditioned calibration pulses to the ADC. Sweep high/low thresholds
   and verify linear counts, minimum pulse width and no rollover. Run
   `window_linearity()` against the same source to confirm
   `RFSOC_MAX_COUNTING_WINDOW_S` before trusting a longer dwell.
3. Observe AOM and pixel clock together on an oscilloscope. Confirm that clock
   transitions do not glitch the AOM mask.
4. Arm a short NI AO buffer and prove that N PMOD edges produce exactly N AO
   updates.
5. Acquire one pixel, one short line, a line with flyback, then XY, XZ/YZ and
   XYZ.
6. Measure the RF DAC spectrum at the intended gain and NQZ.
7. Run CW ODMR, pulsed ODMR, Rabi and T1 in that order.
8. For DEER, repeat step 6 on DAC channel 1 through the combiner.

## Experiment runner

`run_odmr_experiments.py` is a file of experiment blocks. One
`default_config` holds the shared `NVConfiguration`: assign time, frequency and
phase with qickdawg's suffixes (`_tns` / `_tus` / `_treg`, `_fMHz` / `_fGHz` /
`_freg`, and `_ftns` / `_ftus` / `_ftsamp` for FineRes pulses). Each block
copies that config, adds its sweep with `start` / `stop` / `delta`, and calls
the qickdawg program. Uncomment the block you want, comment the others, then:

```powershell
python run_odmr_experiments.py
```

Live traces are `live_pl` and `live_cwodmr` in the same file: uncomment that
call instead of the single-shot one. They take the same copied config.

The pulsed experiments are the FineRes programs, whose microwave pulses land on
DAC samples (about 0.2 ns) instead of on tProc cycles (about 3.3 ns). Each
sweep is one QICK acquire. The readout-window calibration is the exception: it
walks a narrow counting window across the laser pulse from the host, one
acquire per offset, under a single board claim.

| Block | Program | What the fit reports |
| --- | --- | --- |
| Laser on / `hold_laser` | `laser_on`, then `laser_off` | gate stays open until Enter |
| PL Intensity / `live_pl` | `PLIntensity` | count rate |
| Dark Counts | `DarkCounts` | background rate |
| CW ODMR / `live_cwodmr` | `LockinODMR` | resonance, linewidth |
| Pulsed ODMR | `PODMRFineRes` | resonance, linewidth |
| readout window | `CountingDurationFineRes` | `readout_integration_tns`, `laser_on_tns`, `laser_readout_offset_tns` |
| Rabi | `RabiFineRes` | `mw_pi2_ftns`, `mw_pi_ftns` |
| Ramsey | `CPMGXYFineRes` (`n_cpmg=0`) | detuning, T2* |
| Hahn Echo | `CPMGXYFineRes` (`n_cpmg=1`) | T2 |
| CPMG-N | `CPMGXYFineRes` (`n_cpmg=N`) | T2 |
| T1 | `T1FineRes` | T1 |
| DEER spectrum | `DEERSpectrumFineRes` (this repo) | target resonance, linewidth |
| DEER vs tau | `DEERSpectrumFineRes`, tau walked from the host | dipolar coupling, distance |

### DEER spectrum

`rfsoc/deer_program.py` plays a Hahn echo on the sensor at `mw_fMHz` on
generator 0 and a pi pulse on generator 1 (`mw2_*` settings) whose frequency
is swept in the FPGA. Each point reads the echo with the target pulse and the
same echo with generator 1's gain at zero; the contrast is the fraction of the
echo the target pulse removes, so the target resonance is a dip fitted as in
ODMR. `get_reference` must be `True`. The target pulse starts at the sensor pi
pulse plus `mw2_offset_ftns`, which also absorbs the skew between the DACs.
Both tones sit in NQZ 2 with `photon_counting` (`RFSOC_MW2_NQZ`).

Calibrate in this order:

1. Pulsed ODMR and Rabi with `cfg.mw_channel = RFSOC_MW2_CHANNEL` on the
   target transition, for `mw2_fMHz` and `mw2_pi_ftns` through the second
   chain. This needs a target that is optically read out in the same spot.
2. Measure the DAC A / DAC B skew on an oscilloscope and set
   `mw2_offset_ftns`.
3. Hahn echo on the sensor; take `tau_ftns` near T2/2.
4. DEER spectrum.
5. DEER vs tau at the resonance the spectrum found.

### DEER vs tau

`deer_tau_walk()` in `run_odmr_experiments.py` holds the target pulse at one
frequency and runs one acquire per tau under a single board claim, because the
pulse schedule is fixed when the program compiles. Each acquire plays two
identical frequency points, since qickdawg's sweep cannot have one, and
averages them. Flipping the target at the sensor's pi pulse leaves the sensor
a phase `2 pi nu_dd tau`, so the lost fraction of the echo is fitted as

```text
C(tau) = A/2 * (1 - cos(2 pi nu_dd tau) * exp(-tau / decay)) + offset
```

and the fit reports `dipolar_coupling_hz`, the distance
`r = (52.04 MHz nm^3 * |1 - 3cos^2 theta| / nu_dd)^(1/3)` at an angular factor
of 1 (`distance_nm`), and `max_distance_nm` at the largest factor, 2, which
bounds the separation for any orientation. A single trace does not measure the
angle. `tau` is end of pulse to start of pulse, so pulses that are not short
next to tau bias `nu_dd` by about the pulse length over tau. Sample at least
four points per oscillation period; a trace that only rises and saturates is a
bath of targets rather than one coupled spin.

Live windows take the board claim per update and skip a sample rather than
wait, so a confocal scan can still start. Sweep results are written under:

```text
data/mmddyy/RFSoC_<Experiment>/
```

Confocal images (CSV, NPZ, TIFF, and PNG) from this repository go to
`data/mmddyy/RFSoC_confocal/`, separate from the `data/mmddyy/` files saved
by [Single_NV_Scanning_Microscopy](https://github.com/NoeSilva13/Single_NV_Scanning_Microscopy).

Each sweep contains CSV, compressed NPZ metadata, and a PDF with the fitted
curve annotated. The CSV starts with `#` comment lines for the requested and
executed configuration and the fit, in the `NVConfiguration` names, then the
data table; read it with `pandas.read_csv(path, comment="#")`. Ramsey also
plots the FFT of the free-precession trace. `get_reference=True` (set on
`default_config`) doubles each point with a microwave-off readout; set it
`False` on the copied config to halve the duration at the cost of that
normalisation.

## Sharing the board between processes

The board daemon exposes one `QickSoc`, and `start_readout()` stops whatever
readout is already running, so two processes driving it abort each other's
acquisitions rather than queueing. `RFSoCSession.acquisition()` therefore takes
two locks: a thread lock inside the process, and a file claim (in the temp
directory, one file per board IP) that spans processes. The kernel drops the
claim if the holder is killed, so a crash cannot leave the board unusable.

This is what makes the intended workflow safe: keep the confocal app open, pick
an NV, and run `run_odmr_experiments.py` next to it. Each experiment claims the
board for its sweep, the app's live count plot stands down for that time and
resumes afterwards, and a scan already in flight keeps the board until it is
done. Whoever waits longer than `RFSOC_CLAIM_WAIT_S` (30 s, override with
`NV_RFSOC_CLAIM_WAIT_S`) gives up with a message naming the holder's PID instead
of blocking indefinitely.
