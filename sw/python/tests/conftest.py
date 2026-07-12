# examples/ are content, loaded by path (tests.support.load) — keep Python
# from strewing __pycache__ into them.
import sys

sys.dont_write_bytecode = True
