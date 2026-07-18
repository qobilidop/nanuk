# Plan — SIIT demo, part A: the semantic core

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Date:** 2026-07-18
**Spec:** [SIIT demo design](../specs/2026-07-18-siit-demo-design.md) ·
[Application survey](../../notes/2026-07-18-demo-application-survey.md)
**Status:** Ready.

**Goal:** RFC 7915 stateless SIIT running on the Nanuk core: reference
translator (executable spec) → RFC audit → generated committed vectors → the
`examples/siit` program passing them on the golden emulator → parity at every
semantic level → RTL cosim.

**Architecture:** Test-first from the RFC. The reference translator in
`nanuk.testkit` is the oracle; a combinatorial generator runs inputs through
it and commits vector files under `benchmarks/siit/`; the Nanuk program must
reproduce every vector byte-for-byte at every level (emulator, ISS, interp,
RTL). Plan B (Jool graybox replay) and Plan C (playground + SimBricks beats)
follow after this lands.

**Tech stack:** Python (uv, pytest, scapy dev-only in testkit), PP/MAP asm +
eDSL, existing harnesses (`nanuk.testkit.map_harness.run_pipeline`), Amaranth
cosim rig.

## Global constraints

- **Zero ISA / core-interface / Sail changes.** If a task appears to need
  one, STOP — that's a spec violation to raise, not a change to make.
- Python runs in the devcontainer only (`./dev.sh`, or
  `docker run -v $PWD:/workspace <devcontainer image>`); emulator binaries are
  linux builds at `spec/sail/build/`.
- After any pyproject edit: `uv sync` (with extras) then `uv run --no-sync`.
- scapy stays inside `nanuk.testkit`/tests — never in shipping code, never in
  vector *replay* (committed vectors are plain JSON+hex; scapy only generates).
- The Nanuk artifact is called **SIIT**, never "NAT64", in all docs/comments.
- Naming: token-tier files may say `siit`; prose spells "SIIT translator".
- Frequent commits; every task ends green: SW suite
  `cd sw/python && uv run --no-sync pytest tests` and (where touched) HW suite
  `cd hw/amaranth && NANUK_COSIM=1 uv run --no-sync pytest tests`.

## Frozen decisions (the plan-level calls, so tasks don't re-litigate)

**Addressing (RFC 7757 precedence: EAMT first, then RFC 6052 pool6):**

- pool6 = the well-known prefix `64:ff9b::/96`; baked as program constants
  and as the default `SiitConfig.pool6`.
- EAMT is exact-match only (general prefix EAMT waits for LPM/T3).
- Table plane (LOOKUP keys/actions are ≤64-bit — the reason for the hi/lo
  split; confirm widths against `spec/sail/model/map/` before coding, and if
  128-bit keys are somehow legal, keep the scheme below anyway for RTL cost
  honesty):
  - `t0`: v4→v6 EAMT, key = v4 addr (32b), action = v6 addr high 64b.
  - `t1`: v4→v6 EAMT, key = v4 addr (32b), action = v6 addr low 64b.
  - `t2`: v6→v4 EAMT, key = v6 addr **low 64 bits**, action = v4 addr (32b).
    Documented demo constraint: EAMT v6 entries must be distinct in their low
    64 bits (true of any sane EAMT; full generality is the LPM trigger).
- v6→v4 source of truth: if dst (and src) carry the pool6 prefix → 6052
  extract (bytes 12..16 of the address); else t2 lookup; miss → DROP
  (untranslatable-address decision, recorded in the audit).

**Header mappings (RFC 7915 §4.1/§5.1), the field-by-field freeze:**

v4→v6 (head grows 20B net):
| IPv6 field | value |
|---|---|
| version | 6 |
| traffic class | v4 TOS |
| flow label | 0 |
| payload length | v4 total length − 4·IHL |
| next header | v4 protocol, with ICMP 1 → ICMPv6 58 |
| hop limit | v4 TTL − 1; **if TTL ≤ 1 → DROP** (no ICMP-error generation in scope) |
| src / dst | EAMT-else-6052 embed |

v6→v4 (head shrinks 20B net):
| IPv4 field | value |
|---|---|
| version/IHL | 4 / 5 (never emit options) |
| TOS | v6 traffic class |
| total length | v6 payload length + 20 |
| identification | 0, DF=1, MF=0, frag offset 0 (deterministic; RFC 7915-sanctioned post-RFC 8021 policy) |
| TTL | hop limit − 1; **if hop limit ≤ 1 → DROP** |
| protocol | next header, 58 → 1 |
| header checksum | computed fresh (CSUM instruction) |
| src / dst | 6052-extract-else-EAMT |

