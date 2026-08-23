"""Strict OpenQASM 2.0 parser for the LoomQ L1 contract.

The contest subset is intentionally small: register declarations, the official
twelve gates, and measurements.  Parsing rejects all other syntax before a
backend can emit an inequivalent program.
"""

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Tuple


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_INDEXED_REF = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\[\s*([0-9]+)\s*\]")
_VERSION = re.compile(r"OPENQASM\s+2\.0")
_INCLUDE = re.compile(r'include\s+"[^"]+"')
_QREG = re.compile(r"qreg\s+([A-Za-z_][A-Za-z0-9_]*)\s*\[\s*([0-9]+)\s*\]")
_CREG = re.compile(r"creg\s+([A-Za-z_][A-Za-z0-9_]*)\s*\[\s*([0-9]+)\s*\]")
_MEASURE = re.compile(
    r"measure\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)(?:\[\s*([0-9]+)\s*\])?\s*->\s*"
    r"([A-Za-z_][A-Za-z0-9_]*)(?:\[\s*([0-9]+)\s*\])?"
)
_PARAM_GATE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s+(.+)", re.DOTALL
)


@dataclass
class GateOp:
    name: str
    qubits: List[int]
    params: List[float] = field(default_factory=list)


@dataclass
class Circuit:
    qregs: List[Tuple[str, int]] = field(default_factory=list)
    cregs: List[Tuple[str, int]] = field(default_factory=list)
    gates: List[GateOp] = field(default_factory=list)
    measurements: List[Tuple[int, int]] = field(default_factory=list)
    num_qubits: int = 0
    num_clbits: int = 0


# name: (parameter count, qubit count)
_GATE_CONTRACT = {
    "h": (0, 1),
    "x": (0, 1),
    "s": (0, 1),
    "sdg": (0, 1),
    "t": (0, 1),
    "tdg": (0, 1),
    "rz": (1, 1),
    "ry": (1, 1),
    "cx": (0, 2),
    "cu1": (1, 2),
    "swap": (0, 2),
    "ccx": (0, 3),
}


class _ExpressionParser:
    """Small arithmetic parser; never executes Python source."""

    _TOKEN = re.compile(
        r"(?P<number>(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)"
        r"|(?P<name>pi)"
        r"|(?P<op>[()+\-*/])"
        r"|(?P<space>\s+)"
    )

    def __init__(self, expression: str):
        self.tokens = self._tokenize(expression)
        self.position = 0

    @classmethod
    def _tokenize(cls, expression):
        tokens = []
        position = 0
        while position < len(expression):
            match = cls._TOKEN.match(expression, position)
            if not match:
                raise ValueError(
                    "invalid character %r in numeric expression" % expression[position]
                )
            if match.lastgroup != "space":
                tokens.append((match.lastgroup, match.group()))
            position = match.end()
        return tokens

    def _peek(self):
        if self.position >= len(self.tokens):
            return None
        return self.tokens[self.position]

    def _take(self, expected=None):
        token = self._peek()
        if token is None or (expected is not None and token != expected):
            raise ValueError("invalid numeric expression")
        self.position += 1
        return token

    def parse(self):
        if not self.tokens:
            raise ValueError("empty numeric expression")
        value = self._expression()
        if self._peek() is not None:
            raise ValueError("invalid numeric expression")
        if not math.isfinite(value):
            raise ValueError("numeric expression must be finite")
        return value

    def _expression(self):
        value = self._term()
        while self._peek() in (("op", "+"), ("op", "-")):
            operator = self._take()[1]
            right = self._term()
            value = value + right if operator == "+" else value - right
        return value

    def _term(self):
        value = self._factor()
        while self._peek() in (("op", "*"), ("op", "/")):
            operator = self._take()[1]
            right = self._factor()
            if operator == "*":
                value *= right
            else:
                if right == 0:
                    raise ValueError("division by zero in numeric expression")
                value /= right
        return value

    def _factor(self):
        if self._peek() == ("op", "+"):
            self._take()
            return self._factor()
        if self._peek() == ("op", "-"):
            self._take()
            return -self._factor()
        return self._primary()

    def _primary(self):
        token = self._take()
        kind, text = token
        if kind == "number":
            return float(text)
        if kind == "name":
            return math.pi
        if token == ("op", "("):
            value = self._expression()
            self._take(("op", ")"))
            return value
        raise ValueError("invalid numeric expression")


