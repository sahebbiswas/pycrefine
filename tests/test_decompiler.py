import unittest
import py_compile
import os
import shutil
import tempfile
import sys
import os
import subprocess

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pycrefine import get_decompiler

class TestDecompiler(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.snippets_dir = os.path.join(os.path.dirname(__file__), "snippets")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def _run_test_on_snippet(self, snippet_name):
        snippet_path = os.path.join(self.snippets_dir, f"{snippet_name}.py")
        pyc_path = py_compile.compile(snippet_path, cfile=os.path.join(self.test_dir, f"{snippet_name}.pyc"))
        
        # Decompile
        decompiler = get_decompiler(pyc_path)
        decompiled_code = decompiler.decompile()
        
        # Verify by execution
        try:
            orig_out = subprocess.check_output([sys.executable, snippet_path], text=True, stderr=subprocess.STDOUT)
            
            # Run decompiled
            decompiled_file = os.path.join(self.test_dir, f"{snippet_name}_decompiled.py")
            with open(decompiled_file, "w") as f:
                f.write(decompiled_code)
            
            decomp_out = subprocess.check_output([sys.executable, decompiled_file], text=True, stderr=subprocess.STDOUT)
            
            if orig_out.strip() != decomp_out.strip():
                print(f"\nOUT MISMATCH for {snippet_name}:")
                print(f"ORIGINAL OUT:\n{orig_out}")
                print(f"DECOMPILED OUT:\n{decomp_out}")
                
            print(f"DEBUG: DECOMPILED CODE for {snippet_name}:\n{decompiled_code}\n---")
            self.assertEqual(orig_out.strip(), decomp_out.strip())
        except subprocess.CalledProcessError as e:
            print(f"\nError running {snippet_name}:")
            print(f"Output: {e.output}")
            raise

    def test_addition(self):
        self._run_test_on_snippet("add")

    def test_function_call(self):
        self._run_test_on_snippet("func")

    def test_control_flow(self):
        self._run_test_on_snippet("control_flow")

    def test_loops(self):
        self._run_test_on_snippet("loops")

    def test_docstrings(self):
        self._run_test_on_snippet("docstrings")

    def test_nested(self):
        self._run_test_on_snippet("nested")

if __name__ == "__main__":
    unittest.main()
