# Nanuk MAP ISA v0 — Design

**Date:** 2026-07-11
**Status:** Approved design sketch. Exact encodings, assembly syntax, and MAP-SMD layout are finalized during M1 (Sail spec).
**Parent:** [Match-Action Extension](2026-07-11-map-extension-design.md) · [Project design](2026-07-11-nanuk-project-design.md)
**Sibling:** [Parser ISA v0](2026-07-11-parser-isa-v0-design.md) — same school rules, different job.

## Lineage

A fresh, original ISA informed by xISA's MAP ISA — not a subset, not compatible. Each xISA MAP concept auditions:

| xISA concept | Nanuk MAP v0 decision |
|---|---|
| Separate MAP ISA beside the Parser ISA | **Adopted** — sibling ISAs, clean boundary |
| Header-relative memory operands (HeaderOffsetID + offset) | **Adopted** — PP hdr_offsets consumed by the addressing mode |
| Table lookup instruction | **Adopted**, synchronous, fused branch-on-miss (async + LFLAG + dependency checker deferred) |
| Checksum coprocessor (CHKSUMUPD family) | **Adopted** as `CSUMUPD` (synchronous; TST/CALC variants deferred) |
| Length edits as head-delta at send (FrameDelta), never splice | **Adopted** — signed delta on SEND, headroom writes |
| SEND without halt (post-send program continuation) | **Rejected for v0** — SEND/DROP terminate |
| Z/N condition flags, LFLAGs | **Rejected** — flagless school: fused compare-and-branch, fused lookup-branch |
| Multiple entry points (per ingress port) | **Rejected for v0** — single entry, program dispatches on SMD |
| Register preloading at entry, 128-bit registers | **Re-sized/rejected** — 4×64-bit + `rz`, `LDMD` instead of preloads |
| Multi-buffer LBALLOC/FREBASE, clone/mirror, reparse | **Deferred** |

## Machine state

| State | Size (default) | Notes |
|---|---|---|
| `r0`–`r3` | 4 × 64 bits | GPRs; `rz` reads 0, writes discarded (3-bit field: room to grow) |
| window | `H + 256` bytes | `H` = headroom (parameter, default 32), then the frame as the PP saw it. Read-write |
| `h_frame`, `h_*` | header bases | `h_frame` pinned to frame start (window index `H`); PP hdr_offsets hardware-biased by `+H` |
| inbound SMD | 128 bits | PP's SMD + hardware fields (ingress port, `md_flood` = all_ports & ~ingress, hdr_present bits); read via `LDMD` |
| outbound SMD | 128 bits | verdict (sent/drop/error), status/error codes, egress bitmap, head-delta — consumed by egress glue |
| tables | `N_TABLES` slots (default 4) | exact-match; per-table key width ≤64b, action width ≤64b, entries. Control-plane programmed; empty table = always-miss |
| `pc` | 16 bits | 32-bit fixed-width instructions, word-addressed; entry at 0 |
| instruction memory | ~1K entries | parameterized |
| step budget | counter | watchdog, parameterized; same check-before-execute semantics as the parser |

## Instruction set — the strict core (12 instructions)

Byte-granular throughout — a deliberate contrast: the parser is the bit machine (sub-byte extraction is its job), the MAP is the byte machine (every field it edits is byte-aligned).

| Instruction | Semantics |
|---|---|
| `LD rd, hdr+off, n` | rd ← zero-extended `n` bytes (1–8) at signed byte offset `off` from header base `hdr` |
| `ST rs, hdr+off, n` | window bytes ← low `n` bytes of rs; negative `off` reaches headroom |
| `LDMD rd, field` | rd ← inbound-SMD field (mirror of parser `STMD`) |
| `MOVI rd, imm16` | rd ← zero-extended 16-bit immediate |
| `ADDI rd, rs, imm` | rd ← rs + sign-extended immediate (TTL decrement: `ADDI r1, r1, -1`) |
| `LOOKUP rd, table, rs, miss` | exact-match: key = rs masked to table key width. Hit: rd ← action data, fall through. Miss: rd ← 0, branch to `miss` |
| `CSUMUPD hdr+off` | recompute IPv4 header checksum at location, write back in place (black-box accelerator; xISA CHKSUMUPD precedent) |
| `BEQ rs, rt, target` | branch if rs = rt |
| `BNE rs, rt, target` | branch if rs ≠ rt |
| `JMP target` | unconditional |
| `SEND rs, delta` | terminate: transmit to port bitmap rs; signed `delta` prepends (`>0`, frame starts at `h_frame − delta`) or strips (`<0`) |
| `DROP` | terminate without output |

**Deliberately absent, with named re-entry triggers:**