def _eval_expr(expression: str) -> float:
    return _ExpressionParser(expression).parse()


def _resolve_qubit(
    ref: str, qreg_offsets: Mapping[str, int], qreg_sizes: Mapping[str, int]
) -> int:
    match = _INDEXED_REF.fullmatch(ref.strip())
    if not match:
        raise ValueError("cannot parse qubit reference '%s'" % ref.strip())
    name, index_text = match.groups()
    index = int(index_text)
    if name not in qreg_offsets:
        raise ValueError("unknown qreg '%s'" % name)
    if index >= qreg_sizes[name]:
        raise ValueError(
            "qubit index %d out of range for qreg '%s[%d]'"
            % (index, name, qreg_sizes[name])
        )
    return qreg_offsets[name] + index


def _add_register(
    name: str,
    size: int,
    circuit_member: List[Tuple[str, int]],
    offsets: Dict[str, int],
    sizes: Dict[str, int],
    current_size: int,
    kind: str,
) -> int:
    if not _IDENTIFIER.fullmatch(name):
        raise ValueError("invalid %s register name '%s'" % (kind, name))
    if size < 1:
        raise ValueError("%s register '%s' must have at least one bit" % (kind, name))
    if name in offsets:
        raise ValueError("duplicate %s register '%s'" % (kind, name))
    offsets[name] = current_size
    sizes[name] = size
    circuit_member.append((name, size))
    return current_size + size


def _split_top_level(text: str) -> List[str]:
    pieces = []
    start = 0
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("unbalanced parentheses in gate parameters")
        elif char == "," and depth == 0:
            pieces.append(text[start:index].strip())
            start = index + 1
    if depth:
        raise ValueError("unbalanced parentheses in gate parameters")
    pieces.append(text[start:].strip())
    return pieces


def _parse_gate(
    stmt: str,
    qreg_offsets: Mapping[str, int],
    qreg_sizes: Mapping[str, int],
) -> GateOp:
    match = _PARAM_GATE.fullmatch(stmt)
    if match:
        name, params_text, operands_text = match.groups()
        params = [_eval_expr(part) for part in _split_top_level(params_text)]
    else:
        pieces = stmt.split(None, 1)
        if len(pieces) != 2:
            raise ValueError("gate statement '%s' has no qubits" % stmt)
        name, operands_text = pieces
        params = []

    if name not in _GATE_CONTRACT:
        raise ValueError("unsupported gate '%s'; official whitelist: %s" % (name, ", ".join(_GATE_CONTRACT)))
    expected_params, expected_qubits = _GATE_CONTRACT[name]
    if len(params) != expected_params:
        raise ValueError(
            "gate '%s' expects %d parameter(s), got %d"
            % (name, expected_params, len(params))
        )

    operand_parts = [part.strip() for part in operands_text.split(",")]
    if len(operand_parts) != expected_qubits:
        raise ValueError(
            "gate '%s' expects %d qubit(s), got %d"
            % (name, expected_qubits, len(operand_parts))
        )
    qubits = [
        _resolve_qubit(part, qreg_offsets, qreg_sizes) for part in operand_parts
    ]
    return GateOp(name=name, qubits=qubits, params=params)


