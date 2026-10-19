#!/usr/bin/env python3
"""Source-checkout launcher for pycrefine.

The implementation lives in the ``pycrefine`` package under ``src/``. This file keeps
``python pycrefine.py file.pyc`` working from a checkout without installing anything.
It is not part of the installed distribution.

* Run as a script: puts ``src/`` on ``sys.path`` and calls ``pycrefine.main``.
* Imported as ``pycrefine`` from the repository root (for example by tests or tools that
  put the root on ``sys.path``): loads the real package from ``src/`` and returns it in
  place of this file, so ``import pycrefine`` never yields this launcher.
"""
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")

if __name__ == "__main__":
    sys.path.insert(0, _SRC)
    from pycrefine import main

    main()
else:
    import importlib.util

    _pkg_dir = os.path.join(_SRC, "pycrefine")
    _spec = importlib.util.spec_from_file_location(
        "pycrefine",
        os.path.join(_pkg_dir, "__init__.py"),
        submodule_search_locations=[_pkg_dir],
    )
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["pycrefine"] = _module
    _spec.loader.exec_module(_module)
