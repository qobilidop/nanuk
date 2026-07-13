"""The in-network calculator, nanuk-lang edition (hand-written ISA copy: calc.asm).

Benchmark E3 — compute on packet-carried operands. This is the program that
changed the ISA: until v0.1 the MAP ALU was immediate-only, so there was
nowhere to put a second operand that came off the wire. `s.bin_op` is the new
register-register form.

The dispatch is a compare-and-branch chain (`s.dispatch`). xISA would do it
with a lookup table whose values are jump addresses, which we deliberately did
not take: no corpus program forces a register-indirect branch. See calc.asm.

Table ids follow the examples' control-plane layout: t0 is the reflect table
({ingress port -> egress bitmap}), because MAP cannot compute `1 << ingress` —
`shli` takes an immediate and there is no shift-by-register.
Header ids follow ./parse.asm: h_eth=0, h_calc=1.
"""

from nanuk.lang import Header, MatchActionProgram

# The p4-tutorial calculator protocol, byte for byte.
calc = Header(
    "calc",
    magic=16,      # 'P4'
    version=8,
    op=8,          # ASCII '+' '-' '&' '|' '^'
    operand_a=32,
    operand_b=32,
    result=32,
)
eth = Header("eth", dst=48, src=48, ethertype=16)

H_ETH, H_CALC = 0, 1

OP_ADD, OP_SUB, OP_AND, OP_OR, OP_XOR = 0x2B, 0x2D, 0x26, 0x7C, 0x5E


def make_calc() -> MatchActionProgram:
    mp = MatchActionProgram()
    reflect = mp.table("reflect", key_width=16, action_width=16)
    ethh = mp.header(eth, hdr_id=H_ETH)
    calch = mp.header(calc, hdr_id=H_CALC)

    @mp.state(start=True)
    def dispatch(s):
        s.dispatch(
            s.load(calch.op),
            {
                OP_ADD: do_add,
                OP_SUB: do_sub,
                OP_AND: do_and,
                OP_OR: do_or,
                OP_XOR: do_xor,
            },
            default=discard,   # an opcode we do not implement
        )

    def arithmetic(s, kind):
        a = s.load(calch.operand_a)
        b = s.load(calch.operand_b)
        s.store(s.bin_op(kind, a, b), calch.result)
        s.goto(reply)

    @mp.state()
    def do_add(s):
        arithmetic(s, "add")

    @mp.state()
    def do_sub(s):
        arithmetic(s, "sub")

    @mp.state()
    def do_and(s):
        arithmetic(s, "and")

    @mp.state()
    def do_or(s):
        arithmetic(s, "or")

    @mp.state()
    def do_xor(s):
        arithmetic(s, "xor")

    @mp.state()
    def reply(s):
        dst = s.load(ethh.dst)          # answer whoever asked
        src = s.load(ethh.src)
        s.store(src, ethh.dst)
        s.store(dst, ethh.src)
        egress = s.lookup(reflect, s.load_md(0), miss=discard)
        s.send(egress=egress)

    @mp.state()
    def discard(s):
        s.drop()

    return mp


def build_ir():
    return make_calc().build_ir()