def parse_qasm(qasm_str: str) -> Circuit:
    """Parse and validate one contest OpenQASM 2.0 circuit."""
    if not isinstance(qasm_str, str):
        raise ValueError("OpenQASM input must be a string")

    # Contest comments run to the end of the line. Joining preserves statements
    # that are split across source lines.
    logical_lines = []
    for line in qasm_str.splitlines():
        comment = line.find("//")
        if comment >= 0:
            line = line[:comment]
        if line.strip():
            logical_lines.append(line.strip())
    statements = [part.strip() for part in " ".join(logical_lines).split(";") if part]

    if not statements or not _VERSION.fullmatch(statements[0]):
        raise ValueError("OpenQASM 2.0 version declaration is required")
    statement_index = 1
    if statement_index < len(statements) and _INCLUDE.fullmatch(statements[statement_index]):
        statement_index += 1

    circuit = Circuit()
    qreg_offsets: Dict[str, int] = {}
    qreg_sizes: Dict[str, int] = {}
    creg_offsets: Dict[str, int] = {}
    creg_sizes: Dict[str, int] = {}
    measurement_destinations = set()
    saw_measurement = False

    for stmt in statements[statement_index:]:
        match = _QREG.fullmatch(stmt)
        if match:
            name, size_text = match.groups()
            circuit.num_qubits = _add_register(
                name,
                int(size_text),
                circuit.qregs,
                qreg_offsets,
                qreg_sizes,
                circuit.num_qubits,
                "quantum",
            )
            continue

        match = _CREG.fullmatch(stmt)
        if match:
            name, size_text = match.groups()
            circuit.num_clbits = _add_register(
                name,
                int(size_text),
                circuit.cregs,
                creg_offsets,
                creg_sizes,
                circuit.num_clbits,
                "classical",
            )
            continue

        if stmt.startswith("measure"):
            match = _MEASURE.fullmatch(stmt)
            if not match:
                raise ValueError("cannot parse measurement '%s'" % stmt)
            qname, qindex_text, cname, cindex_text = match.groups()
            if qname not in qreg_offsets:
                raise ValueError("unknown qreg '%s' in measurement" % qname)
            if cname not in creg_offsets:
                raise ValueError("unknown creg '%s' in measurement" % cname)

            if qindex_text is None and cindex_text is None:
                if qreg_sizes[qname] != creg_sizes[cname]:
                    raise ValueError(
                        "register measurement requires equal sizes: '%s[%d]' -> '%s[%d]'"
                        % (
                            qname,
                            qreg_sizes[qname],
                            cname,
                            creg_sizes[cname],
                        )
                    )
                q_indices = range(qreg_sizes[qname])
                c_indices = range(creg_sizes[cname])
            elif qindex_text is not None and cindex_text is not None:
                q_index = int(qindex_text)
                c_index = int(cindex_text)
                if q_index >= qreg_sizes[qname]:
                    raise ValueError(
                        "qubit index %d out of range for qreg '%s[%d]'"
                        % (q_index, qname, qreg_sizes[qname])
                    )
                if c_index >= creg_sizes[cname]:
                    raise ValueError(
                        "classical bit index %d out of range for creg '%s[%d]'"
                        % (c_index, cname, creg_sizes[cname])
                    )
                q_indices = [q_index]
                c_indices = [c_index]
            else:
                raise ValueError(
                    "measurement operands must both be registers or both be indexed bits: '%s'"
                    % stmt
                )

            for q_index, c_index in zip(q_indices, c_indices):
                destination = creg_offsets[cname] + c_index
                if destination in measurement_destinations:
                    raise ValueError("classical bit %d is measured more than once" % destination)
                measurement_destinations.add(destination)
                circuit.measurements.append(
                    (qreg_offsets[qname] + q_index, destination)
                )
            saw_measurement = True
            continue

        if saw_measurement:
            raise ValueError("gate statements must precede measurement statements")
        circuit.gates.append(_parse_gate(stmt, qreg_offsets, qreg_sizes))

    if not circuit.qregs:
        raise ValueError("circuit must declare at least one qreg")
    if not circuit.cregs:
        raise ValueError("circuit must declare at least one creg")
    if not circuit.measurements:
        raise ValueError("circuit must contain at least one measurement")
    return circuit
