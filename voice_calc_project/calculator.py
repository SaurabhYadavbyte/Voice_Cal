"""Bounded arithmetic evaluation for keypad and voice expressions."""

import ast
import math
import operator
from decimal import Decimal, DecimalException, localcontext

MAX_VALUE = Decimal("1e12")
FUNCTIONS = {"sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "ln", "log", "fact"}
CONSTANTS = {"pi": Decimal(str(math.pi)), "e": Decimal(str(math.e))}


def evaluate(expression, angle_mode="deg"):
    if not isinstance(expression, str) or not 0 < len(expression) <= 160:
        raise ValueError("Enter an expression of up to 160 characters.")
    if not isinstance(angle_mode, str) or angle_mode not in {"deg", "rad"}:
        raise ValueError("Invalid angle mode.")
    try:
        tree = ast.parse(expression.replace("^", "**"), mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise ValueError("Invalid expression.") from exc
    if sum(1 for _ in ast.walk(tree)) > 80:
        raise ValueError("Expression is too complex.")

    def bounded(value):
        if not value.is_finite() or abs(value) > MAX_VALUE:
            raise ValueError("Number or result is too large.")
        return value

    def float_result(value):
        if not math.isfinite(value):
            raise ValueError("Undefined result.")
        return bounded(Decimal(str(round(value, 12))))

    def visit(node, depth=0):
        if depth > 24:
            raise ValueError("Expression is too complex.")
        if isinstance(node, ast.Expression):
            return visit(node.body, depth + 1)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            literal = ast.get_source_segment(expression.replace("^", "**"), node)
            return bounded(Decimal(literal))
        if isinstance(node, ast.Name) and node.id in CONSTANTS:
            return CONSTANTS[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand, depth + 1)
            return bounded(operator.neg(value) if isinstance(node.op, ast.USub) else value)
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left, depth + 1), visit(node.right, depth + 1)
            operations = {ast.Add: operator.add, ast.Sub: operator.sub,
                          ast.Mult: operator.mul, ast.Div: operator.truediv}
            if type(node.op) in operations:
                return bounded(operations[type(node.op)](left, right))
            if isinstance(node.op, ast.Pow):
                if abs(right) > 12:
                    raise ValueError("Exponent is too large.")
                return bounded(left ** right)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in FUNCTIONS and len(node.args) == 1 and not node.keywords):
            value = visit(node.args[0], depth + 1)
            name = node.func.id
            if name == "sqrt":
                return bounded(value.sqrt())
            if name == "fact":
                if value != int(value) or not 0 <= value <= 14:
                    raise ValueError("Factorial accepts whole numbers from 0 to 14.")
                return bounded(Decimal(math.factorial(int(value))))
            x = float(value)
            if name in {"sin", "cos", "tan"}:
                if angle_mode == "deg":
                    x = math.radians(x)
                return float_result(getattr(math, name)(x))
            if name in {"asin", "acos", "atan"}:
                result = getattr(math, name)(x)
                return float_result(math.degrees(result) if angle_mode == "deg" else result)
            if name in {"ln", "log"}:
                return float_result(math.log(x) if name == "ln" else math.log10(x))
        raise ValueError("Use numbers and supported calculator functions only.")

    try:
        with localcontext() as context:
            context.prec = 28
            result = visit(tree)
    except (ArithmeticError, DecimalException, OverflowError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc) not in {"math domain error"}:
            raise
        raise ValueError("Invalid calculation.") from exc
    return format(bounded(result).normalize(), "f")
