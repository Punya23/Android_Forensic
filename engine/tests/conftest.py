"""pytest conftest — makes ``engine/`` importable as the root for ``triage`` and ``tools``."""
import sys
from pathlib import Path

# The tests live in  engine/tests/
# The triage package lives in  engine/triage/
# The tools package lives in  engine/tools/
# Adding engine/ to sys.path satisfies all ``from triage.xxx`` and ``from tools.xxx`` imports.
_ENGINE_ROOT = Path(__file__).resolve().parent.parent   # …/Android_Forensic/engine
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))
