# nanuk Parser ISA v0 — Design

**Date:** 2026-07-11
**Status:** Approved design sketch. Exact encodings, assembly syntax, and SMD layout are finalized during stage 1 (Sail spec).
**Parent:** [Project design](2026-07-11-nanuk-project-design.md)

## Lineage

A **fresh, original ISA informed by xISA** (Xsight Labs' X-Switch parser ISA, [public spec rev 1.00](https://xsightlabs.com/wp-content/uploads/2025/03/XISA_Public-.pdf)) — not a subset of it and not compatible with it. Each xISA concept auditions for its place in v0 and most are deliberately deferred or re-decided:

| xISA concept | nanuk v0 decision |
|---|---|
| Cursor over 256B header buffer | **Adopted** (parameterized) |
| Offset-based header output (HDR.PRESENT / HDR.OFFSET) | **Adopted** (SETHDR) |
| Standard Metadata struct to next stage | **Adopted** (SMD + STMD) |
| Bit-granular extraction (EXT) | **Adopted** — the parser's raison d'être |
| Transition table + NXTP/JumpMode | **Deferred to v0.x** — v0 dispatches with compare-and-branch; the table arrives later as an earned accelerator |
| PSEEK, IPv4-checksum accelerators | **Deferred to v0.x** |
| 4×128-bit registers, 64-bit encodings | **Re-sized**: 4×64-bit, 32-bit encodings |
| Z/N condition flags | **Rejected**: fused compare-and-branch, no hidden state |
| Modifiers (.CD/.PR/...), reparse, MAP handoff | **Deferred** |

The sibling repo `qobilidop/sail-xisa` is the **pattern donor** (Sail idioms, build/CI/devcontainer, Rust-vs-Sail differential testing, playground architecture) but not a code ancestor — nanuk's spec is written fresh, Apache-2.0, against this design.

## Machine state

| State | Size (default) | Notes |
|---|---|---|
| `r0`–`r3` | 4 × 64 bits | GPRs; 64b holds a MAC address. `rz` reads 0, writes discarded (3-bit register field: room to grow) |
| `cursor` | byte index into buffer | advances monotonically via ADV*; never moves backward |
| header buffer | 256 bytes | read-only to v0 programs |
| `pc` | 16 bits | 32-bit fixed-width instructions, word-addressed |
| instruction memory | ~1K entries | parameterized (xISA's 64K is production sizing) |
| `hdr_present[16]`, `hdr_offset[16]` | 16 slots | output: per-header present bit + cursor snapshot |
| `SMD` | 128 bits | output: program-written fields (STMD) + hardware-written status/error fields |
| step budget | counter | hardware watchdog, parameterized max instructions per packet |

All sizes are implementation parameters; defaults above target the full configuration. A "nano" configuration for Tiny Tapeout shrinks them at stage 4.

## Instruction set — the strict core (11 instructions)

Every instruction is used by a real v0 program; nothing is included on symmetry grounds.

| Instruction | Semantics |
|---|---|
| `EXT rd, boff, bsize` | rd ← zero-extended `bsize` bits (≤64) at bit offset `boff` from cursor. Bit-granular: sub-byte fields (IPv4 version/IHL) come out pre-masked, which is what makes an ALU almost unnecessary |
| `ADVI imm` | cursor += imm bytes |
| `ADVR rs` | cursor += rs bytes |
| `MOVI rd, imm16` | rd ← zero-extended 16-bit immediate |
| `SHL rd, rs, shamt` | rd ← rs << shamt (immediate shift; exists for `IHL × 4`) |
| `BEQ rs, rt, target` | branch if rs = rt (register-register only; constants come via MOVI — compare-immediates don't fit 32-bit encodings) |
| `BNE rs, rt, target` | branch if rs ≠ rt |
| `JMP target` | unconditional |
| `SETHDR hdr_id` | hdr_present[id] ← 1; hdr_offset[id] ← cursor |
| `STMD field, rs` | store rs into an SMD field |
| `HALT accept\|drop` | terminate; verdict + payload offset (= final cursor) delivered with hdr/SMD outputs |

**Deliberately absent, with named re-entry triggers** (the v0 rule: grow only when a demo program cannot be written):

- `ADD/ADDI` — returns with IPv6 extension headers (`len × 8 + 8`)
- `AND/OR/SHR` — return when SMD field-packing outgrows multiple STMDs (bit-granular EXT covers all v0 masking)
- `BLT/BGE` — return with bounds/range validation
- LUI-style wide constants — return when some protocol needs a >16-bit compare
- `MOV` — assembler pseudo-op if ever wanted; not an opcode
- Transition table + NXTP, PSEEK, checksum accelerator, reparse — v0.x accelerators, each a book chapter

## Totality rules (no undefined behavior)

Every abnormal path is a defined, observable outcome recorded in SMD status fields:

1. **Header violation:** cursor advance or EXT reaching past the buffer end → error halt (xISA has the same concept).
2. **Step budget exhausted:** backward branches are allowed (VLAN/QinQ loops); the watchdog guarantees termination. Exhaustion → error halt.
3. **Illegal instruction:** any undefined encoding → error halt. **All-zeros is deliberately illegal, not NOP** — a runaway PC in zeroed instruction memory halts with a diagnosable error instead of NOP-sledding.
4. Error halts deliver the same output contract (verdict = error-drop, partial hdr/SMD state) — consumers never see undefined state.

## Proof by program: the v0 demo parse

Ethernet → 802.1Q (incl. QinQ via backward branch) → IPv4 (with options) → UDP, feeding the dumb forwarder DMAC + VLAN + L4 port through SMD. Syntax illustrative; register pressure peaks at 3 of 4 GPRs.

```asm
start:
    SETHDR  h_eth
    EXT     r0, 0, 48          ; DMAC
    STMD    smd_dmac, r0       ; forwarding key for the dumb forwarder
    EXT     r0, 96, 16         ; EtherType
    ADVI    14
dispatch:
    MOVI    r1, 0x8100
    BEQ     r0, r1, vlan
    MOVI    r1, 0x0800
    BEQ     r0, r1, ipv4
    HALT    accept             ; unknown L3: accept with what we know
vlan:
    SETHDR  h_vlan
    EXT     r2, 0, 16          ; TCI
    STMD    smd_vlan, r2
    EXT     r0, 16, 16         ; inner EtherType
    ADVI    4
    JMP     dispatch           ; QinQ loop — bounded by step budget
ipv4:
    SETHDR  h_ipv4
    EXT     r1, 0, 4           ; version — pre-masked by bit-granular EXT
    MOVI    r2, 4
    BNE     r1, r2, drop
    EXT     r1, 4, 4           ; IHL
    EXT     r2, 72, 8          ; protocol
    SHL     r1, r1, 2          ; header length in bytes
    ADVR    r1                 ; skip header incl. options
    MOVI    r1, 17
    BEQ     r2, r1, udp
    HALT    accept
udp:
    SETHDR  h_udp
    EXT     r1, 16, 16         ; dst port
    STMD    smd_l4_dport, r1
    ADVI    8
    HALT    accept
drop:
    HALT    drop
```

The demo's beat 3 (invented protocol) needs only these same instructions by construction — the protocol is ours to design, and ADVR handles arbitrary length fields in byte units.

## Encoding envelope (verified to fit, exact layout at spec stage)

32-bit fixed width. Worst cases: `EXT` = op(6) + rd(3) + boff(11, for 256B buffer in bits) + bsize(6) = 26 ✓. `MOVI` = op(6) + rd(3) + imm(16) = 25 ✓. `BEQ` = op(6) + rs(3) + rt(3) + offset(≤20) ✓.

## Open for stage 1 (spec work, not design)

Exact opcode assignments and field layouts · assembly syntax and directives · SMD field map (program fields vs. hardware status fields) · parameter plumbing in Sail · default step budget value.
