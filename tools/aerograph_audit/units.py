# Vendored read-only inference helper from AeroGraph semantic-directory/src/expanded_units.py.
# Audit owns this copy; no upstream code is imported or executed.
"""Strict value-type and unit inference for expanded semantic expressions.

The compiler uses this module only to prove that an AST is well typed.  A
missing unit is unknown, never dimensionless.  Unit conversion is also never
implicit: equal dimensions with different scales (for example ``m`` and
``cm``) are deliberately incompatible until an authored formula applies an
explicit ratio.
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction


class UnitInferenceError(ValueError):
    """The expression's declared types or units cannot establish a result."""


@dataclass(frozen=True)
class _Unit:
    dimensions: tuple[tuple[str, Fraction], ...]
    scale: Decimal = Decimal(1)
    flavor: str | None = None

    @staticmethod
    def make(dimensions=None, scale=Decimal(1), flavor=None):
        dimensions = dimensions or {}
        cleaned = tuple(
            sorted(
                (name, Fraction(power)) for name, power in dimensions.items() if power
            )
        )
        return _Unit(cleaned, Decimal(scale), flavor)

    def powers(self):
        return dict(self.dimensions)


_ONE = _Unit.make()


def _u(dimensions=None, scale="1", flavor=None):
    return _Unit.make(dimensions, Decimal(scale), flavor)


_NAMED = {
    "1": _ONE,
    "m": _u({"L": 1}),
    "kg": _u({"M": 1}),
    "s": _u({"T": 1}),
    "A": _u({"I": 1}),
    "K": _u({"Theta": 1}),
    "mol": _u({"Amount": 1}),
    "cd": _u({"Luminosity": 1}),
    # SI treats plane and solid angle as quantities of dimension one.  Their
    # source symbols remain on input fields for traceability, while algebra may
    # correctly reduce W/(m^2 sr) to W/m^2 and rad^2 to 1.
    "rad": _ONE,
    "sr": _ONE,
    "bit": _u({"Information": 1}),
    "By": _u({"Information": 1}, "8"),
    "byte": _u({"Information": 1}, "8"),
    "min": _u({"T": 1}, "60"),
    "h": _u({"T": 1}, "3600"),
    "d": _u({"T": 1}, "86400"),
    "L": _u({"L": 3}, "0.001"),
    "Hz": _u({"T": -1}),
    "N": _u({"M": 1, "L": 1, "T": -2}),
    "Pa": _u({"M": 1, "L": -1, "T": -2}),
    "J": _u({"M": 1, "L": 2, "T": -2}),
    "W": _u({"M": 1, "L": 2, "T": -3}),
    "W/m^2": _u({"M": 1, "T": -3}),
    "Wh": _u({"M": 1, "L": 2, "T": -2}, "3600"),
    "Ah": _u({"I": 1, "T": 1}, "3600"),
    "C": _u({"I": 1, "T": 1}),
    "V": _u({"M": 1, "L": 2, "T": -3, "I": -1}),
    "Wb": _u({"M": 1, "L": 2, "T": -2, "I": -1}),
    "T": _u({"M": 1, "T": -2, "I": -1}),
    "Ohm": _u({"M": 1, "L": 2, "T": -3, "I": -2}),
    "S": _u({"M": -1, "L": -2, "T": 3, "I": 2}),
    "F": _u({"M": -1, "L": -2, "T": 4, "I": 2}),
    "percent": _u(scale="0.01"),
    "%": _u(scale="0.01"),
    "ppm": _u(scale="0.000001"),
    "bar": _u({"M": 1, "L": -1, "T": -2}, "100000"),
    # Temperature offsets and logarithmic quantities cannot participate in
    # ordinary multiplicative unit algebra.
    "degC": _u({"Theta": 1}, flavor="affine:celsius"),
    "Cel": _u({"Theta": 1}, flavor="affine:celsius"),
    "dB": _u(flavor="log:dB"),
    "dBi": _u(flavor="log:dBi"),
    "dBm": _u({"M": 1, "L": 2, "T": -3}, flavor="log:dBm"),
    "dB-Hz": _u({"T": -1}, flavor="log:dB-Hz"),
    "dB/s": _u({"T": -1}, flavor="log-rate:dB/s"),
    "%RH": _u({"RelativeHumidity": 1}),
    "rpm": _u({"Rotation": 1, "T": -1}, flavor="rate:rpm"),
    "deg": _u(flavor="angle:degree"),
    "degree": _u(flavor="angle:degree"),
}

