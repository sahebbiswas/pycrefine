"""
Comprehensive test file for pycrefine decompiler.
Covers classes, inheritance, exceptions, and typical Python idioms.
"""

import sys
import os
from typing import List, Optional

# Basic global constant
VERSION = "1.0.0"

class BaseProcessor:
    """Base class for demonstration."""
    def __init__(self, name: str):
        self.name = name
        self.items = []

    def process(self):
        """Method to be overridden."""
        return f"Base processing for {self.name}"

class DataProcessor(BaseProcessor):
    """Derived class with more logic."""
    def __init__(self, name: str, threshold: int = 10):
        super().__init__(name)
        self.threshold = threshold

    def add_item(self, item):
        if item is not None:
            self.items.append(item)
            return True
        return False

    def process(self):
        results = []
        for item in self.items:
            try:
                if isinstance(item, int) and item > self.threshold:
                    results.append(item * 2)
                elif isinstance(item, str):
                    results.append(item.upper())
                else:
                    results.append(None)
            except Exception as e:
                print(f"Error processing item {item}: {e}")
        return results

def complex_logic(x: int, y: List[int]) -> bool:
    """A function with nested logic and loops."""
    if x < 0:
        return False
    
    found = False
    for i in range(x):
        if i in y:
            found = True
            break
            
    if found:
        print("Match found")
        # Optimized early return
        return True
    else:
        print("No match")
    
    return False

def run_test():
    """Execution harness."""
    proc = DataProcessor("TestRunner", threshold=5)
    
    data = [1, 10, "hello", None, 3]
    for d in data:
        proc.add_item(d)
        
    print(f"Processor: {proc.name}")
    print(f"Results: {proc.process()}")
    
    success = complex_logic(3, [1, 2, 3])
    print(f"Logic success: {success}")

if __name__ == "__main__":
    run_test()
