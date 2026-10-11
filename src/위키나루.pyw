import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import editor  # noqa: E402

editor.main(sys.argv[1:])