# These are semantic counting units.  Keeping their identities prevents, for
# example, a packet count from being compared to a person count merely because
# both happen to be integers.
for _name in (
    "count",
    "packet",
    "person",
    "animal",
    "sample",
    "slot",
    "pixel",
    "px",
    "core",
    "resource",
    "work",
    "work_item",
    "work_unit",
    "cycle",
    "rank",
):
    _NAMED[_name] = _u({"semantic:" + _name: 1})

_PREFIXES = {
    "k": Decimal(1000),
    "c": Decimal("0.01"),
    "m": Decimal("0.001"),
    "u": Decimal("0.000001"),
    "µ": Decimal("0.000001"),
    "μ": Decimal("0.000001"),
    "n": Decimal("0.000000001"),
}
_PREFIXABLE = {
    "m",
    "g",
    "s",
    "A",
    "Hz",
    "N",
    "Pa",
    "J",
    "W",
    "Wh",
    "V",
    "T",
    "F",
    "L",
    "bit",
    "By",
}


def _mul(a, b):
    if a.flavor or b.flavor:
        raise UnitInferenceError(
            "affine/logarithmic units require an explicit conversion before multiplication"
        )
    powers = a.powers()
    for name, value in b.dimensions:
        powers[name] = powers.get(name, Fraction()) + value
    return _Unit.make(powers, a.scale * b.scale)


def _div(a, b):
    if a.flavor or b.flavor:
        raise UnitInferenceError(
            "affine/logarithmic units require an explicit conversion before division"
        )
    powers = a.powers()
    for name, value in b.dimensions:
        powers[name] = powers.get(name, Fraction()) - value
    return _Unit.make(powers, a.scale / b.scale)


def _decimal_power(value, power):
    power = Fraction(power)
    if power.denominator == 1:
        return value**power.numerator
    if power.denominator != 2:
        raise UnitInferenceError(
            "unit exponents are limited to integers and square roots"
        )
    with localcontext() as context:
        context.prec = 50
        root = value.sqrt()
        result = root ** abs(power.numerator)
        return Decimal(1) / result if power.numerator < 0 else result


def _pow(unit, power):
    power = Fraction(power)
    if unit.flavor:
        if power == 1:
            return unit
        raise UnitInferenceError("affine/logarithmic units cannot be raised to a power")
    return _Unit.make(
        {name: value * power for name, value in unit.dimensions},
        _decimal_power(unit.scale, power),
    )


def _unit_token(name):
    if name in _NAMED:
        return _NAMED[name]
    # gram is represented relative to the SI mass base (kg).
    if name == "g":
        return _u({"M": 1}, "0.001")
    for prefix in sorted(_PREFIXES, key=len, reverse=True):
        if name.startswith(prefix) and name[len(prefix) :] in _PREFIXABLE:
            base_name = name[len(prefix) :]
            base = _unit_token(base_name)
            if base.flavor:
                break
            return _Unit.make(base.powers(), base.scale * _PREFIXES[prefix])
    # An unfamiliar atomic engineering unit is retained as an opaque
    # dimension.  It can be compared with exactly the same declaration, but it
    # is never silently treated as dimensionless or converted to another unit.
    return _u({"opaque:" + name: 1})


_TOKEN = re.compile(r"sqrt|[A-Za-z_%µμ][A-Za-z0-9_%µμ]*|1|[-+]?\d+(?:\.\d+)?|[*/()^]")


