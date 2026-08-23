# L1 Local Verification - 2026-08-21

All commands were run from the `loomq/` project root with Python 3.10.

## Regression Tests

```bash
PYTHONPATH=starter_kit PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m unittest discover -s tests -v
```

Result: 16 tests passed.

## Real Backend Fixtures

The all-gates fixture ran both backends with 8192 shots:

- SpinQ: `000=600, 010=3496, 100=3496, 110=600`
- Braket: `000=601, 010=3530, 100=3450, 110=611`

Both asymmetric and partial fixtures returned exactly `100=8192` and `01=8192`, respectively, on each backend.
A three-qubit/one-classical-bit partial-measurement probe also returned `1=32` on both backends.

## Public Evaluator

```bash
PYTHONPATH=starter_kit LOOMQ_SPINQ_PYTHON=.venv-spinq/bin/python \
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python starter_kit/evaluator.py \
--level l1 --target spinq,braket --shots 8192 \
--json-out starter_kit/evidence/l1-public-evaluator-2026-08-21.json
```

Result: 4 passed, 0 failed; process exit status 0. The machine-readable report is stored beside this note.
