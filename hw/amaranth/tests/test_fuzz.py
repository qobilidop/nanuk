"""Differential fuzzing: random programs + random packets through the Sail
golden model and the RTL; the full output contract must agree.

Total semantics make this trivial: every 32-bit word sequence is a valid
program (worst case: a defined error halt) and the step budget bounds every
execution, so there is no validity precondition to generate around.

Gated like the cosim rig (needs nanuk-emu, i.e. the devcontainer).
"""

import os
import random
import struct
from pathlib import Path

import pytest

from nanuk_amaranth.map_sim_util import run_map_one
from nanuk_amaranth.pp_sim_util import run_pp_one
from nanuk.isa import map_encoding as map_enc
from nanuk.isa import pp_encoding as enc
from nanuk.isa.map_asm import assemble as map_assemble
from nanuk.testkit.map_harness import Table, run_map

pytestmark = pytest.mark.skipif(
    os.environ.get("NANUK_COSIM") != "1", reason="needs nanuk-emu (devcontainer)"
)

REGS = ["r0", "r1", "r2", "r3", "rz"]


def golden(prog: bytes, packet: bytes):
    from nanuk.testkit.pp_harness import run_program

    return run_program(prog, packet)


def assert_same(prog: bytes, packet: bytes, seed_info: str):
    g = golden(prog, packet)
    r = run_pp_one(prog, packet)
    for field in ("verdict", "error", "payload_offset", "steps",
                  "hdr_present", "hdr_offset", "md"):
        assert getattr(g, field) == getattr(r, field), (
            f"{field} diverged ({seed_info}): "
            f"golden={getattr(g, field)} rtl={getattr(r, field)}"
        )


def _pp_choices(rng: random.Random) -> dict:
    """One entry per pp_encoding encoder, keyed by the encoder's name —
    test_fuzz_generators_cover_every_encoder diffs these keys against the
    module, so a new instruction cannot land unfuzzed."""
    reg = lambda: rng.choice(REGS)
    return {
        "encode_ext": lambda: enc.encode_ext(reg(), rng.randrange(2048), rng.randrange(1, 65)),
        "encode_advi": lambda: enc.encode_advi(rng.randrange(0x10000)),
        "encode_advr": lambda: enc.encode_advr(reg()),
        "encode_movi": lambda: enc.encode_movi(reg(), rng.randrange(0x10000)),
        "encode_shl": lambda: enc.encode_shl(reg(), reg(), rng.randrange(64)),
        "encode_beq": lambda: enc.encode_beq(reg(), reg(), rng.randrange(1024)),
        "encode_bne": lambda: enc.encode_bne(reg(), reg(), rng.randrange(1024)),
        "encode_jmp": lambda: enc.encode_jmp(rng.randrange(1024)),
        "encode_sethdr": lambda: enc.encode_sethdr(rng.randrange(16)),
        "encode_stmd": lambda: _stmd_any(rng),
        "encode_halt": lambda: enc.encode_halt(drop=rng.random() < 0.5),
        "encode_ldmd": lambda: enc.encode_ldmd(reg(), rng.randrange(16)),
    }


def random_instruction(rng: random.Random) -> int:
    """A structurally-valid instruction with random fields."""
    choices = _pp_choices(rng)
    return choices[rng.choice(sorted(choices))]()


def _stmd_any(rng: random.Random) -> int:
    # Any slot/nunits combination the encoder accepts (slot + n <= 8).
    while True:
        slot, n = rng.randrange(8), rng.randrange(1, 5)
        if slot + n <= 8:
            return enc.encode_stmd(slot, rng.choice(REGS), n)


def random_packet(rng: random.Random) -> bytes:
    return rng.randbytes(rng.randrange(0, 300))


@pytest.mark.parametrize("seed", range(20))
def test_fuzz_valid_instructions(seed):
    """Programs of well-formed instructions with random fields; branches and
    advances go wherever they go — the watchdog and violation semantics
    bound everything."""
    rng = random.Random(1000 + seed)
    words = [random_instruction(rng) for _ in range(rng.randrange(1, 40))]
    prog = b"".join(struct.pack(">I", w) for w in words)
    for i in range(3):
        assert_same(prog, random_packet(rng), f"valid seed={seed} pkt={i}")


