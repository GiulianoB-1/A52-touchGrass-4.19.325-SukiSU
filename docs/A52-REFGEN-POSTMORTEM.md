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

## REFGEN parity result

The Kona downstream implementation uses register offset `0x80`, bit 0 as the
enable mask, writes 1 to enable, writes 0 to disable, and reads the same bit for
`is_enabled`. The stock node maps `0x84` bytes, so the final 32-bit register at
`0x80` is inside the resource. The diagnostic port preserves that behaviour and
adds metadata-only before/after records.

No further speculative REFGEN change should be made before the device test.

## Inputs

The preferred input is the untouched `ramoops-raw-1MiB.bin` capture. Place the
analyzer beside these existing tools from the v2 test kit:

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
- `diagnosis.json`: complete structured evidence
- `critical-timeline.csv`: ordered REFGEN, display, heartbeat, and watchdog events

## Decision boundary

A stable screen after a successful REFGEN enable supports the missing-provider
hypothesis. A black screen with an unmatched display scope moves the next patch
to that one function. A REFGEN probe failure moves the patch to the recorded
probe stage. Missing recorder evidence triggers a collection retry, not another
kernel change.

The analyzer processes recorder metadata only. It does not recover secure
payloads, keys, authentication tokens, command buffers, response buffers, or
process memory.
