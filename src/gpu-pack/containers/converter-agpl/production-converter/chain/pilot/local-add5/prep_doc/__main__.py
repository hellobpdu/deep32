"""python3 -B <path>/prep_doc <doc_key> <in.html> <out.html>: see the package docstring."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from prep_doc import main  # noqa: E402

sys.exit(main(sys.argv[1:]))
