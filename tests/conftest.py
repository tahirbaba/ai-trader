"""Shared pytest fixtures and path setup."""

import sys
from pathlib import Path

# Allow `import feasibility` in tests without installing the scripts/ package.
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
