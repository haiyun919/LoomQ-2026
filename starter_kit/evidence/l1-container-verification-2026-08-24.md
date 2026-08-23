# L1 linux/amd64 Container Verification - 2026-08-24

The image was built on the submission machine with Docker Desktop 29.7.2.
The host is Apple Silicon; the submission image was explicitly built and run
as `linux/amd64` because SpinQit 0.2.4 publishes an x86_64 Linux wheel.

From `loomq/starter_kit/`:

```bash
docker build --platform linux/amd64 --progress=plain \
  -t loomq-l1:amd64 .
docker run --rm --platform linux/amd64 loomq-l1:amd64
```

The build completed successfully. Image metadata:

```text
id=sha256:7bf179a3c7c9a20e8c45b8db650d2cadb56cea6236fbba1e9acf913de1e0e265
size=3114445012
os=linux
arch=amd64
```

Evaluator output:

```text
[PASS] l1:bell.qasm:spinq: fidelity threshold met
[PASS] l1:bell.qasm:braket: fidelity threshold met
[PASS] l1:ghz3.qasm:spinq: fidelity threshold met
[PASS] l1:ghz3.qasm:braket: fidelity threshold met
{"passed": 4, "failed": 0, "total": 4}
```

The container process exited with status `0`. The complete machine-readable
report is `evidence/l1-container-evaluator-2026-08-24.json`.