@pytest.mark.parametrize("seed", range(10))
def test_fuzz_raw_words(seed):
    """Arbitrary bit patterns as the program: exercises decode totality
    (illegal encodings, reserved bits, bad register codes)."""
    rng = random.Random(2000 + seed)
    prog = rng.randbytes(4 * rng.randrange(1, 30))
    for i in range(3):
        assert_same(prog, random_packet(rng), f"raw seed={seed} pkt={i}")


# --- MAP leg: random packets/tables through the M1 demo programs, plus raw
# random MAP programs — nanuk-map-emu vs MatchActionProcessor, full contract. ---

_EXAMPLES = Path(__file__).resolve().parents[3] / "examples"


class _StubPP:
    """All-absent PP context for raw-program fuzzing."""

    hdr_present = [0] * 16
    hdr_offset = [0] * 16

    # run_map consumes attribute access only; this mirrors ParserResult's shape.
    verdict = 0
    error = 0
    payload_offset = 0
    steps = 0


def _assert_map_same(prog, packet, pp, tables, md_in, seed_info):
    g = run_map(prog, packet, pp, tables, md_in)
    r = run_map_one(prog, packet, pp, tables, md_in)
    for field in ("verdict", "error", "md", "delta", "steps", "frame"):
        g_v, r_v = getattr(g, field), getattr(r, field)
        if field == "md":
            g_v, r_v = tuple(g_v), tuple(r_v)
        assert g_v == r_v, (
            f"MAP {field} diverged ({seed_info}): golden={g_v} rtl={r_v}"
        )


def _random_table(rng, packet: bytes) -> Table:
    entries = {}
    for _ in range(rng.randrange(0, 8)):
        if len(packet) >= 6 and rng.random() < 0.5:
            key = int.from_bytes(packet[:6], "big")  # force hits sometimes
        else:
            key = rng.getrandbits(48)
        entries[key] = rng.getrandbits(8)
    return Table(key_width=48, action_width=8, entries=entries)


@pytest.mark.parametrize("seed", range(15))
def test_fuzz_map_l2fwd(seed):
    from nanuk.testkit.pp_harness import run_program
    from nanuk.isa.pp_asm import assemble as pp_assemble

    from nanuk.testkit.testkit import NO_TABLE, demo_flood_table

    rng = random.Random(3000 + seed)
    pp_prog = pp_assemble((_EXAMPLES / "l2l3l4" / "parse.asm").read_text())
    map_prog = map_assemble((_EXAMPLES / "map_l2fwd" / "fwd.asm").read_text())
    for i in range(4):
        packet = rng.randbytes(rng.randrange(14, 300))
        ingress = rng.randrange(4)
        pp = run_program(pp_prog, packet, [ingress])
        if pp.verdict != 0:
            continue
        tables = [_random_table(rng, packet), NO_TABLE, NO_TABLE, demo_flood_table()]
        _assert_map_same(
            map_prog, packet, pp, tables, pp.md,
            f"l2fwd seed={seed} pkt={i}",
        )


@pytest.mark.parametrize("seed", range(10))
def test_fuzz_map_raw_words(seed):
    """Arbitrary bit patterns as MAP programs: exercises decode totality
    (illegal encodings, reserved bits, bad register codes, out-of-window
    md slots)."""
    rng = random.Random(4000 + seed)
    prog = rng.randbytes(4 * rng.randrange(1, 30))
    for i in range(3):
        packet = rng.randbytes(rng.randrange(0, 300))
        _assert_map_same(
            prog, packet, _StubPP(), [], [rng.randrange(4)],
            f"map-raw seed={seed} pkt={i}",
        )