class _UnitParser:
    def __init__(self, text):
        self.text = text
        self.tokens = []
        end = 0
        for match in _TOKEN.finditer(text):
            if text[end : match.start()].strip():
                raise UnitInferenceError(f"unsupported unit syntax: {text!r}")
            self.tokens.append(match.group())
            end = match.end()
        if text[end:].strip() or not self.tokens:
            raise UnitInferenceError(f"unsupported unit syntax: {text!r}")
        self.index = 0

    def peek(self):
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self, expected=None):
        token = self.peek()
        if token is None or expected is not None and token != expected:
            raise UnitInferenceError(f"unsupported unit syntax: {self.text!r}")
        self.index += 1
        return token

    def parse(self):
        result = self.expression()
        if self.peek() is not None:
            raise UnitInferenceError(f"unsupported unit syntax: {self.text!r}")
        return result

    def expression(self):
        value = self.factor()
        while self.peek() is not None and self.peek() != ")":
            token = self.peek()
            if token == "*":
                self.take("*")
                value = _mul(value, self.factor())
            elif token == "/":
                self.take("/")
                value = _div(value, self.factor())
            else:  # Whitespace or adjacency means multiplication.
                value = _mul(value, self.factor())
        return value

    def factor(self):
        token = self.take()
        if token == "(":
            value = self.expression()
            self.take(")")
        elif token == "sqrt":
            self.take("(")
            value = _pow(self.expression(), Fraction(1, 2))
            self.take(")")
        elif token == "1":
            value = _ONE
        elif re.fullmatch(r"[-+]?\d+(?:\.\d+)?", token):
            try:
                value = _u(scale=token)
            except InvalidOperation as error:
                raise UnitInferenceError(f"invalid unit scale: {token}") from error
        elif token not in ("*", "/", ")", "^"):
            value = _unit_token(token)
        else:
            raise UnitInferenceError(f"unsupported unit syntax: {self.text!r}")
        if self.peek() == "^":
            self.take("^")
            exponent = self.take()
            try:
                value = _pow(value, Fraction(exponent))
            except (ValueError, ZeroDivisionError) as error:
                raise UnitInferenceError(
                    f"invalid unit exponent: {exponent!r}"
                ) from error
        return value


def _normalize_unit_text(unit):
    text = unit.strip()
    text = text.replace("·", "*").replace("⋅", "*").replace("**", "^")
    text = text.replace("²", "^2").replace("³", "^3").replace("⁻", "-")
    # Common compact SI exponent notation: m3, m-2, s-1.
    text = re.sub(r"\b([A-Za-z_%µμ]+)([-+]?\d+)\b", r"\1^\2", text)
    return text


def parse_unit(unit):
    """Return a canonical internal unit, or ``None`` for an unknown unit.

    ``None`` deliberately remains unknown.  Only an explicit ``"1"`` means a
    dimensionless numeric value.
    """
    if unit is None:
        return None
    if isinstance(unit, dict):
        unit = unit.get("symbol")
    if not isinstance(unit, str) or not unit.strip():
        return None
    text = _normalize_unit_text(unit)
    if text in _NAMED:
        return _NAMED[text]
    # Source vocabularies contain descriptive/mixed-unit labels.  Preserve the
    # complete label as one opaque unit rather than misparsing parts as SI.
    if any(
        marker in text
        for marker in (",", ":", ".", "_or_", "unit from ", "由", "真实单位", "结构内")
    ):
        return _u({"opaque:" + text: 1})
    return _UnitParser(text).parse()


_PREFERRED = (
    "1",
    "m",
    "kg",
    "s",
    "A",
    "K",
    "mol",
    "cd",
    "rad",
    "sr",
    "bit",
    "By",
    "Hz",
    "N",
    "Pa",
    "J",
    "W",
    "W/m^2",
    "Wh",
    "Ah",
    "C",
    "V",
    "Wb",
    "T",
    "Ohm",
    "S",
    "F",
    "L",
    "min",
    "h",
    "d",
    "percent",
    "ppm",
    "degC",
    "dB",
    "dBi",
    "dBm",
    "dB-Hz",
    "dB/s",
    "%RH",
    "rpm",
    "deg",
    "degree",
    "bar",
    "count",
    "packet",
    "person",
    "animal",
    "sample",
    "slot",
    "pixel",
    "core",
    "resource",
    "work",
    "work_item",
    "work_unit",
    "cycle",
    "rank",
)


def _power_text(power):
    if power.denominator == 1:
        return str(power.numerator)
    return format(float(power), ".12g")


def format_unit(unit):
    if unit is None:
        return None
    for name in _PREFERRED:
        if _NAMED[name] == unit:
            return name
    positive, negative = [], []
    labels = {
        "L": "m",
        "M": "kg",
        "T": "s",
        "I": "A",
        "Theta": "K",
        "Amount": "mol",
        "Luminosity": "cd",
        "Angle": "rad",
        "SolidAngle": "sr",
        "Information": "bit",
    }
    for name, power in unit.dimensions:
        label = (
            name.removeprefix("opaque:")
            if name.startswith("opaque:")
            else labels.get(name, name.removeprefix("semantic:"))
        )
        target = positive if power > 0 else negative
        magnitude = abs(power)
        target.append(label if magnitude == 1 else label + "^" + _power_text(magnitude))
    numerator = "*".join(positive) or "1"
    if not negative:
        expression = numerator
    elif len(negative) == 1:
        expression = numerator + "/" + negative[0]
    else:
        expression = numerator + "/(" + "*".join(negative) + ")"
    if unit.scale != 1:
        scale = format(unit.scale, "f")
        if "." in scale:
            scale = scale.rstrip("0").rstrip(".")
        expression = scale + "*" + expression
    return expression


