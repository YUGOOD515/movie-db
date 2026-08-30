import ast
from pathlib import Path


root = Path(__file__).resolve().parents[2]
for filename in ("app.py", "helpers.py", "models.py"):
    source = (root / filename).read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = [
        (node.name, node.end_lineno - node.lineno + 1)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    ]
    print(filename)
    for name, length in sorted(functions, key=lambda item: item[1], reverse=True):
        if length >= 12:
            print(f"  {name}: {length}")
