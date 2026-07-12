"""L2 forward, nanuk-lang edition (the hand-written ISA copy is fwd.asm).

Compile: cd lang && uv run python -c \
    "from fwd import make_map; print(make_map().compile())"  # (with examples/map_l2fwd on sys.path)
"""

from nanuk.lang.programs.map_demos import make_l2fwd as make_map  # noqa: F401