def _map_choices(rng: random.Random) -> dict:
    """One entry per map_encoding encoder, keyed by name — same tripwire
    contract as _pp_choices. The v0.1 reg-reg ALU shipped with no structured
    fuzzing (only hand cosim cases) because the old generator was a bare
    opcode list nothing diffed against the encoder module."""
    reg = lambda: rng.choice(REGS)
    hdr = lambda: rng.randrange(16)
    off = lambda: rng.randrange(-512, 512)
    return {
        "encode_ld": lambda: map_enc.encode_ld(reg(), hdr(), off(), rng.randrange(1, 9)),
        "encode_st": lambda: map_enc.encode_st(reg(), hdr(), off(), rng.randrange(1, 9)),
        "encode_ldmd": lambda: map_enc.encode_ldmd(reg(), rng.randrange(16)),
        "encode_movi": lambda: map_enc.encode_movi(reg(), rng.randrange(0x10000)),
        "encode_addi": lambda: map_enc.encode_addi(reg(), reg(), rng.randrange(-0x8000, 0x10000)),
        "encode_beq": lambda: map_enc.encode_beq(reg(), reg(), rng.randrange(1024)),
        "encode_bne": lambda: map_enc.encode_bne(reg(), reg(), rng.randrange(1024)),
        "encode_jmp": lambda: map_enc.encode_jmp(rng.randrange(1024)),
        "encode_lookup": lambda: map_enc.encode_lookup(
            reg(), rng.randrange(16), reg(), rng.randrange(1024)
        ),
        "encode_csum": lambda: map_enc.encode_csum(reg(), hdr(), off(), reg()),
        "encode_send": lambda: map_enc.encode_send(rng.randrange(-512, 512)),
        "encode_drop": lambda: map_enc.encode_drop(),
        "encode_stmd": lambda: _map_stmd_any(rng),
        "encode_andi": lambda: map_enc.encode_andi(reg(), reg(), rng.randrange(0x10000)),
        "encode_shli": lambda: map_enc.encode_shli(reg(), reg(), rng.randrange(64)),
        "encode_alu": lambda: map_enc.encode_alu(
            rng.choice(sorted(map_enc.ALU_OPS)), reg(), reg(), reg()
        ),
    }


def _map_stmd_any(rng: random.Random) -> int:
    # Stay inside the md window (slot + n <= 8); out-of-window encodings are
    # the raw-words leg's job.
    while True:
        slot, n = rng.randrange(8), rng.randrange(1, 5)
        if slot + n <= 8:
            return map_enc.encode_stmd(rng.choice(REGS), n, slot)


def random_map_instruction(rng: random.Random) -> int:
    """A structurally-valid MAP instruction with random fields."""
    choices = _map_choices(rng)
    return choices[rng.choice(sorted(choices))]()


@pytest.mark.parametrize("seed", range(15))
def test_fuzz_map_valid_instructions(seed):
    """Programs of well-formed MAP instructions over real PP contexts:
    every opcode (incl. the v0.1 reg-reg ALU) reaches the emu-vs-RTL
    differential, not just the demo programs' working set."""
    from nanuk.testkit.pp_harness import run_program
    from nanuk.isa.pp_asm import assemble as pp_assemble
    from nanuk.testkit.testkit import NO_TABLE, demo_flood_table

    rng = random.Random(5000 + seed)
    pp_prog = pp_assemble((_EXAMPLES / "l2l3l4" / "parse.asm").read_text())
    words = [random_map_instruction(rng) for _ in range(rng.randrange(1, 40))]
    prog = b"".join(struct.pack(">I", w) for w in words)
    for i in range(3):
        packet = rng.randbytes(rng.randrange(14, 300))
        ingress = rng.randrange(4)
        pp = run_program(pp_prog, packet, [ingress])
        if pp.verdict != 0:
            continue
        tables = [_random_table(rng, packet), NO_TABLE, NO_TABLE, demo_flood_table()]
        _assert_map_same(
            prog, packet, pp, tables, pp.md,
            f"map-valid seed={seed} pkt={i}",
        )


def test_fuzz_generators_cover_every_encoder():
    """Tripwire: an encoder added to pp_encoding/map_encoding without a fuzz
    generator entry fails here. The v0.1 MAP ALU and PP LDMD both slipped
    through before this existed."""
    rng = random.Random(0)
    pp_encoders = {n for n in dir(enc) if n.startswith("encode_")}
    assert set(_pp_choices(rng)) == pp_encoders
    map_encoders = {n for n in dir(map_enc) if n.startswith("encode_")}
    assert set(_map_choices(rng)) == map_encoders
