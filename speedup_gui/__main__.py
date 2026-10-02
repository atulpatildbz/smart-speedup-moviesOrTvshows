import os
import sys

# Lets `python speedup_gui` / `python -m speedup_gui` from a checkout find
# splitspeedconcatV2 next to this package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from speedup_gui import main  # noqa: E402

main()