**Ingress validation (totality ledger — every packet gets a verdict):**
non-IPv4/IPv6 EtherType → PP refusal verdict (pass-through decision recorded
in md; MAP DROPs). Runt / truncated → PP window refusal. v4 header checksum
invalid (CSUM over IHL·4 bytes ≠ 0xFFFF fold) → DROP. v4 UDP checksum 0 →
DROP (spec decision). Unsupported L4 (not UDP/TCP/ICMP-echo) → DROP.
ICMP non-echo types → DROP (error translation deferred). Fragments
(v4 MF/offset ≠ 0, v6 fragment header) → DROP. VLAN → out of scope for the
siit parser (no tag handling; framing convention is untagged).

**L4 checksum algebra (RFC 1624 incremental form, `HC' = ~(~HC + ~m + m')`):**
UDP/TCP pseudo-header length+proto contributions are equal on both sides
(same upper-layer length; proto 6/17 unchanged), so only address words
differ: patch by folding `sum(new address bytes) − sum(old address bytes)`
via CSUM over the address ranges + end-around-carry adds (the flagless
carry idiom from `examples/icmp_echo`). ICMP: v4 has no pseudo-header, v6
does — echo translation patches by (type-word delta) ± pseudo-header sum
(v6 src+dst + upper-layer length + 58). v4→v6 UDP: checksum is never 0 on
the way in (dropped) so no special case; v6→v4 UDP checksum passes through
patched (legal either way; deterministic).

**Ethernet framing convention:** vectors and program operate on full frames;
the translator rewrites **only** the EtherType (0x0800 ↔ 0x86DD); MACs pass
through untouched (L2 forwarding is the switch's job, not the translator's).

**md conventions:** slot 0 stays system (egress). PP writes the
header-present bitmap to the slot the l2l3l4 convention already uses (read
`examples/l2l3l4/parse.asm` and reuse the same slot; the bitmap covers the
new siit header ids). PP header ids for siit: 0 eth, 1 ipv4, 2 ipv6, 3 udp,
4 tcp, 5 icmpv4, 6 icmpv6.

**Vector file schema** (committed, scapy-free replay):
`benchmarks/siit/vectors/<group>.json` = list of
`{"name": str, "rfc": str, "dir": "46"|"64", "in": hex, "verdict": "sent"|"drop", "out": hex|null, "why": str}`.
Groups: `udp46 udp64 tcp46 tcp64 icmp46 icmp64 edge negative`.

---

### Task 1: Reference translator (the executable spec)

**Files:**
- Create: `sw/python/nanuk/testkit/siit_ref.py`
- Test: `sw/python/tests/test_siit_ref.py`

**Interfaces (later tasks rely on these exact names):**
- `SiitConfig(pool6: bytes = WKP, eamt: tuple[tuple[str, str], ...] = ())` —
  `pool6` is the 12-byte /96 prefix; `eamt` pairs are (dotted-v4, v6 colon
  form); `__post_init__` derives the lookup dicts `eamt46: dict[bytes, bytes]`
  (4-byte key → 16-byte value) and `eamt64` (inverse) that the code below and
  `siit_tables()` consume. Frozen default demo config lives here too:
  `DEMO_SIIT = SiitConfig(eamt=(("192.0.2.1", "2001:db8:1::c001"),))`.
- `WKP = bytes.fromhex("0064ff9b000000000000000000")[:12]` (64:ff9b::/96).
- `translate(frame: bytes, cfg: SiitConfig = DEMO_SIIT) -> SiitResult` where
  `SiitResult(verdict: str, frame: bytes | None, why: str)`, verdict in
  `{"sent", "drop"}`.
- Pure stdlib + scapy for parsing convenience is allowed here (testkit is
  dev-only), but the *output* frame must be built by explicit byte assembly,
  not scapy recompute — the reference must encode OUR decisions (ID=0, DF=1,
  TTL−1), not scapy defaults.

**Steps:**

