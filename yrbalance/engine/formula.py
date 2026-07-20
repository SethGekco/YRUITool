"""
Sandboxed arithmetic formula evaluator for modifier scripts.

Formulas like `TechLevel * BuildLimit * 100` are parsed to an AST and walked with
a strict whitelist — only numbers, named variables, a fixed set of operators, and
a fixed set of functions are allowed. There is NO call to Python `eval`/`exec`, no
attribute access, no indexing, no comprehensions. Anything outside the whitelist
raises FormulaError.

Auto-detection: `looks_like_formula(rhs, known_names)` decides whether a tag's
right-hand side should be computed (a valid arithmetic expression whose every name
is a known variable/param) or written verbatim as a literal string.
"""

import ast
import math


class FormulaError(Exception):
    """Raised when an expression is invalid or references an unknown name."""


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


# Whitelisted functions available inside formulas.
_FUNCS = {
    "min": min, "max": max, "round": round, "abs": abs,
    "floor": math.floor, "ceil": math.ceil, "clamp": _clamp,
    "int": int, "float": float,
}

# Whitelisted AST node types.
_ALLOWED_NODES = (
    ast.Expression, ast.Constant, ast.Name, ast.Load,
    ast.BinOp, ast.UnaryOp, ast.Call,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd,
)


def _parse(expr):
    try:
        return ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"could not parse expression: {expr!r} ({e})")


def _eval_node(node, variables):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, variables)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise FormulaError(f"only numeric literals allowed, got {node.value!r}")

    if isinstance(node, ast.Name):
        if node.id in variables:
            return variables[node.id]
        raise FormulaError(f"unknown variable: {node.id}")

    if isinstance(node, ast.BinOp):
        l = _eval_node(node.left, variables)
        r = _eval_node(node.right, variables)
        op = node.op
        if isinstance(op, ast.Add):      return l + r
        if isinstance(op, ast.Sub):      return l - r
        if isinstance(op, ast.Mult):     return l * r
        if isinstance(op, ast.Div):
            if r == 0:
                raise FormulaError("division by zero")
            return l / r
        if isinstance(op, ast.FloorDiv):
            if r == 0:
                raise FormulaError("division by zero")
            return l // r
        if isinstance(op, ast.Mod):      return l % r
        if isinstance(op, ast.Pow):      return l ** r
        raise FormulaError(f"operator not allowed: {type(op).__name__}")

    if isinstance(node, ast.UnaryOp):
        v = _eval_node(node.operand, variables)
        if isinstance(node.op, ast.USub):
            return -v
        if isinstance(node.op, ast.UAdd):
            return +v
        raise FormulaError(f"unary operator not allowed: {type(node.op).__name__}")

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            fn = getattr(node.func, "id", "?")
            raise FormulaError(f"unknown or disallowed function: {fn}")
        if node.keywords:
            raise FormulaError("keyword arguments are not allowed in formulas")
        args = [_eval_node(a, variables) for a in node.args]
        try:
            return _FUNCS[node.func.id](*args)
        except TypeError as e:
            raise FormulaError(f"bad arguments to {node.func.id}(): {e}")

    raise FormulaError(f"expression element not allowed: {type(node).__name__}")


def _validate(node):
    """Raise FormulaError if any node in the tree is outside the whitelist."""
    for child in ast.walk(node):
        if not isinstance(child, _ALLOWED_NODES):
            raise FormulaError(
                f"expression element not allowed: {type(child).__name__}")
    return node


def evaluate(expr, variables):
    """
    Evaluate `expr` against a {name: number} dict. Returns an int if the result is
    a whole number, else a float. Raises FormulaError on anything invalid.
    """
    tree = _validate(_parse(expr))
    result = _eval_node(tree, variables)
    if isinstance(result, bool):  # guard: bools sneak through as ints
        raise FormulaError("boolean result is not a valid tag value")
    if isinstance(result, float) and result == int(result):
        return int(result)
    return result


def referenced_names(expr):
    """Return the set of variable names an expression references (best effort)."""
    try:
        tree = _parse(expr)
    except FormulaError:
        return set()
    return {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and n.id not in _FUNCS}


def looks_like_formula(rhs, known_names):
    """
    Decide whether `rhs` should be computed as a formula (True) or written as a
    literal string (False).

    True only when rhs parses as a whitelisted arithmetic expression AND every
    name it uses is a known variable/param AND it is more than a bare string/number
    (i.e. it has an operator/function call, or is a single known variable). This
    keeps 'Owner = Russians,Africans' and 'Elite = yes' literal while treating
    'Cost = TechLevel * 100' and 'Cost = TechLevel' as formulas.
    """
    stripped = rhs.strip()
    # Explicitly quoted -> forced literal.
    if len(stripped) >= 2 and stripped[0] in "\"'" and stripped[-1] == stripped[0]:
        return False
    try:
        tree = _parse(stripped)
    except FormulaError:
        return False
    # Must be entirely whitelisted nodes (rejects tuples/strings/commas/etc.).
    try:
        _validate(tree)
    except FormulaError:
        return False

    names = {n.id for n in ast.walk(tree)
             if isinstance(n, ast.Name)}
    # Every name must be a known variable/param or a whitelisted function name.
    for name in names:
        if name not in known_names and name not in _FUNCS:
            return False

    has_op_or_call = any(
        isinstance(n, (ast.BinOp, ast.UnaryOp, ast.Call)) for n in ast.walk(tree))
    single_known_var = (
        isinstance(tree.body, ast.Name) and tree.body.id in known_names)
    return has_op_or_call or single_known_var


def strip_quotes(s):
    """Remove a single matching pair of surrounding quotes from a literal."""
    s = s.strip()
    if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
        return s[1:-1]
    return s
