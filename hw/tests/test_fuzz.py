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

import pytest

from nanuk_hw.sim_util import run_one
from nanuk_spec import encoding as enc

pytestmark = pytest.mark.skipif(
    os.environ.get("NANUK_COSIM") != "1", reason="needs nanuk-emu (devcontainer)"
)

REGS = ["r0", "r1", "r2", "r3", "rz"]


def golden(prog: bytes, packet: bytes):
    from nanuk_spec.harness import run_program

    return run_program(prog, packet)


def assert_same(prog: bytes, packet: bytes, seed_info: str):
    g = golden(prog, packet)
    r = run_one(prog, packet)
    for field in ("verdict", "error", "payload_offset", "steps",
                  "hdr_present", "hdr_offset", "smd"):
        assert getattr(g, field) == getattr(r, field), (
            f"{field} diverged ({seed_info}): "
            f"golden={getattr(g, field)} rtl={getattr(r, field)}"
        )


def random_instruction(rng: random.Random) -> int:
    """A structurally-valid instruction with random fields."""
    choice = rng.randrange(11)
    reg = lambda: rng.choice(REGS)
    match choice:
        case 0:
            return enc.encode_ext(reg(), rng.randrange(2048), rng.randrange(1, 65))
        case 1:
            return enc.encode_advi(rng.randrange(0x10000))
        case 2:
            return enc.encode_advr(reg())
        case 3:
            return enc.encode_movi(reg(), rng.randrange(0x10000))
        case 4:
            return enc.encode_shl(reg(), reg(), rng.randrange(64))
        case 5:
            return enc.encode_beq(reg(), reg(), rng.randrange(1024))
        case 6:
            return enc.encode_bne(reg(), reg(), rng.randrange(1024))
        case 7:
            return enc.encode_jmp(rng.randrange(1024))
        case 8:
            return enc.encode_sethdr(rng.randrange(16))
        case 9:
            return _stmd_any(rng)
        case 10:
            return enc.encode_halt(drop=rng.random() < 0.5)


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
