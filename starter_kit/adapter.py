#!/usr/bin/env python3
"""LoomQ submission adapter - L1 implementation."""

from typing import Any, Dict, List, Tuple

# Support both package import (from starter_kit) and direct import
try:
    from .qasm_parser import parse_qasm
except ImportError:
    from qasm_parser import parse_qasm


SUPPORTED_TARGETS = ("spinq", "originq", "braket")


def transpile(qasm_str: str, target: str) -> str:
    """Translate OpenQASM 2.0 into the target backend's native representation."""
    circuit = parse_qasm(qasm_str)

    if target == "braket":
        try:
            from .backends.braket_backend import transpile_to_braket
        except ImportError:
            from backends.braket_backend import transpile_to_braket
        return transpile_to_braket(circuit)
    elif target == "spinq":
        try:
            from .backends.spinq_backend import transpile_to_spinq
        except ImportError:
            from backends.spinq_backend import transpile_to_spinq
        return transpile_to_spinq(circuit)
    elif target == "originq":
        raise NotImplementedError("OriginQ backend not yet implemented")
    else:
        raise ValueError("unknown target '%s'; supported: %s" % (target, SUPPORTED_TARGETS))


def run(qasm_str: str, target: str, shots: int) -> Dict[str, Any]:
    """Execute a circuit and return the unified result schema."""
    circuit = parse_qasm(qasm_str)

    if target == "braket":
        try:
            from .backends.braket_backend import run_on_braket
        except ImportError:
            from backends.braket_backend import run_on_braket
        return run_on_braket(circuit, shots)
    elif target == "spinq":
        try:
            from .backends.spinq_backend import run_on_spinq
        except ImportError:
            from backends.spinq_backend import run_on_spinq
        return run_on_spinq(circuit, shots)
    elif target == "originq":
        raise NotImplementedError("OriginQ backend not yet implemented")
    else:
        raise ValueError("unknown target '%s'; supported: %s" % (target, SUPPORTED_TARGETS))


def agent_chat(prompt: str) -> str:
    """Optional L2 entry point using the documented LOOMQ_LLM_* environment."""
    raise NotImplementedError("L2 is optional; implement agent_chat(prompt) to enter")


def compile_hybrid(hybrid_qasm_str: str) -> Tuple[List[str], str]:
    """Optional L3 entry point."""
    raise NotImplementedError(
        "L3 is optional; implement compile_hybrid(hybrid_qasm_str) to enter"
    )
