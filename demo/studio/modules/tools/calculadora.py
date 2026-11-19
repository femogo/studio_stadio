"""Herramienta: calculadora aritmética segura."""
import ast
import operator

from langchain_core.tools import tool

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv, ast.USub: operator.neg,
        ast.UAdd: operator.pos}


def _ev(n):
    if isinstance(n, ast.Expression):
        return _ev(n.body)
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
        return n.value
    if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
        return _OPS[type(n.op)](_ev(n.left), _ev(n.right))
    if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
        return _OPS[type(n.op)](_ev(n.operand))
    raise ValueError("Solo se admiten números y + - * / ** % //")


@tool
def calculadora(expresion: str) -> str:
    """Calcula una expresión aritmética, p. ej. '37*91' o '(2+3)**2'. Úsala siempre para hacer cuentas."""
    expresion = expresion.replace("×", "*").replace("x", "*").replace(",", ".")
    r = _ev(ast.parse(expresion, mode="eval"))
    return str(int(r) if isinstance(r, float) and r.is_integer() else r)
