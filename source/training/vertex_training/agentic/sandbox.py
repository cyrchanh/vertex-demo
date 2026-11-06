"""Checks and loads a strategist's bot program.

A bot program is one Python module that defines

    class Fighter:
        def __init__(self, seed): ...
        def act(self, view): ...      # → action dict, called 10 times a second

It may import only math, random and collections, and may not touch dunder attributes or the
builtins that reach outside the program (open, eval, exec, getattr, ...). This is a guard against
mistakes and casual escapes, not a security boundary: the code runs on this machine, in a child
process with a time limit (match.py), so only run strategists you trust with that.
"""

import ast
import builtins
import math

ALLOWED_IMPORTS = {"math", "random", "collections"}
BANNED_NAMES = {"open", "eval", "exec", "compile", "__import__", "globals", "locals", "vars", "getattr",
                "setattr", "delattr", "input", "breakpoint", "exit", "quit", "help", "memoryview",
                "classmethod", "staticmethod", "super", "object", "type"}
SAFE_BUILTINS = ["abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter", "float", "int",
                 "isinstance", "len", "list", "map", "max", "min", "pow", "range", "reversed", "round",
                 "set", "sorted", "str", "sum", "tuple", "zip", "Exception", "ValueError", "KeyError",
                 "IndexError", "ZeroDivisionError", "print"]
MAX_CODE_BYTES = 40_000
KEYS = ("forward", "back", "left", "right", "jump", "sprint", "sneak", "attack")
MAX_TURN, MAX_LOOK = 40.0, 10.0          # degrees per tick, as for the scripted bots


class BotCodeError(ValueError):
    pass


def check(code):
    """Raises BotCodeError naming the first problem; returns None if the code is acceptable."""
    if len(code.encode("utf-8")) > MAX_CODE_BYTES:
        raise BotCodeError(f"code is over {MAX_CODE_BYTES} bytes")
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise BotCodeError(f"syntax error line {e.lineno}: {e.msg}") from None
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad = [a.name for a in node.names if a.name.split(".")[0] not in ALLOWED_IMPORTS]
            if bad:
                raise BotCodeError(f"line {node.lineno}: import of {bad[0]} (allowed: {sorted(ALLOWED_IMPORTS)})")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in ALLOWED_IMPORTS or node.level:
                raise BotCodeError(f"line {node.lineno}: import from {node.module} (allowed: {sorted(ALLOWED_IMPORTS)})")
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            raise BotCodeError(f"line {node.lineno}: {node.id} is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise BotCodeError(f"line {node.lineno}: dunder attribute .{node.attr} is not allowed")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            raise BotCodeError(f"line {node.lineno}: global/nonlocal is not allowed")
    fighters = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Fighter"]
    if not fighters:
        raise BotCodeError("no top-level class Fighter")
    methods = {n.name for n in fighters[0].body if isinstance(n, ast.FunctionDef)}
    if "act" not in methods:
        raise BotCodeError("class Fighter has no act(self, view) method")


def _import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".")[0] not in ALLOWED_IMPORTS or level:
        raise ImportError(f"import of {name} is not allowed")
    return __import__(name, globals, locals, fromlist, level)


def load(code, name="bot"):
    """Checked code → its Fighter class."""
    check(code)
    safe = {k: getattr(builtins, k) for k in SAFE_BUILTINS}
    safe["__import__"] = _import
    safe["__build_class__"] = builtins.__build_class__
    namespace = {"__builtins__": safe, "__name__": f"agentic_bot_{name}"}
    exec(compile(code, f"<{name}>", "exec"), namespace)
    return namespace["Fighter"]


def sanitize(action):
    """Whatever act() returned → a valid action dict (missing keys off, turns clamped)."""
    if not isinstance(action, dict):
        return {}
    out = {k: bool(action.get(k, False)) for k in KEYS}
    for key, cap in (("dyaw", MAX_TURN), ("dpitch", MAX_LOOK)):
        try:
            v = float(action.get(key, 0.0))
        except (TypeError, ValueError):
            v = 0.0
        out[key] = max(-cap, min(cap, v)) if math.isfinite(v) else 0.0
    return out
