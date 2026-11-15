# Contributing

Thanks for your interest in `paradoxdb`.

## Ground rules

- **Clean-room project.** Everything here is implemented from the public
  description of the Paradox file format and from a written specification
  of the behaviour (the design decisions it left open are recorded in
  `docs/DECISIONES.md`). Do not contribute code, constants or fixtures taken
  from other Paradox implementations with incompatible licences (for example
  GPL code).
- **Synthetic data only.** Tests and examples must use tables produced by
  `paradoxdb.synthetic`. Never commit a real `.DB`, `.MB` or `.PX` file, even
  an "anonymised" one.
- **No silent conversions.** A cell is `OK`, `NULL` or `INVALID` and always
  carries its raw bytes. Changing that contract needs a discussion first.

## Development setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check .
```

Python 3.11 and 3.12 are tested in CI. The code base is pure Python with no
runtime dependencies; please keep it that way.

## Pull requests

1. Open an issue first for anything beyond a small fix.
2. Add or update tests for every behavioural change; property-based tests
   with Hypothesis are welcome for encoders and decoders.
3. Keep identifiers in English. Documentation may be in English or Spanish.
4. Update `CHANGELOG.md` under *Unreleased*.
5. CI must be green: tests on 3.11 and 3.12, `ruff`, the leak test and
   `gitleaks`.

## Reporting format problems

If a real-world table is read incorrectly, please do **not** attach the
table. Instead, describe the header bytes (first 0x80 bytes are enough) and
the field layout, or reproduce the problem with the synthetic factory.