- [ ] **Write failing known-answer tests first** (`test_siit_ref.py`). Cover,
  each with hand-derived expected bytes (compute checksums in the test with
  the local `ones_csum` helper pattern from `tests/test_benchmarks_map.py`):
  - `test_udp46_6052`: v4 UDP frame (src 198.51.100.2, dst 192.0.2.33, both
    6052-embedded) → v6 frame; assert full output frame equality AND
    the named fields (EtherType 0x86DD, TC==TOS, payload_len, hop_limit ==
    TTL−1, embedded addresses `64:ff9b::c633:6402` / `64:ff9b::c000:221`),
    AND that the patched UDP checksum verifies (pseudo-header sum == 0xFFFF
    fold).
  - `test_udp64_6052`: exact inverse input; assert IPv4 ID==0, DF set, fresh
    header checksum verifies, TTL == hop_limit−1.
  - `test_eamt_beats_6052`: dst = 192.0.2.1 (in DEMO_SIIT eamt) → v6 dst
    2001:db8:1::c001, not the 6052 embed.
  - `test_tcp46_checksum_patch`: TCP both directions verify like UDP.
  - `test_icmp46_echo`: v4 echo request (type 8) → ICMPv6 128, checksum
    verifies against the v6 pseudo-header.
  - Drops with reasons: `test_ttl1_drops`, `test_zero_udp_csum_drops`,
    `test_bad_v4_header_csum_drops`, `test_fragment_drops`,
    `test_icmp_error_drops` (type 3), `test_unknown_l4_drops` (proto 47),
    `test_non_ip_ethertype_drops` (ARP), `test_v6_dst_neither_pool6_nor_eamt_drops`.
- [ ] Run: `cd sw/python && uv run --no-sync pytest tests/test_siit_ref.py -q`
  → all FAIL (module missing).
- [ ] Implement `siit_ref.py`. Shape (complete the obvious ellipses; keep it
  ~200 lines, byte-assembly style):

```python
def _fold(s: int) -> int:
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return s

def _sum16(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    return _fold(sum(struct.unpack("!%dH" % (len(data) // 2), data)))

def _patch(csum: int, old: bytes, new: bytes) -> int:
    # RFC 1624: HC' = ~fold(~HC + ~sum(old) + sum(new))
    return (~_fold((~csum & 0xFFFF) + (~_sum16(old) & 0xFFFF) + _sum16(new))) & 0xFFFF

def _addr46(v4: bytes, cfg: SiitConfig) -> bytes:
    return cfg.eamt46.get(v4) or cfg.pool6 + v4

def _addr64(v6: bytes, cfg: SiitConfig) -> bytes | None:
    if v6[:12] == cfg.pool6:
        return v6[12:16]
    return cfg.eamt64.get(v6)   # None -> untranslatable
```

  with `translate()` dispatching on EtherType, running the ingress-validation
  ledger in the frozen order, building the new IP header per the frozen field
  tables, and patching/recomputing checksums per the frozen algebra. Every
  drop returns the ledger's `why` string (these become vector `why` fields).
- [ ] Run the tests → PASS.
- [ ] Commit: `feat(testkit): SIIT reference translator — RFC 7915 as executable spec`

### Task 2: RFC 7915 requirements audit

**Files:**
- Create: `benchmarks/siit/audit.md`, `benchmarks/siit/README.md`

**Interfaces:** audit section anchors (`#s4-1`, `#s5-1`, …) are cited by
vector `rfc` fields (Task 3) and by the deferred-triggers list.

**Steps:**

- [ ] Fetch RFC 7915 (`https://www.rfc-editor.org/rfc/rfc7915.txt`) and RFC
  6052 §2. Walk §1, §4 (v4→v6), §5 (v6→v4) clause by clause in the Jool-7915
  genre crossed with `benchmarks/coverage.md`: quote or tightly paraphrase
  each normative statement, disposition it as one of
  **tested(group/vector)** / **deferred(trigger)** / **refused(rationale)** /
  **not-a-requirement**. Every frozen decision in this plan (ID=0+DF, TTL≤1
  drop, zero-csum drop, no options emission, EAMT-low64) must appear as an
  explicit disposition with its rationale.
- [ ] `README.md`: one screen — what the suite is, the four legs from the
  spec, how to regenerate vectors, where Plan B/C artifacts will land.
- [ ] Self-check: grep the audit for every `rfc` anchor the vectors will cite
  (Task 3 lists them); no dangling anchors either direction.
- [ ] Commit: `docs(benchmarks): SIIT RFC 7915 clause-by-clause audit`

### Task 3: Vector generator + committed vectors

**Files:**
- Create: `benchmarks/siit/gen_vectors.py`,
  `benchmarks/siit/vectors/{udp46,udp64,tcp46,tcp64,icmp46,icmp64,edge,negative}.json`
- Test: `sw/python/tests/test_siit_vectors.py`