def units_compatible(left, right):
    """Exact compatibility without an implicit scale or offset conversion."""
    left, right = parse_unit(left), parse_unit(right)
    return left is not None and right is not None and left == right


def _schema_unit(schema, inherited=None):
    raw = schema.get("unit", schema.get("unitSymbol", inherited))
    return raw.get("symbol") if isinstance(raw, dict) else raw


def _branches(schema):
    return schema.get("oneOf", schema.get("anyOf", schema.get("branches", [])))


def _kind(schema):
    if not isinstance(schema, dict):
        raise UnitInferenceError("operator input schema must be an object")
    if _branches(schema):
        return "union"
    if "const" in schema:
        value = schema["const"]
        if type(value) is bool:
            return "boolean"
        if type(value) in (int, float):
            return "integer" if type(value) is int else "number"
        if isinstance(value, str):
            return "string"
        return (
            "record"
            if isinstance(value, dict)
            else "array"
            if isinstance(value, list)
            else "null"
        )
    if "enum" in schema or "values" in schema or schema.get("type") == "enum":
        values = schema.get("enum", schema.get("values", []))
        kinds = {_literal_kind(value) for value in values}
        if len(kinds) == 1:
            return next(iter(kinds))
        return "union"
    typ = schema.get("type")
    if isinstance(typ, list):
        return typ[0] if len(typ) == 1 else "union"
    if typ == "object":
        return "record"
    if (
        typ in ("ref", "reference")
        or schema.get("x-referenceTargets")
        or schema.get("identityComponents")
    ):
        return "reference"
    return typ


def _literal_kind(value):
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "record"
    return "null"


def _literal_schema(value):
    kind = _literal_kind(value)
    if kind in ("number", "integer"):
        if not math.isfinite(value):
            raise UnitInferenceError("numeric literals must be finite")
        return {"type": kind, "unit": "1", "zeroLiteral": value == 0}
    if kind == "array":
        schemas = [_literal_schema(item) for item in value]
        if not schemas:
            return {"type": "array"}
        signatures = {repr(schema): schema for schema in schemas}
        items = (
            next(iter(signatures.values()))
            if len(signatures) == 1
            else {"oneOf": list(signatures.values())}
        )
        return {"type": "array", "items": items}
    return {"type": kind, "const": copy.deepcopy(value)}


def _with_literal(schema, node):
    if isinstance(node, dict) and set(node) == {"literal"}:
        literal = _literal_schema(node["literal"])
        # The literal is authoritative; compilers often initially classify all
        # JSON numbers as simply "number" and arrays without item schemas.
        return literal
    if schema.get("zeroLiteral"):
        schema = dict(schema)
        schema.pop("zeroLiteral", None)
    return schema


@dataclass(frozen=True)
class _Numeric:
    kind: str
    unit: _Unit | None
    zero_literal: bool = False


def _numeric(schema, inherited=None):
    unit_text = _schema_unit(schema, inherited)
    branches = _branches(schema)
    if branches:
        parts = [_numeric(branch, unit_text) for branch in branches]
        if not parts:
            raise UnitInferenceError("numeric union has no declared branches")
        first = parts[0].unit
        if any(
            (part.unit is None) != (first is None)
            or part.unit is not None
            and part.unit != first
            for part in parts[1:]
        ):
            raise UnitInferenceError("numeric union branches have incompatible units")
        return _Numeric(
            "number" if any(part.kind == "number" for part in parts) else "integer",
            first,
            all(part.zero_literal for part in parts),
        )
    typ = schema.get("type")
    if isinstance(typ, list):
        if not typ or any(item not in ("number", "integer") for item in typ):
            raise UnitInferenceError("union is not purely numeric")
        kind = "number" if "number" in typ else "integer"
    else:
        kind = _kind(schema)
        if kind == "union":
            values = schema.get("enum", schema.get("values", []))
            if not values or any(
                type(value) not in (int, float) or type(value) is bool
                for value in values
            ):
                raise UnitInferenceError("union/enum is not purely numeric")
            kind = (
                "number" if any(type(value) is float for value in values) else "integer"
            )
        if kind not in ("number", "integer"):
            raise UnitInferenceError(f"operator requires numeric input, got {kind!r}")
    return _Numeric(kind, parse_unit(unit_text), bool(schema.get("zeroLiteral")))


