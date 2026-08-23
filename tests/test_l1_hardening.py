#!/usr/bin/env python3
"""Local regression tests for L1 hidden-circuit behavior."""

import math
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "starter_kit"))

from adapter import transpile  # noqa: E402
from backends.braket_backend import _normalize_braket_counts  # noqa: E402
from backends.spinq_backend import _execution_qasm  # noqa: E402
from backends.spinq_backend import _normalize_spinq_counts  # noqa: E402
from qasm_parser import parse_qasm  # noqa: E402


def read_fixture(name):
    return (ROOT / "starter_kit" / "circuits" / name).read_text(encoding="utf-8")


def circuit_with(body):
    return """
OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];
creg c[3];
%s
""" % body.strip()


class L1ParserTests(unittest.TestCase):
    def test_all_twelve_official_gates_are_preserved_in_order(self):
        circuit = parse_qasm(read_fixture("l1_all_gates.qasm"))
        expected = [
            "h", "x", "s", "sdg", "t", "tdg", "rz", "ry",
            "cu1", "cx", "swap", "ccx",
        ]
        self.assertEqual([gate.name for gate in circuit.gates], expected)
        self.assertEqual(circuit.num_qubits, 3)
        self.assertEqual(circuit.num_clbits, 3)
        self.assertEqual(circuit.measurements, [(0, 0), (1, 1), (2, 2)])

    def test_non_whitelist_gates_are_rejected(self):
        for body in (
            "y q[0];",
            "z q[0];",
            "rx(0.5) q[0];",
            "u1(pi) q[0];",
        ):
            with self.assertRaisesRegex(ValueError, "unsupported gate", msg=body):
                parse_qasm(circuit_with(body))

    def test_wrong_gate_arity_and_parameter_count_are_rejected(self):
        invalid_bodies = (
            "cx q[0];",
            "ccx q[0], q[1];",
            "h q[0], q[1];",
            "rz q[0];",
            "rz(0.1, 0.2) q[0];",
            "cu1(0.1) q[0];",
            "swap q[0];",
        )
        for body in invalid_bodies:
            with self.assertRaisesRegex(ValueError, "expects", msg=body):
                parse_qasm(circuit_with(body))

    def test_register_bounds_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "qubit index .* out of range"):
            parse_qasm(circuit_with("h q[3];"))
        with self.assertRaisesRegex(ValueError, "classical bit index .* out of range"):
            parse_qasm(circuit_with("measure q[0] -> c[3];"))

    def test_numeric_expressions_use_safe_parser(self):
        circuit = parse_qasm(circuit_with("rz(2*pi/4) q[0]; measure q[0] -> c[0];"))
        self.assertAlmostEqual(circuit.gates[0].params[0], math.pi / 2)

        for expression in (
            "__import__('os')",
            "sin(pi)",
            "1 if True else 2",
            "1/0",
            "pi pi",
        ):
            with self.assertRaisesRegex(ValueError, "numeric expression", msg=expression):
                parse_qasm(circuit_with("rz(%s) q[0];" % expression))

    def test_explicit_and_partial_measurement_maps_are_preserved(self):
        asymmetric = parse_qasm(read_fixture("l1_asymmetric_measurement.qasm"))
        self.assertEqual(asymmetric.measurements, [(2, 0), (0, 2)])

        partial = parse_qasm(read_fixture("l1_partial_measurement.qasm"))
        self.assertEqual(partial.measurements, [(1, 0)])

    def test_no_implicit_measurement_is_invented(self):
        source = circuit_with("h q[0];")
        with self.assertRaisesRegex(ValueError, "at least one measurement"):
            parse_qasm(source)

    def test_gates_after_measurement_are_rejected(self):
        source = circuit_with("h q[0]; measure q[0] -> c[0]; x q[1];")
        with self.assertRaisesRegex(ValueError, "gate statements must precede measurement"):
            parse_qasm(source)

    def test_multiple_registers_are_flattened_consistently(self):
        source = """
OPENQASM 2.0;
include "qelib1.inc";
qreg qa[2];
qreg qb[1];
creg ca[1];
creg cb[1];
h qa[1];
cx qa[0], qb[0];
measure qa[1] -> ca[0];
measure qb[0] -> cb[0];
"""
        circuit = parse_qasm(source)
        self.assertEqual(circuit.num_qubits, 3)
        self.assertEqual(circuit.num_clbits, 2)
        self.assertEqual([(gate.name, gate.qubits) for gate in circuit.gates], [
            ("h", [1]),
            ("cx", [0, 2]),
        ])
        self.assertEqual(circuit.measurements, [(1, 0), (2, 1)])