**Interfaces:**
- `gen_vectors.py` (run: `cd sw/python && uv run --no-sync python
  ../../benchmarks/siit/gen_vectors.py`) imports `nanuk.testkit.siit_ref`,
  builds inputs with scapy, computes expected outputs via `translate()`,
  writes the JSON groups sorted by name (deterministic output — no
  randomness, no timestamps).
- Replay helper for all later tasks:
  `nanuk.testkit.siit_ref.load_vectors(group: str | None = None) ->
  list[dict]` reading `benchmarks/siit/vectors/*.json` by repo-relative path
  (same `parents[4]` anchoring as `testkit/load.py`).

**Steps:**

- [ ] Write `test_siit_vectors.py` first:
  - `test_regen_is_byte_identical`: run the generator into a tmp dir, diff
    against the committed files (this is the drift tripwire).
  - `test_every_vector_agrees_with_reference`: replay every committed vector
    through `translate()`; verdict+frame must match (guards hand-edits).
  - `test_vectors_cite_real_audit_anchors`: every `rfc` value appears in
    `benchmarks/siit/audit.md`.
- [ ] Implement the generator. Enumerate, through the reference translator:
  - the 6 protocol×direction groups × payload lengths {0, 4, 25(odd)} ×
    addressing {6052, eamt} × TTL/hop {64, 2};
  - `edge`: TOS/TC nonzero, v4 options present (IHL 6, translated fine),
    max-window-adjacent lengths, minimum frames;
  - `negative`: every drop in the ingress ledger, one vector each, `why`
    matching the reference's reason strings.
- [ ] Run generator; commit vectors; run the three tests → PASS.
- [ ] Commit: `feat(benchmarks): SIIT conformance vectors, generated from the executable spec`

### Task 4: The siit program

**Files:**
- Create: `examples/siit/README.md`, `examples/siit/parse.asm`,
  `examples/siit/translate.asm`
- Test: `sw/python/tests/test_siit_program.py`
- Modify: `sw/python/nanuk/testkit/testkit.py` (add `siit_tables()`)

**Interfaces:**
- `testkit.siit_tables(cfg: SiitConfig = DEMO_SIIT) -> list[Table]` builds
  t0/t1/t2 per the frozen table plane (int keys/actions big-endian from the
  address bytes).
