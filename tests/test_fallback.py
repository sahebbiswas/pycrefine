import unittest
from unittest.mock import patch
import pycrefine

class TestStringReplacementFallback(unittest.TestCase):
    def test_repr_fallback_logic(self):
        """Verify that the fallback mechanism in post_process_source's repl_str 
        correctly handles cases where repr() fails."""
        
        original_repr = repr
        def side_effect(obj):
            if isinstance(obj, str) and "FAIL" in obj:
                raise ValueError("forced repr failure")
            return original_repr(obj)
            
        with patch('builtins.repr', side_effect=side_effect):
            # 1. Simple fallback
            source = 'print("""FAIL_ME""")'
            processed = pycrefine.post_process_source(source)
            self.assertIn('print("FAIL_ME")', processed)
            
            # 2. Fallback with newlines and double quotes
            # The fallback logic is:
            # escaped = raw_content.replace('\\', '\\\\').replace('\n', '\\n').replace('"', '\\"')
            # return f'"{escaped}"'
            source = 'print("""FAIL\nWITH "QUOTES" AND \\BACKSLASH""")'
            processed = pycrefine.post_process_source(source)
            # Expected escaped: FAIL\nWITH \"QUOTES\" AND \\BACKSLASH
            # When printed in source it will look like "FAIL\nWITH \"QUOTES\" AND \\BACKSLASH"
            self.assertIn('print("FAIL\\nWITH \\"QUOTES\\" AND \\\\BACKSLASH")', processed)

if __name__ == "__main__":
    unittest.main()
