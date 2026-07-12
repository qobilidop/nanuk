import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
# examples/ are content, loaded by path — keep __pycache__ out of them.
sys.dont_write_bytecode = True
