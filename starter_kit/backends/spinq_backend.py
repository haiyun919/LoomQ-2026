"""SpinQ backend: transpile Circuit IR to QASM 2.0 and run via subprocess.

SpinQit requires antlr4 4.9.x while Braket needs 4.13.x, so we isolate
SpinQit in a separate venv (.venv-spinq) and call it via subprocess.
"""

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from dataclasses import replace
from typing import Dict

try:
    from ..qasm_parser import Circuit
except ImportError:
    from qasm_parser import Circuit


def transpile_to_spinq(circuit: Circuit) -> str:
    """SpinQ accepts OpenQASM 2.0 natively - reconstruct from IR."""
    _validate_circuit(circuit)
    lines = ['OPENQASM 2.0;', 'include "qelib1.inc";']
    lines.append(f"qreg q[{circuit.num_qubits}];")
    lines.append(f"creg c[{circuit.num_clbits}];")

    import math

    def fmt_angle(v):
        if abs(v) < 1e-12:
            return "0"
        ratio = v / math.pi
        for num, den in [(1,1), (-1,1), (1,2), (-1,2), (1,4), (-1,4)]:
            if abs(ratio - num/den) < 1e-10:
                sign = "-" if num < 0 else ""
                return f"{sign}pi" if abs(num) == 1 and den == 1 else f"{sign}pi/{den}"
        return repr(v)

    for gate in circuit.gates:
        qs = ", ".join(f"q[{q}]" for q in gate.qubits)
        if gate.params:
            ps = ", ".join(fmt_angle(p) for p in gate.params)
            lines.append(f"{gate.name}({ps}) {qs};")
        else:
            lines.append(f"{gate.name} {qs};")

    for qubit, clbit in circuit.measurements:
        lines.append(f"measure q[{qubit}] -> c[{clbit}];")

    return "\n".join(lines) + "\n"


def _validate_circuit(circuit: Circuit) -> None:
    if circuit.num_qubits < 1 or circuit.num_clbits < 1:
        raise ValueError("circuit must declare positive-size q and c registers")
    if not circuit.measurements:
        raise ValueError("circuit must contain at least one measurement")


_RUNNER = os.path.join(os.path.dirname(__file__), "spinq_runner.py")


def _execution_qasm(circuit: Circuit) -> str:
    """Build SpinQ's stable full-measurement execution form."""
    execution_clbits = max(circuit.num_qubits, circuit.num_clbits)
    measured_circuit = replace(
        circuit,
        measurements=[(qubit, qubit) for qubit in range(circuit.num_qubits)],
        num_clbits=execution_clbits,
    )
    return transpile_to_spinq(measured_circuit)


def _spinq_python():
    """Resolve the isolated interpreter without exposing it to Braket deps."""
    configured = os.environ.get("LOOMQ_SPINQ_PYTHON")
    if configured:
        if not os.path.isfile(configured):
            raise RuntimeError(
                "LOOMQ_SPINQ_PYTHON does not point to a Python executable: %s"
                % configured
            )
        return os.path.abspath(configured)

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    candidates = [
        os.path.join(project_root, ".venv-spinq", "bin", "python"),
        os.path.join(project_root, ".venv-spinq", "Scripts", "python.exe"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    raise RuntimeError(
        "isolated SpinQ interpreter not found; set LOOMQ_SPINQ_PYTHON to the "
        "Python executable in the SpinQit environment"
    )


def _spinq_environment(python_path):
    env = os.environ.copy()
    matplotlib_cache = os.path.join(tempfile.gettempdir(), "loomq-matplotlib")
    os.makedirs(matplotlib_cache, exist_ok=True)
    env["MPLCONFIGDIR"] = matplotlib_cache

    if sys.platform == "darwin":
        native_dir = os.environ.get("LOOMQ_SPINQ_NATIVE_LIB")
        if not native_dir:
            venv_root = os.path.dirname(os.path.dirname(python_path))
            native_dir = os.path.join(
                venv_root, "lib", "python3.10", "site-packages", "spinqit"
            )
        if not os.path.isfile(
            os.path.join(native_dir, "libSpinQInterface_darwin_arm_64.dylib")
        ):
            raise RuntimeError(
                "SpinQ native library directory not found; set "
                "LOOMQ_SPINQ_NATIVE_LIB to the directory containing "
                "libSpinQInterface_darwin_arm_64.dylib"
            )
        existing = env.get("DYLD_LIBRARY_PATH", "")
        env["DYLD_LIBRARY_PATH"] = (
            native_dir + os.pathsep + existing if existing else native_dir
        )
    return env


def _normalize_spinq_counts(raw_counts, circuit: Circuit):
    """Map a full qubit-order SpinQ key onto the declared c register.

    Execution always asks SpinQ to measure every qubit as q[i] -> c[i]. Its
    key is emitted in q0 ... q[n-1] order by this execution path.
    """
    normalized = {}
    for raw_key, count in raw_counts.items():
        values = str(raw_key).zfill(circuit.num_qubits)
        bits = ["0"] * circuit.num_clbits
        for qubit, clbit in circuit.measurements:
            bits[circuit.num_clbits - 1 - clbit] = values[qubit]
        normalized_key = "".join(bits)
        normalized[normalized_key] = normalized.get(normalized_key, 0) + count
    return normalized


def run_on_spinq(circuit: Circuit, shots: int = 8192) -> Dict:
    """Run circuit on SpinQit via subprocess, return unified schema."""
    _validate_circuit(circuit)
    if not isinstance(shots, int) or isinstance(shots, bool) or shots < 1:
        raise ValueError("shots must be a positive integer")
    qasm_str = _execution_qasm(circuit)

    python_path = _spinq_python()
    env = _spinq_environment(python_path)

    proc = subprocess.run(
        [python_path, _RUNNER, qasm_str, str(shots)],
        capture_output=True, text=True, env=env, timeout=120
    )

    if proc.returncode != 0:
        raise RuntimeError("SpinQ subprocess failed: %s" % proc.stderr[-500:])

    data = json.loads(proc.stdout.strip())

    normalized = _normalize_spinq_counts(data["counts"], circuit)

    return {
        "backend": "spinq_basic_simulator",
        "job_id": "spinq-local-{:04x}".format(hash(qasm_str) & 0xFFFF),
        "shots": shots,
        "counts": normalized,
        "bit_order": "little",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "meta": {
            "transpiled_gates": len(circuit.gates),
            "depth": _estimate_depth(circuit),
        },
    }


def _estimate_depth(circuit: Circuit) -> int:
    if not circuit.gates:
        return 0
    layers = []
    for gate in circuit.gates:
        placed = False
        for layer in layers:
            if not (set(gate.qubits) & layer):
                layer.update(gate.qubits)
                placed = True
                break
        if not placed:
            layers.append(set(gate.qubits))
    return len(layers)
