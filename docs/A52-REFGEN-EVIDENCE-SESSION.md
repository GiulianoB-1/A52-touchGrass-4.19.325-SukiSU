# A52 REFGEN evidence session

## Purpose

This local-file workflow binds one audited candidate, one physical screen result,
one returned collector capture, and one analyzer verdict into a reproducible
handoff bundle. It does not communicate with the phone or modify the original
capture.

## Start the session

Run before the manual device test:

```bat
tools\start_a52_refgen_test_session.bat
```

The command reruns candidate validation and creates a unique session directory
containing:

- the audited boot image hash and byte size
- source workflow and artifact identifiers
- UTC session timestamp
- candidate validation report
- a session manifest

It also writes `LATEST-A52-REFGEN-SESSION.txt` beside the session directories.

## Finish the session

After recovery collection, use the same session directory:

```bat
tools\finish_a52_refgen_test_session.bat ^
  <session-directory> <untouched-collector.zip> black
```

Use `stable` when the display remained usable or `unknown` when the physical
result was not recorded.

The finish step:

1. Revalidates the candidate and rejects any identity change.
2. Validates the collector archive, directory, or raw-only fallback.
3. Runs the task-aware REFGEN and display analyzer.
4. Preserves an untouched copy of the supplied evidence.
5. Writes an internal checksum manifest.
6. Creates a deterministic handoff ZIP.

The output consists of:

- `A52_REFGEN_EVIDENCE_<session-id>.zip`
- the matching `.sha256` file
- the matching `.receipt.json` file

The ZIP contains the session manifest, candidate reports, capture-intake report,
original capture, decoder outputs, diagnosis, and `FINAL-SHA256SUMS.txt`.

The ZIP hash is kept outside the ZIP because an archive cannot contain its own
final hash without a circular dependency.

## Verify a received bundle

Keep the ZIP, `.sha256`, and `.receipt.json` files together, then run:

```bat
tools\verify_a52_refgen_evidence_bundle.bat A52_REFGEN_EVIDENCE_....zip
```

The verifier checks:

- external ZIP checksum
- receipt hash, byte size, and session ID
- safe archive paths
- every internal checksum
- completed session schema
- exactly one preserved original capture
- generated `diagnosis.json`

A different capture cannot silently replace an already preserved capture inside
an existing session. Repeated completion is accepted only when the source hash
is unchanged.
