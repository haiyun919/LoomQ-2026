"""AWS Braket backend: transpile Circuit IR to OpenQASM 3 and run on LocalSimulator."""

import math
import re
from datetime import datetime, timezone
from typing import Dict

try:
    from ..qasm_parser import Circuit, GateOp
except ImportError:
    from qasm_parser import Circuit, GateOp


# Map QASM 2 gate names to Braket OpenQASM 3 equivalents
_QASM3_GATE_NAME = {
    "h": "h", "x": "x", "s": "s", "sdg": "sdg",
    "t": "t", "tdg": "tdg",
    "rz": "rz", "ry": "ry",
    "cx": "cnot", "swap": "swap", "ccx": "ccx",
}

# Braket's local OpenQASM interpreter does not define the qelib1 aliases
# sdg/tdg. rz is equivalent for standalone gates up to an unobservable global
# phase, so use it instead of relying on stdgates.inc being available.
_QASM3_RZ_ALIASES = {
    "sdg": -math.pi / 2,
    "tdg": -math.pi / 4,
}


def _format_angle(val: float) -> str:
    import math
    if abs(val) < 1e-12:
        return "0"
    ratio = val / math.pi
    for num, den in [(1,1), (-1,1), (1,2), (-1,2), (1,4), (-1,4)]:
        if abs(ratio - num / den) < 1e-10:
            sign = "-" if num < 0 else ""
            return f"{sign}pi" if abs(num) == 1 and den == 1 else f"{sign}pi/{den}"
    return repr(val)


def _decompose_cu1(gate: GateOp):
    """Decompose cu1(theta) into phase rotations + cnot.

    The qelib1 identity uses u1/p. Braket's local interpreter defines rz but
    not p; replacing each p(theta) with rz(theta) differs only by an overall
    global phase, so every measurement probability is unchanged.
    """
    theta = gate.params[0]
    a, b = gate.qubits[0], gate.qubits[1]
    return [
        ("rz", [a], [theta / 2]),
        ("cnot", [a, b], []),
        ("rz", [b], [-theta / 2]),
        ("cnot", [a, b], []),
        ("rz", [b], [theta / 2]),
    ]


def _decompose_ccx(gate: GateOp):
    """Return the official Toffoli identity supported by Braket locally."""
    a, b, c = gate.qubits
    return [
        ("h", [c], []),
        ("cnot", [b, c], []),
        ("rz", [c], [-math.pi / 4]),
        ("cnot", [a, c], []),
        ("t", [c], []),
        ("cnot", [b, c], []),
        ("rz", [c], [-math.pi / 4]),
        ("cnot", [a, c], []),
        ("t", [b], []),
        ("t", [c], []),
        ("h", [c], []),
        ("cnot", [a, b], []),
        ("t", [a], []),
        ("rz", [b], [-math.pi / 4]),
        ("cnot", [a, b], []),
    ]


def transpile_to_braket(circuit: Circuit) -> str:
    """Convert Circuit IR to OpenQASM 3.0 text for Braket."""
    _validate_circuit(circuit)
    lines = ["OPENQASM 3.0;", 'include "stdgates.inc";']
    lines.append(f"qubit[{circuit.num_qubits}] q;")
    lines.append(f"bit[{circuit.num_clbits}] c;")

    for gate in circuit.gates:
        if gate.name in _QASM3_RZ_ALIASES:
            qubit = gate.qubits[0]
            lines.append(
                f"rz({_format_angle(_QASM3_RZ_ALIASES[gate.name])}) q[{qubit}];"
            )
            continue

        if gate.name == "cu1":
            for gname, gqubits, gparams in _decompose_cu1(gate):
                qs = ", ".join(f"q[{q}]" for q in gqubits)
                if gparams:
                    lines.append(f"{gname}({_format_angle(gparams[0])}) {qs};")
                else:
                    lines.append(f"{gname} {qs};")
            continue

        if gate.name == "ccx":
            for gname, gqubits, gparams in _decompose_ccx(gate):
                qs = ", ".join(f"q[{q}]" for q in gqubits)
                if gparams:
                    lines.append(
                        f"{gname}({_format_angle(gparams[0])}) {qs};"
                    )
                else:
                    lines.append(f"{gname} {qs};")
            continue

        q3_name = _QASM3_GATE_NAME.get(gate.name, gate.name)
        qs = ", ".join(f"q[{q}]" for q in gate.qubits)
        if gate.params:
            ps = ", ".join(_format_angle(p) for p in gate.params)
            lines.append(f"{q3_name}({ps}) {qs};")
        else:
            lines.append(f"{q3_name} {qs};")

    for qubit, clbit in circuit.measurements:
        lines.append(f"c[{clbit}] = measure q[{qubit}];")

    return "\n".join(lines) + "\n"


def _validate_circuit(circuit: Circuit) -> None:
    if circuit.num_qubits < 1 or circuit.num_clbits < 1:
        raise ValueError("circuit must declare positive-size q and c registers")
    if not circuit.measurements:
        raise ValueError("circuit must contain at least one measurement")


def _strip_include(qasm3: str) -> str:
    """Remove include directive - Braket local simulator has gates built in."""
    return re.sub(r'include\s+"[^"]+"\s*;\n?', "", qasm3)


def _normalize_braket_counts(raw_counts, circuit: Circuit):
    """Map Braket's measurement-order key onto the declared c register."""
    normalized = {}
    measurement_count = len(circuit.measurements)
    for raw_key, count in raw_counts.items():
        bits = ["0"] * circuit.num_clbits
        key = str(raw_key).zfill(measurement_count)
        for index, (_, clbit) in enumerate(circuit.measurements):
            value = key[index]
            bits[circuit.num_clbits - 1 - clbit] = value
        normalized_key = "".join(bits)
        normalized[normalized_key] = normalized.get(normalized_key, 0) + count
    return normalized


def run_on_braket(circuit: Circuit, shots: int = 8192) -> Dict:
    """Run circuit on Braket LocalSimulator, return unified schema."""
    _validate_circuit(circuit)
    if not isinstance(shots, int) or isinstance(shots, bool) or shots < 1:
        raise ValueError("shots must be a positive integer")
    from braket.devices import LocalSimulator
    from braket.ir.openqasm import Program

    qasm3 = transpile_to_braket(circuit)
    # Strip include for local simulator compatibility
    qasm3_clean = _strip_include(qasm3)

    device = LocalSimulator()
    program = Program(source=qasm3_clean)
    task = device.run(program, shots=shots)
    result = task.result()

    raw_counts = result.measurement_counts

    normalized = _normalize_braket_counts(raw_counts, circuit)

    return {
        "backend": "braket_local_simulator",
        "job_id": "braket-local-{:04x}".format(hash(qasm3) & 0xFFFF),
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
