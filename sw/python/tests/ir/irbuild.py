"""Shared MatchActionProgram IR builders for the compiler test suite."""

from nanuk.ir import nanuk_ir_pb2 as ir


def send(delta: int = 0) -> ir.Terminator:
    return ir.Terminator(send=ir.MapSend(delta=delta))


def drop() -> ir.Terminator:
    return ir.Terminator(drop=ir.Drop())


def load(vid: int, hdr: int = 15, off: int = 0, n: int = 1) -> ir.MatchActionOp:
    return ir.MatchActionOp(
        load=ir.MapLoad(value_id=vid, hdr_id=hdr, byte_offset=off, nbytes=n)
    )


def store_md(vid: int, slot: int = 0, nunits: int = 1) -> ir.MatchActionOp:
    return ir.MatchActionOp(
        store_md=ir.MdStore(value_id=vid, slot=slot, nunits=nunits)
    )


def load_md(vid: int, slot: int = 0) -> ir.MatchActionOp:
    return ir.MatchActionOp(load_md=ir.MdLoad(value_id=vid, slot=slot))


def l2_table() -> ir.TableDecl:
    return ir.TableDecl(table_id=0, key_width=48, action_width=8, debug_name="l2")


def flood_table_decl() -> ir.TableDecl:
    """The system flood table: {ingress port id -> flood bitmap}."""
    return ir.TableDecl(table_id=3, key_width=16, action_width=16, debug_name="flood")


def l2fwd_program() -> ir.MatchActionProgram:
    """The L2 forward, as IR: table hit forwards, miss floods via the
    system flood table (md slot 0 carries ingress in, egress bitmap out)."""
    return ir.MatchActionProgram(
        ir_version=1,
        tables=[l2_table(), flood_table_decl()],
        states=[
            ir.MatchActionState(
                name="forward",
                ops=[
                    load(1, hdr=0, off=0, n=6),
                    ir.MatchActionOp(
                        lookup=ir.Lookup(
                            value_id=2, table_id=0, key_value_id=1,
                            miss_state="flood",
                        )
                    ),
                    store_md(2),
                ],
                terminator=send(),
            ),
            ir.MatchActionState(
                name="flood",
                ops=[
                    load_md(3),
                    ir.MatchActionOp(
                        lookup=ir.Lookup(
                            value_id=4, table_id=3, key_value_id=3,
                            miss_state="dark",
                        )
                    ),
                    store_md(4),
                ],
                terminator=send(),
            ),
            ir.MatchActionState(name="dark", terminator=drop()),
        ],
    )
