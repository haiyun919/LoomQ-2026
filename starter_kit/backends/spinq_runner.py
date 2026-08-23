#!/usr/bin/env python3
"""SpinQ subprocess runner - executes a QASM circuit on SpinQit simulator.

Called by spinq_backend.py via subprocess to isolate antlr4 version.
Outputs JSON result to stdout.
"""

import json
import os
import sys
import tempfile


def run_on_spinq(qasm_str, shots=8192):
    import spinqit as sq
    from spinqit import BasicSimulatorConfig, get_basic_simulator, get_compiler

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".qasm", delete=False, encoding="utf-8")
    try:
        tmp.write(qasm_str)
        tmp.close()
        compiler = get_compiler("qasm")
        ir = compiler.compile(tmp.name, 0)
        engine = get_basic_simulator()
        config = BasicSimulatorConfig()
        config.configure_shots(shots)
        result = engine.execute(ir, config)
        counts = result.counts
    finally:
        os.unlink(tmp.name)

    return {
        "counts": {str(k): v for k, v in counts.items()},
        "qubits_count": ir.qnum,
    }


if __name__ == "__main__":
    qasm_str = sys.argv[1]
    shots = int(sys.argv[2])
    result = run_on_spinq(qasm_str, shots)
    print(json.dumps(result))