- `parse.asm`: PP program; header ids per the frozen list; md bitmap per the
  l2l3l4 slot convention; accepts exactly {v4, v6} × {udp, tcp, icmp-echo
  types only}; refusal verdicts for everything else per the ledger (PP
  refuses what it can see: EtherType, truncation; value-dependent drops like
  bad checksum / TTL are MAP's).
- `translate.asm`: MAP program implementing the frozen mappings; terminates
  SEND (delta per direction) or DROP; scratch via the headroom idiom where
  registers run out.

**Steps:**

- [ ] Write `test_siit_program.py` first — the whole file is one rig:

```python
from nanuk.isa import map_asm, pp_asm
from nanuk.testkit import map_harness
from nanuk.testkit.siit_ref import DEMO_SIIT, load_vectors
from nanuk.testkit.testkit import siit_tables

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
PP = pp_asm.assemble((EXAMPLES / "siit" / "parse.asm").read_text())
MP = map_asm.assemble((EXAMPLES / "siit" / "translate.asm").read_text())

@pytest.mark.parametrize("vec", load_vectors(), ids=lambda v: v["name"])
def test_vector_on_golden_model(vec):
    pp, r = map_harness.run_pipeline(
        PP, MP, bytes.fromhex(vec["in"]), siit_tables(), md_in=(0,) * 8)
    if vec["verdict"] == "sent":
        assert r is not None and r.sent and r.error == 0
        assert r.frame == bytes.fromhex(vec["out"])
    else:
        assert r is None or not r.sent   # PP refusal or MAP drop

def test_step_budget_canary():
    worst = max(load_vectors(), key=lambda v: len(v["in"]))
    ...  # run, assert steps < 200 and record the number in the assert message
```

- [ ] Run it → FAIL (no program).
- [ ] Write `parse.asm` (l2l3l4 is the donor: EtherType dispatch, IHL
  handling; add the v6 arm — fixed 40B header, next-header byte at offset 6).
  Iterate until PP-level behaviors (refusals, hdr offsets) look right via a
  few direct `pp_harness.run_program` spot-tests inside the test file.
- [ ] Write `translate.asm` in this order, re-running the vector rig after
  each: (1) v4→v6 UDP 6052 happy path; (2) v6→v4 UDP; (3) EAMT lookups
  (t0/t1/t2, miss handling); (4) TCP (same patch, different csum offset);
  (5) ICMP echo both directions; (6) the drop ledger (v4 header csum verify
  first, then TTL, zero-csum, fragments...). Register budget is 4 GPRs —
  plan the allocation in a header comment; spill to headroom scratch
  (`st`/`ld` at negative offsets) as icmp_echo does.
- [ ] Full vector suite green on the golden model, canary recorded.
- [ ] `examples/siit/README.md`: what it is, the CLAT story, the table plane,
  scope pointers to audit/spec.
- [ ] Run the whole SW suite: `uv run --no-sync pytest tests` → green.
- [ ] Commit: `feat(examples): siit — RFC 7915 SIIT translator on the Nanuk core`

### Task 5: eDSL twins + all-levels parity

**Files:**
- Create: `examples/siit/parse.py`, `examples/siit/translate.py`
- Test: `sw/python/tests/lang/test_siit_parity.py`

**Interfaces:**
- Twins follow the nanukproto pattern: `parse.py` exposes `make_parser()` /
  `build_ir()`; `translate.py` exposes `build_map_ir()`; standalone-document
  rule — every constant (header ids, pool6 bytes, table ids) declared inline.

**Steps:**

- [ ] Write `test_siit_parity.py` first (donors: `test_map_parity.py`,
  `test_pp_interp_parity.py`, `test_pp_iss_parity.py`): over every committed
  vector, assert agreement of
  (a) eDSL-lowered asm vs the hand asm — behavior fields, not steps;
  (b) `pp_interp`/`map_interp` on the twins' IR vs the emulator result;
  (c) ISS (`run_pp_iss`/`run_map_iss`) on the assembled words vs the
  emulator — all seven contract fields including steps, frame bytes
  identical.
- [ ] → FAIL (no twins). Write the twins; iterate to green.
- [ ] PP symex leg: extend the rig with the `test_pp_symex_parity.py` pattern
  — enumerate siit parser paths, validate every witness on interp AND emulator,
  and assert the witness corpus reaches every PP verdict the audit's ledger
  names (program-coverage claim, PP scope only — MAP symex stays parked).
- [ ] SW suite green. Commit:
  `feat(lang): siit eDSL twins — parity at every semantic level`

### Task 6: RTL cosim leg

**Files:**
- Test: `hw/amaranth/tests/test_siit_cosim.py`

**Steps:**

- [ ] Write the test (donor: `hw/amaranth/tests/test_core.py` stream-BFM
  pattern): drive every committed vector's input frame through `NanukCore`
  configured with the siit programs + `siit_tables()`, oracle = chained ISS;
  assert verdict/error/md/frame byte-identical; include the >256B
  tail-passthrough vector from `edge`.
- [ ] Run: `cd hw/amaranth && uv sync && NANUK_COSIM=1 uv run --no-sync
  pytest tests/test_siit_cosim.py -q` → green (first run may be slow;
  that's fine).
- [ ] Full HW suite green. Commit:
  `test(hw): siit vectors through the RTL — application corpus joins cosim`

### Task 7: Close out part A

- [ ] Cross-check the audit one final time: every disposition "tested(x)"
  points at a passing vector/test; counts in `benchmarks/siit/README.md`.
- [ ] Lab notes: `docs/notes/2026-07-18-siit-core-lab-notes.md` (what bit,
  what the canaries read, any reference-vs-program disagreements found and
  which side was wrong).
- [ ] Full gates: Sail ctest (untouched, but run it), SW suite, HW suite —
  all green in the devcontainer; push and confirm cloud CI.
- [ ] Commit: `docs(notes): siit core lab notes` — then STOP: Plan B (Jool
  oracle) and Plan C (demo tiers) get written against what actually landed.

## Self-review notes

- Spec coverage: legs 1–3 of the spec's test architecture are Tasks 2, 1+3,
  4–6 respectively; leg 4 (Jool) is deliberately Plan B; tiers are Plan C;
  success criteria 1–2 and 6 are covered here, 3–5 by B/C.
- The one intentionally unfrozen detail: exact md slot for the bitmap and
  exact PP refusal codes — both are read-from-donor decisions
  (`examples/l2l3l4/parse.asm`), pinned by tests once chosen.
- Type consistency: `SiitConfig`/`translate`/`load_vectors`/`siit_tables`
  names match across Tasks 1/3/4/5/6.
