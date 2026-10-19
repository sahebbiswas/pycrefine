"""Python version identification from the ``.pyc`` magic number."""
from typing import Optional


# Magic-number (& 0xFFFF) ranges, from the history in CPython's
# Lib/importlib/_bootstrap_external.py. Final releases: 3.9 = 3425,
# 3.10 = 3439, 3.11 = 3495, 3.12 = 3531, 3.13 = 3571, 3.14 = 3627.
# 3.9              3420 – 3429
# 3.10             3430 – 3449
# 3.11             3450 – 3499
# 3.12             3500 – 3549
# 3.13             3550 – 3599
# 3.14             3600 – 3649

def _get_python_version_from_magic(version_id: int) -> Optional[str]:
    """Return a Python version string (e.g., '3.12') corresponding to the given magic number."""
    if 3310 <= version_id <= 3349:
        return "3.4"
    if 3350 <= version_id <= 3378:
        return "3.5"
    if 3379 <= version_id <= 3393:
        return "3.6"
    if 3394 <= version_id <= 3412:
        return "3.7"
    if 3413 <= version_id <= 3413:
        return "3.8"
    if 3420 <= version_id <= 3429:
        return "3.9"
    if 3430 <= version_id <= 3449:
        return "3.10"
    if 3450 <= version_id <= 3499:
        return "3.11"
    if 3500 <= version_id <= 3549:
        return "3.12"
    if 3550 <= version_id <= 3599:
        return "3.13"
    if 3600 <= version_id <= 3649:
        return "3.14"
    if version_id >= 3650:
        return "3.15+"
    return None
