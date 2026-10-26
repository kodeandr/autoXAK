"""Read string settings from old source without importing or executing its code."""
import ast


def read_string_setting(source, name, environment, known=None):
    scope = dict(known or {})

    def evaluate(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name) and node.id in scope:
            return scope[node.id]
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            values = [evaluate(value) for value in node.values]
            return next((value for value in values if value), values[-1])
        if isinstance(node, ast.Call) and not node.keywords:
            if (isinstance(node.func, ast.Attribute) and node.func.attr == "getenv"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "os"
                    and 1 <= len(node.args) <= 2):
                key = evaluate(node.args[0])
                if key in environment:
                    return environment[key]
                return evaluate(node.args[1]) if len(node.args) == 2 else ""
            if isinstance(node.func, ast.Attribute) and node.func.attr == "strip" and not node.args:
                return evaluate(node.func.value).strip()
        raise ValueError(f"Unsupported legacy setting expression for {name}")

    tree = ast.parse(source.lstrip("\ufeff"))
    for statement in tree.body:
        if (isinstance(statement, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == name for target in statement.targets)):
            return evaluate(statement.value)
    raise ValueError(f"Legacy setting {name} was not found")