- `SHL`/shifts — header-relative addressing killed address arithmetic; returns with a field-packing need
- `ADD/SUB` (reg-reg), `AND/OR/XOR` — return with a demo that computes across two loaded fields
- Multi-register (>64b) lookup keys — return with a 5-tuple demo
- `CSUMTST`/`CSUMCALC` — return with checksum *verification* or non-IPv4 checksum demos
- Async LOOKUP/CSUM (LFLAG-style) — return when RTL pipelining makes latency hurt
- Data-plane table writes (learning) — learning-switch demo
- SEND-without-halt, clone/mirror — post-send bookkeeping or mirroring demo
- Reparse (MAP → PP) — decap-then-reparse a single MAP program can't express
- `HALT` — not an opcode; SEND/DROP are the terminators

## Totality rules

Mirror the parser's, same doctrine (no undefined behavior, errors are observable outcomes in outbound SMD):

1. **Window violation:** LD/ST/CSUMUPD reaching outside `[0, H+256)` → error halt.
2. **Step budget exhausted** (backward branches allowed) → error halt.
3. **Illegal instruction / all-zeros** → error halt (all-zeros illegal, not NOP).
4. **Falling off the program** (PC past last instruction) → illegal-instruction error halt.
5. **SEND delta out of range** (beyond headroom / frame) → error halt.
6. Error halts produce verdict = error-drop + status in outbound SMD; the egress glue drops. Consumers never see undefined state.
7. `LOOKUP` on any table id is defined (unconfigured/empty = miss); `LDMD` of every architected field is defined.

## Proof by program: the three demos

**1. L2 forward (5 instructions):**

```asm
    LD      r0, h_eth+0, 6         ; DMAC
    LOOKUP  r1, t_l2, r0, miss     ; hit: r1 = egress port bitmap
    SEND    r1, 0
miss:
    LDMD    r1, md_flood           ; hardware: all_ports & ~ingress
    SEND    r1, 0
```

**2. TTL decrement + rewrite:**

```asm
    LD      r0, h_ipv4+8, 1        ; TTL
    BEQ     r0, rz, expired        ; TTL 0 → drop
    MOVI    r1, 1
    BEQ     r0, r1, expired        ; TTL 1 would decrement to 0 → drop (router rule: drop if TTL ≤ 1;
    ADDI    r0, r0, -1             ;   note 64-bit regs make decrement-then-test-zero wrap on TTL=0)
    ST      r0, h_ipv4+8, 1
    CSUMUPD h_ipv4+0               ; accelerator fixes the header checksum
    LD      r0, h_eth+0, 6         ; then L2-forward as above
    LOOKUP  r1, t_l2, r0, miss
    SEND    r1, 0
miss:
    LDMD    r1, md_flood
    SEND    r1, 0
expired:
    DROP
```

**3. nanukproto tunnel push (encap; pop is the mirror with `delta = -22`):**

```asm
    ; encap decision via lookup on DMAC (action data = outer dst tag + port bitmap, packed ≤64b)
    LD      r0, h_eth+0, 6
    LOOKUP  r1, t_tun, r0, plain
    ST      r1, h_frame-22, 6      ; outer DMAC from action data
    MOVI    r2, 0x4E4B             ; 'NK' outer src/type constants...
    ST      r2, h_frame-10, 2      ;   (illustrative field layout)
    MOVI    r2, 0x0801             ; nanukproto EtherType
    ST      r2, h_frame-8, 2       ; tunnel header fields...
    SEND    r1, 22                 ; transmit with 22B prepended
plain:
    LDMD    r1, md_flood
    SEND    r1, 0
```

**Design constraint discovered by demo 3:** nanukproto encap must be **full L2-in-L2** — `outer Eth | nanukproto hdr | original frame` (22B ≤ 32B headroom) — so push/pop stay head-only (no splice). The existing parse-side nanukproto grows an outer-header variant at M1.

Register pressure peaks at 3 of 4 GPRs. Every instruction is used by a real demo program; nothing on symmetry grounds.

## Encoding envelope (verified to fit; exact layout at M1)

32-bit fixed width, 6-bit opcode space shared conventions with the parser (separate opcode plane — the two ISAs are not co-resident but keep the same decoder idioms). Worst cases: `LD/ST` = op(6)+r(3)+hdr(4)+off(signed 10)+n(4) = 27 ✓ · `LOOKUP` = op(6)+rd(3)+table(4)+rs(3)+target(16) = 32 ✓ (exactly) · `ADDI` = op(6)+rd(3)+rs(3)+imm(16) = 28 ✓ · `SEND` = op(6)+rs(3)+delta(signed 10) = 19 ✓ · `CSUMUPD` = op(6)+hdr(4)+off(10) = 20 ✓ · branches = op(6)+3+3+target(16) = 28 ✓.

## Open for M1 (spec work, not design)

Exact opcodes and field layouts · assembly syntax/directives · inbound/outbound MAP-SMD field maps · table config record format (control-plane API: key width, action width, entry add/remove) · PP→MAP metadata pass-through fields (leaning yes per parent doc — resolved when the composed rig is built) · default step budget · Sail parameter plumbing · outer-header nanukproto variant.
