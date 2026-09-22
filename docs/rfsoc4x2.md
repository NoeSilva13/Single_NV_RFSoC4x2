# RFSoC4x2 setup and bring-up

This branch targets the RFSoC4x2 with QICK-DAWG. The `main` branch remains the
implementation for TimeTagger, Pulse Streamer 8/2, and Rigol DSG836.

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

Live windows take the board claim per update and skip a sample rather than
wait, so a confocal scan can still start. Sweep results are written under:

```text
data/mmddyy/RFSoC_<Experiment>/
```

Confocal images (CSV, NPZ, TIFF, and PNG) from this branch go to
`data/mmddyy/RFSoC_confocal/`, separate from the `data/mmddyy/` files saved
by the TimeTagger / Pulse Streamer / Rigol branch.

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
