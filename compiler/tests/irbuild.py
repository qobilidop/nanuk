"""Shared MapProgram IR builders for the compiler test suite."""

from nanuk_ir import nanuk_ir_pb2 as ir


def send(vid: int, delta: int = 0) -> ir.Terminator:
    return ir.Terminator(send=ir.MapSend(bitmap_value_id=vid, delta=delta))


def drop() -> ir.Terminator:
    return ir.Terminator(drop=ir.Drop())


def load(vid: int, hdr: int = 15, off: int = 0, n: int = 1) -> ir.MapOp:
    return ir.MapOp(
        load=ir.MapLoad(value_id=vid, hdr_id=hdr, byte_offset=off, nbytes=n)
    )


def l2_table() -> ir.TableDecl:
    return ir.TableDecl(table_id=0, key_width=48, action_width=8, debug_name="l2")


def l2fwd_program() -> ir.MapProgram:
    """The 5-instruction L2 forward, as IR."""
    return ir.MapProgram(
        ir_version=1,
        tables=[l2_table()],
        states=[
            ir.MapState(
                name="forward",
                ops=[
                    load(1, hdr=0, off=0, n=6),
                    ir.MapOp(
                        lookup=ir.Lookup(
                            value_id=2, table_id=0, key_value_id=1,
                            miss_state="flood",
                        )
                    ),
                ],
                terminator=send(2),
            ),
            ir.MapState(
                name="flood",
                ops=[ir.MapOp(load_md=ir.MapLoadMd(value_id=3, field=9))],
                terminator=send(3),
            ),
        ],
    )