def _require_arity(op, args, exact=None, minimum=None):
    if exact is not None and len(args) != exact:
        raise UnitInferenceError(f"{op} requires exactly {exact} arguments")
    if minimum is not None and len(args) < minimum:
        raise UnitInferenceError(f"{op} requires at least {minimum} arguments")


def _known_compatible(numbers, op):
    nonzero = [item for item in numbers if not item.zero_literal]
    if not nonzero:
        return _ONE
    # A wholly undeclared unit remains undeclared.  This validates the source
    # expression's type without inventing dimensionless semantics.  Mixing a
    # declared unit with an undeclared one is still an error.
    if all(item.unit is None for item in nonzero):
        return None
    if any(item.unit is None for item in nonzero):
        raise UnitInferenceError(f"{op} mixes a declared unit with a missing unit")
    selected = nonzero[0].unit
    if any(item.unit != selected for item in nonzero[1:]):
        raise UnitInferenceError(f"{op} has incompatible numeric units")
    # Zero is dimension-neutral only when another operand establishes a known
    # unit.  It must never turn an unknown unit into a known result.
    return selected


def _result(kind, unit=None, **extra):
    return {
        "type": kind,
        "unit": format_unit(unit) if isinstance(unit, _Unit) else unit,
        **extra,
    }


def _generic_kinds(schema):
    branches = _branches(schema)
    if branches:
        values = set()
        for branch in branches:
            values.update(_generic_kinds(branch))
        return values
    typ = schema.get("type")
    if isinstance(typ, list):
        values = set()
        for item in typ:
            values.add(
                "number"
                if item in ("number", "integer")
                else "record"
                if item == "object"
                else item
            )
        return values
    kind = _kind(schema)
    return {
        "number"
        if kind in ("number", "integer")
        else "string"
        if kind == "enum"
        else kind
    }


def _comparable_non_numeric(schemas, op):
    kinds = [_generic_kinds(schema) for schema in schemas]
    if any(len(value) != 1 for value in kinds):
        raise UnitInferenceError(f"{op} requires compatible, non-mixed value types")
    selected = next(iter(kinds[0]))
    if selected in (None, "null") or any(
        next(iter(value)) != selected for value in kinds[1:]
    ):
        raise UnitInferenceError(f"{op} has incompatible value types")
    for other in schemas[1:]:
        _deep_compatible(schemas[0], other, op)


def _deep_compatible(left, right, op):
    """Prove full-value equality compatibility, including records/arrays."""
    left_branches, right_branches = _branches(left), _branches(right)
    if left_branches or right_branches:
        left_options = left_branches or [left]
        right_options = right_branches or [right]
        if len(left_options) != len(right_options):
            raise UnitInferenceError(f"{op} has incompatible union structures")
        unused = list(right_options)
        for option in left_options:
            for index, candidate in enumerate(unused):
                try:
                    _deep_compatible(option, candidate, op)
                except UnitInferenceError:
                    continue
                unused.pop(index)
                break
            else:
                raise UnitInferenceError(f"{op} has incompatible union branches")
        return
    left_kinds, right_kinds = _generic_kinds(left), _generic_kinds(right)
    if left_kinds <= {"number"} or right_kinds <= {"number"}:
        if not left_kinds <= {"number"} or not right_kinds <= {"number"}:
            raise UnitInferenceError(f"{op} has incompatible structured member types")
        _known_compatible([_numeric(left), _numeric(right)], op)
        return
    if len(left_kinds) != 1 or len(right_kinds) != 1:
        raise UnitInferenceError(f"{op} has mixed structured member types")
    left_kind, right_kind = next(iter(left_kinds)), next(iter(right_kinds))
    if left_kind != right_kind:
        raise UnitInferenceError(f"{op} has incompatible structured member types")
    if left_kind == "record":
        left_members = left.get("members", left.get("properties", {}))
        right_members = right.get("members", right.get("properties", {}))
        if set(left_members) != set(right_members):
            raise UnitInferenceError(f"{op} has incompatible record members")
        for name in left_members:
            _deep_compatible(left_members[name], right_members[name], op)
        return
    if left_kind in ("array", "vector", "matrix"):
        if left_kind != right_kind:
            raise UnitInferenceError(f"{op} has incompatible collection types")
        left_shape, right_shape = left.get("shape"), right.get("shape")
        if (
            left_shape is not None
            and right_shape is not None
            and left_shape != right_shape
        ):
            raise UnitInferenceError(f"{op} has incompatible collection shapes")
        _deep_compatible(_array(left, op), _array(right, op), op)
        return
    if left_kind == "reference":
        left_targets = left.get(
            "x-referenceTargets", left.get("targetClass", left.get("targetClasses"))
        )
        right_targets = right.get(
            "x-referenceTargets", right.get("targetClass", right.get("targetClasses"))
        )
        if (
            left_targets is not None
            and right_targets is not None
            and left_targets != right_targets
        ):
            raise UnitInferenceError(f"{op} has incompatible reference targets")


