"""Protocol header layouts for the nanuk eDSL.

A Header declares an ordered sequence of named bit fields. Bit offsets are
computed from the declaration order; the total must be a whole number of
bytes (the cursor advances in bytes) and each field must fit one EXT
(at most 64 bits).
"""


class CompileError(Exception):
    """Raised for any nanuk-lang authoring or compilation error."""


class Field:
    """A single named bit field of a Header (immutable)."""

    __slots__ = ("header", "name", "bit_offset", "width")

    def __init__(self, header: "Header", name: str, bit_offset: int, width: int):
        object.__setattr__(self, "header", header)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "bit_offset", bit_offset)
        object.__setattr__(self, "width", width)

    def __setattr__(self, name, value):
        raise AttributeError("Field is immutable")

    @property
    def qualname(self) -> str:
        return f"{self.header.name}.{self.name}"

    def __repr__(self) -> str:
        return f"<field {self.qualname}: bit {self.bit_offset}, {self.width} bits>"


class Header:
    """An ordered header layout: ``Header("eth", dst=48, src=48, ethertype=16)``.

    Fields are accessed as attributes (``eth.dst``) and resolve to
    (bit_offset, width) pairs relative to wherever the header is marked.
    """

    def __init__(self, name: str, **fields: int):
        if not fields:
            raise CompileError(f"header {name!r} has no fields")
        self.name = name
        self.fields: dict[str, Field] = {}
        bit_offset = 0
        for fname, width in fields.items():
            if not isinstance(width, int) or isinstance(width, bool) or width < 1:
                raise CompileError(
                    f"field {name}.{fname}: width must be a positive integer, got {width!r}"
                )
            if width > 64:
                raise CompileError(
                    f"field {name}.{fname} is {width} bits; EXT extracts at most 64"
                )
            self.fields[fname] = Field(self, fname, bit_offset, width)
            bit_offset += width
        if bit_offset % 8 != 0:
            raise CompileError(
                f"header {name!r} is {bit_offset} bits; headers must be whole bytes"
            )
        self.bit_len = bit_offset
        self.byte_len = bit_offset // 8

    def __getattr__(self, name: str) -> Field:
        # Only called when normal attribute lookup fails.
        fields = self.__dict__.get("fields", {})
        if name in fields:
            return fields[name]
        raise AttributeError(f"header {self.__dict__.get('name')!r} has no field {name!r}")

    def __repr__(self) -> str:
        return f"<header {self.name}: {self.byte_len} bytes, {len(self.fields)} fields>"
