"""The evals test her usual self, whatever she is wearing right now. To test an outfit with its own
personality instead, name it: ALICE_EVAL_OUTFIT=evil uv run python -m evals.conduct"""

import os
import tempfile
from pathlib import Path

_worn = Path(tempfile.mkdtemp(prefix="alice-eval-")) / "outfit"
if os.environ.get("ALICE_EVAL_OUTFIT"):
    _worn.write_text(os.environ["ALICE_EVAL_OUTFIT"])
os.environ["ALICE_WORN_FILE"] = str(_worn)