def _array(schema, op):
    if _kind(schema) not in ("array", "vector", "matrix"):
        raise UnitInferenceError(f"{op} requires an array/vector input")
    items = schema.get("items")
    if not isinstance(items, dict):
        raise UnitInferenceError(f"{op} requires a declared item schema")
    inherited = _schema_unit(schema)
    if inherited is not None and _schema_unit(items) is None:
        items = {**items, "unit": inherited}
    return items


def _items_compatible(left, right, op):
    left_numeric = _generic_kinds(left) <= {"number"}
    right_numeric = _generic_kinds(right) <= {"number"}
    if left_numeric or right_numeric:
        if not left_numeric or not right_numeric:
            raise UnitInferenceError(f"{op} has incompatible item types")
        _known_compatible([_numeric(left), _numeric(right)], op)
    else:
        # Literal arrays become unions of exact const schemas.  Membership only
        # needs a compatible scalar value type; it does not require the enum
        # and the literal set to have identical alternative counts.
        left_kinds, right_kinds = _generic_kinds(left), _generic_kinds(right)
        if (
            len(left_kinds) == len(right_kinds) == 1
            and left_kinds == right_kinds
            and next(iter(left_kinds)) not in ("record", "array", "vector", "matrix")
        ):
            return
        _deep_compatible(left, right, op)


def _vector(schema, op):
    items = _array(schema, op)
    number = _numeric(items)
    if number.unit is None:
        raise UnitInferenceError(
            f"{op} cannot infer a vector whose numeric unit is missing"
        )
    shape = schema.get("shape")
    return number, tuple(shape) if isinstance(shape, list) else None


def _tensor_number(schema, op, inherited=None):
    """Resolve the scalar leaves of a vector/matrix/nested wildcard result."""
    if _kind(schema) in ("array", "vector", "matrix"):
        own = _schema_unit(schema, inherited)
        return _tensor_number(_array(schema, op), op, own)
    candidate = dict(schema)
    if _schema_unit(candidate) is None and inherited is not None:
        candidate["unit"] = inherited
    return _numeric(candidate)


def _temporal(schema):
    branches = _branches(schema)
    if branches:
        concrete = [branch for branch in branches if _kind(branch) != "null"]
        return (
            bool(concrete)
            and all(_temporal(branch) for branch in concrete)
            and all(
                _kind(branch) in ("null", "string", "date", "date_time", "timestamp")
                for branch in branches
            )
        )
    return _kind(schema) in ("string", "date", "date_time", "timestamp")


def _identity_schema(schema):
    if _kind(schema) == "reference" or schema.get("identityComponents"):
        return True
    branches = _branches(schema)
    return bool(branches) and all(_identity_schema(branch) for branch in branches)


