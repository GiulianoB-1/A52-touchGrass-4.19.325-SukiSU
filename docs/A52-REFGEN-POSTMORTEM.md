# A52 REFGEN postmortem analysis

## Scope

This tooling classifies the next hardware capture from the audited ACK 5.10
REFGEN candidate. It does not modify the kernel, boot image, device tree,
regulator driver, recorder, watchdog policy, or display sequence.

The analyzer consumes two independent metadata-only decoder views:

- exact A52USR2 records from the standard decoder
- high and medium confidence recovery from the mirrored console and ftrace banks

Exact records take priority. Mirrored records fill sequence gaps only when an
exact copy is unavailable. Low-confidence recovered records are excluded from
verdict decisions.

Display scope entry and exit events are paired within the recorded task context.
PID and TGID are the primary key, with `comm` used only when numeric task identity
is unavailable. CPU is deliberately excluded because a task may migrate while a
function is running. This prevents concurrent display calls from different tasks
from being paired together and producing a false stalled-function verdict.

## REFGEN parity result

The Kona downstream implementation uses register offset `0x80`, bit 0 as the
enable mask, writes 1 to enable, writes 0 to disable, and reads the same bit for
`is_enabled`. The exact A52 Lagoon node declares only `0x60` bytes at
`0x88e7000`, while the working downstream driver still accesses offset `0x80`.
This is an inherited DT/driver resource-span inconsistency, not a different
register choice introduced by the ACK candidate. The diagnostic port preserves
the working downstream behaviour and the analyzer reports the inconsistency as
a warning.

No further speculative REFGEN change should be made before the device test.

## Non-destructive hardware-test validation

Before flashing, validate the extracted hardware-test kit locally:

```bash
python3 tools/a52-refgen/validate-a52-refgen-hardware-inputs.py candidate /path/to/extracted-kit
```

On Windows, the packaged kit provides:

```bat
tools\preflight_a52_refgen_candidate.bat
```

The preflight verifies the exact audited `boot.img` size and SHA-256, required
analysis tools, and the complete kit checksum manifest. It never communicates
with the phone and never flashes anything.

After collection, validate the untouched collector ZIP or directory before
analysis:

```bash
python3 tools/a52-refgen/validate-a52-refgen-hardware-inputs.py capture A52_RAW_RAMOOPS_*.zip
```

A full capture must include the raw 1 MiB image, exporter status, recovery
identity, recovery dmesg, pstore listing, and collector checksum manifest. A raw
image by itself remains analysable, but the validator reports that the recovery
and exporter context is incomplete.

The validator rejects incorrect boot hashes, incorrect RAMOOPS size, checksum
mismatches, missing full-capture evidence, malformed archives, and ZIP path
traversal. It is metadata-only and non-destructive.

## Inputs

The preferred input is the untouched `ramoops-raw-1MiB.bin` capture. Place the
analyzer beside these existing tools from the hardware-test kit:

- `decode-a52-unified-secure-recorder.py`
- `decode-a52-mirrored-ramoops-v2.py`

Then run:

```bash
python3 diagnose-a52-refgen-display.py ramoops-raw-1MiB.bin \
  --screen-result black \
  --output a52-refgen-display-diagnosis
```

It can also consume decoder CSVs directly:

```bash
python3 diagnose-a52-refgen-display.py \
  --standard-csv decoded-standard/secure-events.csv \
  --mirrored-csv decoded-mirrored/recovered-events.csv \
  --screen-result black \
  --output a52-refgen-display-diagnosis
```

## Outputs

- `diagnosis.md`: readable verdict and next action
- `diagnosis.json`: complete structured evidence, including task context for display scopes
- `critical-timeline.csv`: ordered REFGEN, display, heartbeat, and watchdog events
- `candidate-preflight.json`: local candidate and kit-integrity result
- `a52-refgen-capture-intake.json`: collector completeness and checksum result

## Decision boundary

A stable screen after a successful REFGEN enable supports the missing-provider
hypothesis. A black screen with an unmatched display scope moves the next patch
to that one function and task context. A REFGEN probe failure moves the patch to
the recorded probe stage. Missing or damaged recorder evidence triggers a
collection retry, not another kernel change.

The analyzer and validator process recorder metadata only. They do not recover
secure payloads, keys, authentication tokens, command buffers, response buffers,
or process memory.