class L1TranspilerTests(unittest.TestCase):
    def test_all_official_gates_transpile_to_spinq(self):
        output = transpile(read_fixture("l1_all_gates.qasm"), "spinq")
        self.assertIn("qreg q[3];", output)
        self.assertIn("creg c[3];", output)
        self.assertIn("rz(pi/2) q[0];", output)
        self.assertIn("cu1(pi/2) q[0], q[1];", output)
        self.assertIn("ccx q[0], q[1], q[2];", output)
        self.assertIn("measure q[0] -> c[0];", output)

    def test_all_official_gates_transpile_to_braket(self):
        output = transpile(read_fixture("l1_all_gates.qasm"), "braket")
        self.assertIn("qubit[3] q;", output)
        self.assertIn("bit[3] c;", output)
        self.assertIn("rz(-pi/2) q[0];", output)
        self.assertIn("rz(-pi/4) q[0];", output)
        self.assertIn("rz(pi/2) q[0];", output)
        self.assertIn("cnot q[0], q[1];", output)
        self.assertIn("swap q[0], q[2];", output)
        self.assertNotIn("ccx ", output)
        self.assertEqual(output.count("cnot "), 9)
        self.assertIn("c[0] = measure q[0];", output)

        # cu1 is not native in the Braket target; use the official phase
        # identity, expressed with Braket's supported rz gate.
        self.assertNotIn("cu1 ", output)
        self.assertIn("rz(pi/4) q[0];", output)
        self.assertIn("rz(-pi/4) q[1];", output)
        self.assertIn("rz(pi/4) q[1];", output)

    def test_measurement_mapping_is_not_collapsed(self):
        source = read_fixture("l1_asymmetric_measurement.qasm")
        spinq = transpile(source, "spinq")
        braket = transpile(source, "braket")
        self.assertIn("measure q[2] -> c[0];", spinq)
        self.assertIn("measure q[0] -> c[2];", spinq)
        self.assertIn("c[0] = measure q[2];", braket)
        self.assertIn("c[2] = measure q[0];", braket)
        self.assertNotIn("measure q -> c;", spinq)
        self.assertNotIn("c = measure q;", braket)

    def test_partial_measurement_emits_only_the_declared_measurement(self):
        source = read_fixture("l1_partial_measurement.qasm")
        spinq = transpile(source, "spinq")
        braket = transpile(source, "braket")
        self.assertIn("measure q[1] -> c[0];", spinq)
        self.assertNotIn("measure q[0] -> c[1];", spinq)
        self.assertIn("c[0] = measure q[1];", braket)
        self.assertNotIn("c[1] = measure q[0];", braket)

    def test_braket_counts_honor_destination_bits(self):
        identity = parse_qasm(
            circuit_with("x q[0]; measure q[0] -> c[0]; measure q[1] -> c[1];")
        )
        self.assertEqual(_normalize_braket_counts({"10": 8}, identity), {"001": 8})

        asymmetric = parse_qasm(read_fixture("l1_asymmetric_measurement.qasm"))
        self.assertEqual(_normalize_braket_counts({"01": 8}, asymmetric), {"100": 8})

        partial = parse_qasm(read_fixture("l1_partial_measurement.qasm"))
        self.assertEqual(_normalize_braket_counts({"1": 8}, partial), {"01": 8})

    def test_spinq_counts_honor_destination_bits(self):
        identity = parse_qasm(
            circuit_with("x q[0]; measure q[0] -> c[0]; measure q[1] -> c[1];")
        )
        # SpinQ's execution key is q0 q1 q2 even when the source measured only
        # a subset; execution measures all qubits before applying destinations.
        self.assertEqual(_normalize_spinq_counts({"100": 8}, identity), {"001": 8})

        asymmetric = parse_qasm(read_fixture("l1_asymmetric_measurement.qasm"))
        self.assertEqual(_normalize_spinq_counts({"100": 8}, asymmetric), {"100": 8})

        partial = parse_qasm(read_fixture("l1_partial_measurement.qasm"))
        self.assertEqual(_normalize_spinq_counts({"01": 8}, partial), {"01": 8})

    def test_spinq_execution_meets_classical_register_size(self):
        source = """
OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];
creg c[1];
x q[0];
measure q[0] -> c[0];
"""
        circuit = parse_qasm(source)
        self.assertIn("creg c[1];", transpile(source, "spinq"))
        execution = _execution_qasm(circuit)
        self.assertIn("creg c[3];", execution)
        self.assertIn("measure q[2] -> c[2];", execution)


if __name__ == "__main__":
    unittest.main()