def infer_operator(op, arg_schemas, arg_nodes):
    """Infer and validate one expanded AST operator.

    The returned schema always has an explicit ``unit`` key.  Numeric literals
    are reconstructed from ``arg_nodes`` so the exact zero exception cannot be
    forged by a broad source schema.
    """
    if len(arg_schemas) != len(arg_nodes):
        raise UnitInferenceError(
            "operator schemas and AST arguments have different lengths"
        )
    schemas = [
        _with_literal(schema, node) for schema, node in zip(arg_schemas, arg_nodes)
    ]

    if op in ("and", "or"):
        _require_arity(op, schemas, minimum=1)
        if any(_generic_kinds(schema) != {"boolean"} for schema in schemas):
            raise UnitInferenceError(f"{op} requires Boolean arguments")
        return _result("boolean")
    if op == "not":
        _require_arity(op, schemas, exact=1)
        if _generic_kinds(schemas[0]) != {"boolean"}:
            raise UnitInferenceError("not requires one Boolean argument")
        return _result("boolean")
    if op == "implies":
        _require_arity(op, schemas, exact=2)
        if any(_generic_kinds(schema) != {"boolean"} for schema in schemas):
            raise UnitInferenceError("implies requires two Boolean arguments")
        return _result("boolean")

    if op in ("eq", "ne"):
        _require_arity(op, schemas, exact=2)
        numeric = [_generic_kinds(schema) <= {"number"} for schema in schemas]
        if any(numeric):
            if not all(numeric):
                raise UnitInferenceError(f"{op} mixes numeric and nonnumeric values")
            _known_compatible([_numeric(schema) for schema in schemas], op)
        else:
            _comparable_non_numeric(schemas, op)
        return _result("boolean")

    if op in ("gt", "gte", "lt", "lte"):
        _require_arity(op, schemas, exact=2)
        _known_compatible([_numeric(schema) for schema in schemas], op)
        return _result("boolean")

    if op in ("add", "sub", "min", "max"):
        _require_arity(
            op,
            schemas,
            exact=2 if op == "sub" else None,
            minimum=1 if op != "sub" else None,
        )
        numbers = [_numeric(schema) for schema in schemas]
        unit = _known_compatible(numbers, op)
        kind = (
            "number"
            if any(number.kind == "number" for number in numbers)
            else "integer"
        )
        return _result(kind, unit)

    if op == "abs":
        _require_arity(op, schemas, exact=1)
        number = _numeric(schemas[0])
        return _result(number.kind, number.unit)

    if op in ("mul", "div"):
        _require_arity(
            op,
            schemas,
            exact=2 if op == "div" else None,
            minimum=1 if op != "div" else None,
        )
        numbers = [_numeric(schema) for schema in schemas]
        unit = numbers[0].unit
        for number in numbers[1:]:
            if unit is None and number.unit == _ONE:
                continue
            if number.unit is None and unit == _ONE and op == "mul":
                unit = None
                continue
            if unit is None and number.unit is None:
                continue
            if unit is None or number.unit is None:
                raise UnitInferenceError(
                    f"{op} mixes a declared unit with a missing unit"
                )
            unit = _mul(unit, number.unit) if op == "mul" else _div(unit, number.unit)
        kind = (
            "number"
            if op == "div" or any(number.kind == "number" for number in numbers)
            else "integer"
        )
        return _result(kind, unit)

    if op == "pow":
        _require_arity(op, schemas, exact=2)
        base, exponent = _numeric(schemas[0]), _numeric(schemas[1])
        if exponent.unit != _ONE:
            raise UnitInferenceError(
                "pow exponent must have an explicit dimensionless unit"
            )
        node = arg_nodes[1]
        value = (
            node.get("literal")
            if isinstance(node, dict) and set(node) == {"literal"}
            else None
        )
        if (
            type(value) not in (int, float)
            or type(value) is bool
            or not math.isfinite(value)
        ):
            if base.unit != _ONE:
                raise UnitInferenceError(
                    "a dimensional pow base requires a finite literal exponent"
                )
            return _result("number", _ONE)
        if base.unit is None:
            return _result(
                "number" if base.kind == "number" or value != 1 else base.kind, None
            )
        try:
            power = Fraction(str(value))
        except ValueError as error:
            raise UnitInferenceError(
                "pow exponent is not a rational literal"
            ) from error
        return _result(
            "number"
            if base.kind == "number" or power.denominator != 1 or power < 0
            else "integer",
            _pow(base.unit, power),
        )

    if op == "sqrt":
        _require_arity(op, schemas, exact=1)
        number = _numeric(schemas[0])
        return _result(
            "number", None if number.unit is None else _pow(number.unit, Fraction(1, 2))
        )

    if op == "vector_norm":
        _require_arity(op, schemas, exact=1)
        number = _tensor_number(schemas[0], op)
        if number.unit is None:
            raise UnitInferenceError(
                f"{op} cannot infer a tensor whose numeric unit is missing"
            )
        return _result("number", number.unit)

    if op in ("dot", "cross"):
        _require_arity(op, schemas, exact=2)
        left, left_shape = _vector(schemas[0], op)
        right, right_shape = _vector(schemas[1], op)
        if left_shape and right_shape and left_shape != right_shape:
            raise UnitInferenceError(f"{op} requires equal vector shapes")
        if op == "cross" and (
            (left_shape and left_shape != (3,)) or (right_shape and right_shape != (3,))
        ):
            raise UnitInferenceError("cross requires three-component vectors")
        unit = _mul(left.unit, right.unit)
        if op == "dot":
            return _result("number", unit)
        return _result("vector", unit, items=_result("number", unit), shape=[3])

    if op == "contains":
        _require_arity(op, schemas, exact=2)
        if _kind(schemas[0]) == "string":
            if _generic_kinds(schemas[1]) != {"string"}:
                raise UnitInferenceError("string contains requires a string member")
        else:
            _items_compatible(_array(schemas[0], op), schemas[1], op)
        return _result("boolean")

    if op in ("subset", "disjoint"):
        _require_arity(op, schemas, exact=2)
        _items_compatible(_array(schemas[0], op), _array(schemas[1], op), op)
        return _result("boolean")

    if op == "count":
        _require_arity(op, schemas, exact=1)
        if _kind(schemas[0]) not in ("array", "vector", "matrix", "string"):
            raise UnitInferenceError(
                "count requires an array, vector, matrix or string"
            )
        return _result("integer", _ONE)

    if op == "date_time":
        _require_arity(op, schemas, exact=1)
        if not _temporal(schemas[0]):
            raise UnitInferenceError("date_time requires an ISO date/date-time string")
        return _result("number", _NAMED["s"])

    if op in ("interval_overlap", "interval_contains"):
        _require_arity(op, schemas, exact=2)
        left = _array(schemas[0], op)
        if op == "interval_overlap" or _kind(schemas[1]) in ("array", "vector"):
            right = _array(schemas[1], op)
        else:
            right = schemas[1]
        _items_compatible(left, right, op)
        return _result("boolean")

    if op == "same_identity":
        _require_arity(op, schemas, minimum=2)
        if not all(_identity_schema(schema) for schema in schemas):
            raise UnitInferenceError(
                "same_identity requires complete reference/identity schemas"
            )
        return _result("boolean")

    raise UnitInferenceError(f"unsupported expanded operator: {op}")


