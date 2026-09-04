"""Test package marker.

Present so `tests.support.*` resolves as a real dotted import (design.md
section 10) without widening `sys.path` to the repository root, which would
make every top-level directory importable.
"""
