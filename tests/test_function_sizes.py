"""No function in the decompiler package is over 150 lines (#119, #120).

The handlers, dispatchers and structural prescans were split into named steps; this keeps them split.
"""
import ast
import unittest
from pathlib import Path

LIMIT = 150
PACKAGE = Path(__file__).resolve().parent.parent / "src" / "pycrefine" / "decompiler"


class TestFunctionSizes(unittest.TestCase):
    def test_no_function_is_over_the_limit(self):
        long = []
        for path in sorted(PACKAGE.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.FunctionDef) and node.end_lineno - node.lineno + 1 > LIMIT:
                    long.append((path.name, node.name, node.end_lineno - node.lineno + 1))
        self.assertEqual(long, [], f"split these into helpers (limit {LIMIT} lines)")


if __name__ == "__main__":
    unittest.main()