def unit_at_path(schema, path, field_unit=None):
    """Resolve a member path while preserving declared scalar unit inheritance.

    Record members only inherit a parent unit when that unit describes the
    whole numeric quantity.  Array/vector items inherit their container unit.
    Unions must resolve every reachable branch to exactly compatible units.
    """

    def descend(current, remaining, inherited, through_record=False):
        own = _schema_unit(current)
        current_unit = own if own is not None else inherited
        branches = _branches(current)
        if branches:
            resolved = [
                descend(branch, remaining, current_unit, through_record)
                for branch in branches
            ]
            parsed = [parse_unit(value) for value in resolved]
            if any(value is None for value in parsed):
                return None
            if any(value != parsed[0] for value in parsed[1:]):
                raise UnitInferenceError("union path resolves to incompatible units")
            return format_unit(parsed[0])
        if not remaining:
            kind = _kind(current)
            return current_unit if kind in ("number", "integer") else None
        head, *tail = remaining
        kind = _kind(current)
        if head == "*":
            if kind not in ("array", "vector", "matrix") or not isinstance(
                current.get("items"), dict
            ):
                raise UnitInferenceError(
                    "wildcard path requires declared array/vector items"
                )
            return descend(current["items"], tail, current_unit, False)
        members = current.get("members", current.get("properties", {}))
        if kind != "record" or head not in members:
            raise UnitInferenceError(f"path member is absent: {head}")
        # A top-level unit on a structured record is descriptive unless the
        # selected scalar member explicitly declares its own unit.
        return descend(members[head], tail, None, True)

    return descend(schema, list(path), field_unit)


__all__ = [
    "UnitInferenceError",
    "format_unit",
    "infer_operator",
    "parse_unit",
    "unit_at_path",
    "units_compatible",
]
