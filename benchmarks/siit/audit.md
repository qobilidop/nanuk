# SIIT — RFC 7915 clause-by-clause requirements audit

**Date:** 2026-07-18
**Status:** Complete for the first landing. Lives at `benchmarks/siit/audit.md`.
**Spec:** [SIIT demo design](../../docs/superpowers/specs/2026-07-18-siit-demo-design.md) ·
**Plan:** [SIIT part A](../../docs/superpowers/plans/2026-07-18-siit-a-core.md) ·
**Sibling genre:** [`benchmarks/coverage.md`](../coverage.md)

This is the **scope ledger** for the SIIT translator — a stateless IPv4↔IPv6
translator (RFC 7915 + RFC 6052 addressing + RFC 7757 explicit mappings)
running on the Nanuk core. It walks RFC 7915 §1 (applicability), §4 (IPv4→IPv6),
and §5 (IPv6→IPv4) clause by clause, quotes or tightly paraphrases each
normative statement, and gives every one an explicit **disposition**. It is the
book-chapter seed and the definition of "done": leg 1 of the four-leg test
architecture ([README](README.md)).

We build **SIIT, not a port of Jool.** The RFC is the spec; Nanuk's semantics
stay sovereign where the RFC leaves choices, and every such choice is recorded
here with its rationale. The artifact is the **SIIT translator**, never
"NAT64" — stateful NAT64 (RFC 6146) is a standing architectural refusal, not a
deferral.

## Method

Each clause is dispositioned as exactly one of:

- **tested(_group_)** — a committed vector group exercises it. The group is one
  of the eight in the [vector schema](../../docs/superpowers/plans/2026-07-18-siit-a-core.md):
  `udp46` `udp64` `tcp46` `tcp64` `icmp46` `icmp64` `edge` `negative`.
  (`edge` = addressing/options/boundary cases; `negative` = drop-verdict cases.)
- **deferred(_trigger_)** — out of scope for the first landing, parked with the
  named trigger that would pull it back in.
- **refused(_rationale_)** — a standing architectural boundary; will not be
  built.
- **not-a-requirement** — not a packet-observable translation obligation
  (config-provision SHOULDs, deployment/routing guidance, taxonomy prose), or a
  requirement Nanuk satisfies structurally with nothing to test.

Every disposition carries a **stable ID** (fixed first column, e.g.
`7915-4.1-tos`). Task 3's vectors cite these IDs in their `rfc` field — they are
rename-proof, unlike the GitHub heading anchors (`#41-ipv4-to-ipv6-header`),
which vectors may also cite. All IDs are collected in the [summary
table](#summary-table).

**Ground truth for dispositions and drop reasons** is the reference translator
`sw/python/nanuk/testkit/siit_ref.py` — its docstrings define the ledger order
and its drop-reason strings (`runt`, `non_ip_ethertype`, `v4_truncated`,
`l4_truncated`, `v4_bad_header_checksum`, `fragment`, `zero_udp_checksum`,
`icmp_error`, `unsupported_l4`, `ttl_expired`, `untranslatable_address`,
`v6_truncated`) are the exact `why` values that `negative`-group vectors assert.
Where this audit and any other document disagree on ordering, the ledger in
`siit_ref.py` wins.

---

## 1. Applicability (RFC 7915 §1)

§1 frames the translation model and draws the stateless/stateful line. Little
here is directly testable; it fixes scope.

| ID | Clause (RFC 7915 §1) | Disposition | Rationale / vector |
|---|---|---|---|
| `7915-1-stateless` | Stateless mode: translate IPv4↔IPv6 "solely based on configuration and information contained within the packet"; no session state. | not-a-requirement | This is Nanuk's whole design. Evidenced jointly by every group; no single vector. The pool6 prefix and EAMT are baked configuration, per packet only. |
| `7915-1-stateful` | Stateful mode maintains a dynamic binding table; all packets of a flow must traverse the same translator (NAT64, RFC 6146). | **refused** | Per-flow session state is a standing architectural refusal. Nanuk has no learning/RMW state plane (see `coverage.md` negative set). The honest name is SIIT; docs never say "NAT64". |
| `7915-1-no-options-xlate` | IPv4 options are not translated. | tested(`edge`) | We ignore options and find the payload via IHL — see [`7915-4.1-options`](#41-ipv4-to-ipv6-header). |
| `7915-1-no-exthdr-xlate` | IPv6 extension headers, except the Fragment Header, are not translated. | deferred(extension-header traversal) | Trigger: same arc as ICMP-error/fragmentation. Our parser handles only UDP/TCP/ICMPv6/Fragment next-headers; any other next header → `unsupported_l4` drop (see [`7915-5.1-exthdr`](#51-ipv6-to-ipv4-header)). |
| `7915-1-multicast` | IPv4 multicast addresses cannot be mapped to IPv6 multicast. | **refused** | Multicast is a spec non-goal. Multicast destinations are neither WKP-embeddable nor EAMT-mapped, so v6→v4 they fall to `untranslatable_address`; v4→v6 they are out of the demo's addressing plan. |
| `7915-1-frag-not-xlated` | Fragmented UDP without a checksum, and fragmented ICMP/ICMPv6, are not translated. | deferred(fragmentation) + tested(`negative`) | Any fragment → `fragment` drop today (see [`7915-4.1-frag`](#41-ipv4-to-ipv6-header)); full fragment translation is deferred. |

---

## 4. IPv4-to-IPv6 (RFC 7915 §4)

Head grows 20 B net: a fresh 40 B IPv6 header is written over the old IPv4
header region plus headroom. The ingress ledger (drop ordering) is **outer-in**,
identical in both directions — see [`7915-ledger-order`](#frozen-decisions-ledger).

### 4.1. IPv4-to-IPv6 header

| ID | Clause (RFC 7915 §4.1) | Disposition | Rationale / vector |
|---|---|---|---|
| `7915-4.1-version` | Version = 6. | tested(`udp46`) | `v6[0] = 0x60 \| …`. Asserted on every v4→v6 output frame. |
| `7915-4.1-tos` | Traffic Class = copied from the IPv4 TOS octet (default). | tested(`udp46`, `edge`) | `v6[0..1]` carry all 8 TOS bits. `edge` varies non-zero TOS/DSCP/ECN. |
| `7915-4.1-tos-ignore` | SHOULD offer a config option to ignore IPv4 TOS and set Traffic Class to 0. | not-a-requirement | Config-provision SHOULD, no packet-observable default behavior beyond `7915-4.1-tos` (which we implement — copy). A knob, not a translation rule. |
| `7915-4.1-flowlabel` | Flow Label = 0. | tested(`udp46`) | `v6[1..3]` low 20 bits are zero. |
| `7915-4.1-payloadlen` | Payload Length = IPv4 Total Length − IPv4 header (incl. options) length. | tested(`udp46`, `edge`) | `payload_len = total_len − ihl`; `l4` is bound to Total Length so trailing L2 padding never leaks. `edge` covers options (IHL>5). |
| `7915-4.1-nexthdr` | Next Header = IPv4 Protocol, except ICMPv4 (1) → ICMPv6 (58). | tested(`udp46`, `tcp46`, `icmp46`) | `new_nh = 58 if proto == ICMP else proto`. |
| `7915-4.1-hoplimit` | Hop Limit derived from TTL; MUST decrement; if it reaches zero, drop and (per §4.4) return ICMPv4 Time Exceeded. | tested(`negative`) + refused(ICMP-error generation) | **Frozen decision:** `hop = TTL − 1`; **TTL ≤ 1 → DROP** (`ttl_expired`). We do NOT originate the Time Exceeded — packet origination is refused (see [`7915-4.4`](#44-generation-of-icmpv4-errors)). Normal decrement covered by every `udp46`/`tcp46`/`icmp46` output; the drop by `negative`. |
| `7915-4.1-src` | Source Address mapped to IPv6 via the addressing algorithm (§6). | tested(`udp46`, `edge`) | EAMT-first, else RFC 6052 embed — see [§6](#6-addressing-rfc-7915-6-rfc-6052-2-rfc-7757). `udp46` covers 6052 embed; `edge` covers EAMT hits. |
| `7915-4.1-dst` | Destination Address mapped to IPv6 via §6. | tested(`udp46`, `edge`) | Same as `7915-4.1-src`. |
| `7915-4.1-src-illegal` | Silently discard packets whose source is illegal (0.0.0.0, 127/8, etc.). | deferred(source-address sanity filtering) | Trigger: security-hardening pass (RFC 6052 §3.1 martian checks). Today RFC 6052 embed succeeds for any 32-bit source, so martians would translate. Named, not silently ignored. |
| `7915-4.1-options` | IPv4 options MUST be ignored; the packet is translated normally. | tested(`edge`) | Payload located via IHL·4; options are not carried into IPv6 (IPv6 has none). This is the "options handled, not deferred" decision — packets with options are still translated. |
| `7915-4.1-source-route` | An unexpired Source Route option → discard + ICMPv4 (Type 3 Code 5). | deferred(source-route inspection) + refused(ICMP-error generation) | Options are ignored wholesale (`7915-4.1-options`); we do not inspect for source-route, and we never originate the ICMP error. Trigger: security-hardening. |
| `7915-4.1-no-fraghdr` | For a non-fragmented IPv4 packet with DF=0, the translator MUST NOT include an IPv6 Fragment Header. | tested(`udp46`) | We emit a bare 40 B IPv6 header, no extension headers, ever. Structurally satisfied and asserted byte-exact. |
| `7915-4.1-frag` | An IPv4 fragment (MF set or non-zero offset) is translated by adding an IPv6 Fragment Header (Identification low-16 from IPv4 ID / hi-16 zero, Offset and M copied). | deferred(fragmentation) + tested(`negative`) | **Frozen decision:** any fragment → `fragment` drop, evaluated *before* L4 truncation/checksum (a non-initial fragment carries no L4 header). Trigger: fragmentation arc. |
| `7915-4.1-df0-fragment` | If DF=0 and the result would exceed `lowest-ipv6-mtu` (default 1280), SHOULD fragment. | deferred(fragmentation) | No emitter-side fragmentation in scope. |
| `7915-4.1-mtu-config` | MUST offer a config knob to raise the minimum-IPv6-MTU threshold above 1280. | deferred(fragmentation) | Config-provision tied to the fragmentation arc. |
| `7915-4.1-df1-frag-needed` | If DF=1 and next-hop MTU < Total Length + 20, MUST send ICMPv4 Fragmentation Needed. | deferred(fragmentation) + refused(ICMP-error generation) | No PMTU state, no packet origination. Trigger: fragmentation arc. |

### 4.2. ICMPv4-to-ICMPv6

First landing translates **echo request/reply only**. ICMP *error* translation
(the embedded "packet in error") is a named deferral.

| ID | Clause (RFC 7915 §4.2) | Disposition | Rationale / vector |
|---|---|---|---|
| `7915-4.2-checksum` | ICMPv6 checksum MUST be computed to include the ICMPv6 pseudo-header (ICMPv4 has none). | tested(`icmp46`) | `_icmp6_pseudo(src6, dst6, upper_len)`; RFC 1624 patch folds in the whole v6 pseudo-header in one step (old word has no pseudo-header term). |
| `7915-4.2-echo-req` | Echo Request Type 8 → 128; adjust checksum. | tested(`icmp46`) | `ICMP4_ECHO_REQUEST → ICMP6_ECHO_REQUEST`. |
| `7915-4.2-echo-reply` | Echo Reply Type 0 → 129; adjust checksum. | tested(`icmp46`) | `ICMP4_ECHO_REPLY → ICMP6_ECHO_REPLY`. |
| `7915-4.2-nonecho-drop` | Obsoleted/single-hop/unknown query types (Info Req/Reply 15/16, Timestamp 13/14, Addr Mask 17/18, Router Adv/Sol 9/10, unknown) are silently dropped. | tested(`negative`) | Non-echo ICMP (or code≠0) → `icmp_error` drop. Our drop matches the RFC's silent drop; error *translation* is separately deferred. |
| `7915-4.2-igmp-drop` | IGMP messages SHOULD be silently dropped. | tested(`negative`) | IGMP is IP protocol 2, not ICMP → `unsupported_l4` drop. Aligned with the silent-drop intent. |
| `7915-4.2-err-dest-unreach` | ICMPv4 Destination Unreachable (Type 3) → ICMPv6 (Type 1/2/4) with per-code mapping. | deferred(ICMP-error translation) + tested(`negative`) | Error translation deferred (trigger: Jool `b*` fixtures / traceroute-through-Nanuk). Today the error is dropped (`icmp_error`). |
| `7915-4.2-err-frag-needed` | Dest Unreachable Code 4 (Frag Needed) → ICMPv6 Packet Too Big with MTU adjustment (max/min formula). | deferred(ICMP-error translation) | Depends on both error translation and PMTU/MTU handling. |
| `7915-4.2-err-time-exceeded` | Time Exceeded (Type 11) → ICMPv6 Type 3, code preserved. | deferred(ICMP-error translation) | Dropped today (`icmp_error`). |
| `7915-4.2-err-param-problem` | Parameter Problem (Type 12) → ICMPv6 Type 4 with pointer remap; some codes silently dropped. | deferred(ICMP-error translation) | Dropped today. |
| `7915-4.2-err-redirect-quench` | Redirect (5), Alt Host (6), Source Quench (4) silently dropped. | tested(`negative`) | Non-echo → `icmp_error`. Matches silent-drop. |
| `7915-4.2-icmp-extensions` | ICMPv4 extension length attribute MUST be adjusted; truncate if it overflows the outgoing MTU. | deferred(ICMP-error translation) | Part of the error-message body handling. |

### 4.3. ICMPv4 error messages

| ID | Clause (RFC 7915 §4.3) | Disposition | Rationale / vector |
|---|---|---|---|
| `7915-4.3-inner-xlate` | The embedded "packet in error" MUST be translated like a normal IP packet, except inner TTL/Hop Limit is not decremented. | deferred(ICMP-error translation) | Trigger: Jool `b*` fixtures / traceroute story. Inner packet lies within the 256 B window, so it is feasible when wanted. |
| `7915-4.3-outer-len` | If inner translation changes length, the outer IPv6 Total/Payload Length MUST be updated. | deferred(ICMP-error translation) | Same deferral. |
| `7915-4.3-stop-first-embedded` | Processing MUST stop at the first embedded header; drop if more embedded headers. | deferred(ICMP-error translation) | Same deferral. |

### 4.4. Generation of ICMPv4 errors

| ID | Clause (RFC 7915 §4.4) | Disposition | Rationale / vector |
|---|---|---|---|
| `7915-4.4-generate` | If an IPv4 packet is discarded, the translator SHOULD send back an ICMPv4 error (Type 3 Code 13 by default) to the original sender, unless the discarded packet was itself an ICMPv4 error; SHOULD allow rate-limit/off config. | **refused** (ICMP-error generation) | Nanuk is a rewrite-only dataplane: it never *originates* a fresh packet. All discards are silent drops recorded in the totality ledger. This is why TTL≤1 is a plain drop, not a Time-Exceeded emission. A first-class architectural boundary, distinct from the (deferred) *translation* of an existing ICMP error. |

### 4.5. Transport-layer headers

| ID | Clause (RFC 7915 §4.5) | Disposition | Rationale / vector |
|---|---|---|---|
| `7915-4.5-csum-update` | If the address mapping is not checksum-neutral, TCP/UDP/ICMP pseudo-header checksums MUST be recalculated. Translators MUST do this for TCP, ICMP, and UDP-with-checksum. | tested(`udp46`, `tcp46`, `icmp46`) | RFC 1624 incremental patch `HC' = ~(~HC + ~m + m')` over the address words (pseudo-header length+proto are equal on both sides for UDP/TCP). `_patch(...)`. |
| `7915-4.5-udp-zero-csum` | For UDP with a zero checksum, the translator SHOULD offer: (1) drop + management event, or (2) compute the IPv6 checksum and forward. | tested(`negative`) | **Frozen decision:** IPv4 UDP checksum 0 → **DROP** (`zero_udp_checksum`). Rationale: computing the mandatory IPv6 UDP checksum needs the full payload, which can exceed the 256 B window — totality-as-guard. This is a documented Jool divergence if their fixtures assume the forwarding config. |
| `7915-4.5-udp-zero-frag` | A stateless translator cannot compute the checksum of a fragmented zero-checksum UDP packet; SHOULD drop + management event. | tested(`negative`) + deferred(fragmentation) | We drop *all* zero-checksum UDP (stricter than, and consistent with, this SHOULD). Fragmented case also caught by the `fragment` drop upstream. |
| `7915-4.5-udp-zero-transmit` | (RFC 768 / RFC 8200) An IPv6 UDP checksum that computes to 0x0000 MUST be transmitted as 0xFFFF; likewise ICMPv6 (RFC 4443). | tested(`udp46`, `icmp46`) | **Frozen decision:** `if new_csum == 0: new_csum = 0xFFFF` on both the UDP and ICMP v4→v6 paths. `edge`/`icmp46` include an input engineered to fold to zero. |
| `7915-4.5-other-transports` | Other transport protocols (e.g. DCCP) are OPTIONAL to support. | not-a-requirement | Optional. Non-UDP/TCP/ICMP-echo → `unsupported_l4` drop by decision. |
| `7915-4.5-forward-all` | To ease debugging, translators MUST forward all transport protocols. | **refused** (rewrite-only totality) + tested(`negative`) | **Documented divergence.** Nanuk translates only UDP, TCP, and ICMP echo; any other L4 → `unsupported_l4` DROP (totality doctrine — every packet gets an explicit verdict). Blindly forwarding an unknown L4 whose pseudo-header checksum we cannot recompute would emit a corrupt frame, so the safe verdict is drop. Recorded here as a deliberate deviation from the §4.5 MUST-forward. |

### 4.6. Knowing when to translate

| ID | Clause (RFC 7915 §4.6) | Disposition | Rationale / vector |
|---|---|---|---|
| `7915-4.6-route-priority` | If the translator also forwards and the destination is reachable by a more-specific non-translated route, it MUST forward without translating. | not-a-requirement | Routing/deployment decision. In Nanuk the switch fabric decides what enters the `siit` program; the translator program itself is invoked only on packets destined for translation. Out of the translator's contract. |
| `7915-4.6-flow-order` | SHOULD keep same-flow packets in arrival order. | not-a-requirement | Single in-order pipeline; no reordering surface exists to test. |

---

## 5. IPv6-to-IPv4 (RFC 7915 §5)

Head shrinks 20 B net: a fresh 20 B IPv4 header (including a freshly computed
header checksum) replaces the 40 B IPv6 header. Same **outer-in** ledger order
as §4, minus the v4-header-checksum step (no v6 analogue).

### 5.1. IPv6-to-IPv4 header

| ID | Clause (RFC 7915 §5.1) | Disposition | Rationale / vector |
|---|---|---|---|
| `7915-5.1-version` | Version = 4. | tested(`udp64`) | `v4[0] = 0x45`. |
| `7915-5.1-ihl` | IHL = 5; no IPv4 options are generated. | tested(`udp64`) | **Frozen decision:** never emit options — `v4[0] = 0x45` always. |
| `7915-5.1-tos` | TOS = copied from IPv6 Traffic Class (all 8 bits). | tested(`udp64`, `edge`) | `tc = (tc-hi << 4) \| (tc-lo)`. `edge` varies non-zero TC. |
| `7915-5.1-totallen` | Total Length = IPv6 Payload Length + 20. | tested(`udp64`) | `total_len = payload_len + 20`. |
| `7915-5.1-identification` | Identification set by a fragment-ID generator at the translator. | tested(`udp64`) | **Frozen decision:** `Identification = 0` (deterministic; RFC 7915-sanctioned post-RFC 8021 policy, since we never fragment). `struct.pack_into("!H", v4, 6, 0x4000)`. |
| `7915-5.1-df` | DF = 0 if the translated packet ≤ 1260 B, else 1. MF = 0, Fragment Offset = 0. | tested(`udp64`) — **documented divergence** | **Frozen decision:** always **DF=1** (with ID=0, MF=0, offset=0). Rationale: ID=0 is only safe under DF=1 — a small packet emitted with DF=0 and ID=0 that a downstream router fragments would misreassemble. RFC 8021 deprecates atomic fragments; a stateless translator that never fragments is safe and fully deterministic with DF=1. Diverges from §5.1's size-conditional DF for packets ≤ 1260 B; a candidate Jool divergence, recorded. |
| `7915-5.1-ttl` | TTL derived from Hop Limit; MUST decrement; if zero, drop and (per §5.4) return ICMPv6 Time Exceeded. | tested(`negative`) + refused(ICMP-error generation) | **Frozen decision:** `TTL = hop − 1`; **Hop Limit ≤ 1 → DROP** (`ttl_expired`). No error origination (see [`7915-5.4`](#54-generation-of-icmpv6-errors)). |
| `7915-5.1-protocol` | Protocol = IPv6 Next Header, except ICMPv6 (58) → ICMPv4 (1). | tested(`udp64`, `tcp64`, `icmp64`) | `new_proto = 1 if nh == ICMPV6 else nh`. |
| `7915-5.1-checksum` | IPv4 header checksum computed fresh. | tested(`udp64`) | **Frozen decision:** computed via ones-complement fold (the `CSUM` instruction in-program) — `struct.pack_into("!H", v4, 10, (~_sum16(v4)) & 0xFFFF)`. |
| `7915-5.1-src` | Source Address mapped to IPv4 via §6. | tested(`udp64`, `edge`) | 6052-extract if the address carries pool6, else EAMT `t2` lookup — see [§6](#6-addressing-rfc-7915-6-rfc-6052-2-rfc-7757). |
| `7915-5.1-dst` | Destination Address mapped to IPv4 via §6. | tested(`udp64`, `edge`) | Same. Miss on both extract and EAMT → `untranslatable_address` drop. |
| `7915-5.1-untranslatable` | An address that cannot be mapped → drop (implied by §6). | tested(`negative`) | **Frozen decision:** neither pool6 prefix nor EAMT `t2` hit → `untranslatable_address` DROP. |
| `7915-5.1-exthdr` | Hop-by-Hop, Destination Options, and Routing (Segments Left = 0) headers MUST be ignored (skipped) during translation. | deferred(extension-header traversal) + tested(`negative`) | Trigger: fragmentation/ICMP-error arc (needs an ext-header chain walk in PP). Today a non-UDP/TCP/ICMPv6/Fragment next-header → `unsupported_l4` drop. |
| `7915-5.1-routing-nonzero` | A Routing header with non-zero Segments Left MUST NOT be translated; SHOULD return ICMPv6 Parameter Problem (Type 4 Code 0). | tested(`negative`) + refused(ICMP-error generation) | Routing header (NH 43) → `unsupported_l4` drop; no error origination. |
| `7915-5.1.1-fragment` | If a Fragment Header is present: derive Total Length/Identification/MF/Offset from it, clear DF; perform IPv4 fragmentation if the result exceeds the next-hop MTU. | deferred(fragmentation) + tested(`negative`) | **Frozen decision:** IPv6 Fragment Header (NH 44) → `fragment` drop, evaluated before L4 checks. Trigger: fragmentation arc. |

### 5.2. ICMPv6-to-ICMPv4

Echo request/reply only in the first landing.

| ID | Clause (RFC 7915 §5.2) | Disposition | Rationale / vector |
|---|---|---|---|
| `7915-5.2-checksum` | ICMPv4 checksum MUST be updated: ICMPv6 includes a pseudo-header in its checksum, ICMPv4 does not, so the pseudo-header contribution is removed. | tested(`icmp64`) | `_patch(old_csum, old_word + pseudo, new_word)` removes the v6 pseudo-header while remapping the type word. |
| `7915-5.2-echo-req` | Echo Request Type 128 → 8. | tested(`icmp64`) | `ICMP6_ECHO_REQUEST → ICMP4_ECHO_REQUEST`. |
| `7915-5.2-echo-reply` | Echo Reply Type 129 → 0. | tested(`icmp64`) | `ICMP6_ECHO_REPLY → ICMP4_ECHO_REPLY`. |
| `7915-5.2-mld-nd-drop` | MLD and Neighbor Discovery (single-hop) messages are silently dropped. | tested(`negative`) | Non-echo ICMPv6 (or code≠0) → `icmp_error` drop. |
| `7915-5.2-unknown-drop` | Unknown informational ICMPv6 types are silently dropped. | tested(`negative`) | Same `icmp_error` path. |
| `7915-5.2-err-dest-unreach` | Destination Unreachable (Type 1) → ICMPv4 Type 3 with per-code mapping. | deferred(ICMP-error translation) + tested(`negative`) | Error translation deferred; dropped today. |
| `7915-5.2-err-too-big` | Packet Too Big (Type 2) → ICMPv4 Dest Unreachable Type 3 Code 4, with MTU adjustment. | deferred(ICMP-error translation) | Ties into PMTU. |
| `7915-5.2-err-time-exceeded` | Time Exceeded (Type 3) → ICMPv4 Type 11, code preserved. | deferred(ICMP-error translation) | Dropped today. |
| `7915-5.2-err-param-problem` | Parameter Problem (Type 4) → ICMPv4 with pointer remap (Figure 6). | deferred(ICMP-error translation) | Dropped today. |

### 5.3. ICMPv6 error messages

| ID | Clause (RFC 7915 §5.3) | Disposition | Rationale / vector |
|---|---|---|---|
| `7915-5.3-inner-xlate` | The embedded packet in error MUST be translated like a normal IP packet (inner Hop Limit not decremented); MUST stop at the first embedded header and drop if more. | deferred(ICMP-error translation) | Trigger: Jool `b*` fixtures / traceroute. Inner packet fits the 256 B window. |

### 5.4. Generation of ICMPv6 errors

| ID | Clause (RFC 7915 §5.4) | Disposition | Rationale / vector |
|---|---|---|---|
| `7915-5.4-generate` | If an IPv6 packet is discarded, the translator SHOULD send back an ICMPv6 error (Type 1 Code 1 default), unless the discarded packet is itself an ICMPv6 message; config for rate-limit/off. | **refused** (ICMP-error generation) | Same rewrite-only boundary as [`7915-4.4`](#44-generation-of-icmpv4-errors). Discards are silent. |

### 5.5. Transport-layer headers

| ID | Clause (RFC 7915 §5.5) | Disposition | Rationale / vector |
|---|---|---|---|
| `7915-5.5-csum-update` | TCP/UDP/ICMP pseudo-header checksums MUST be recalculated when the mapping is not checksum-neutral (MUST for TCP, ICMP, UDP-with-checksum). | tested(`udp64`, `tcp64`, `icmp64`) | RFC 1624 patch over the address words; `_patch(old_csum, src6+dst6, src4+dst4)`. |
| `7915-5.5-udp-zero-passthrough` | A resulting IPv4 UDP checksum of zero is legal (unlike v4→v6, where zero-checksum ingress is dropped). | tested(`udp64`) | Passed through patched, no special case — `# v4 UDP checksum 0 is legal`. Deterministic. |
| `7915-5.5-forward-all` | MUST forward all transport protocols. | **refused** (rewrite-only totality) + tested(`negative`) | Same documented divergence as [`7915-4.5-forward-all`](#45-transport-layer-headers). Unsupported L4 → `unsupported_l4` drop. |
| `7915-5.5-other-transports` | Other transports OPTIONAL. | not-a-requirement | Optional; unsupported → drop. |

### 5.6. Knowing when to translate

| ID | Clause (RFC 7915 §5.6) | Disposition | Rationale / vector |
|---|---|---|---|
| `7915-5.6-route-priority` | If a more-specific non-translated route exists, MUST forward without translating. | not-a-requirement | Deployment/routing; the switch decides program entry — mirror of [`7915-4.6-route-priority`](#46-knowing-when-to-translate). |

---

## 6. Addressing (RFC 7915 §6, RFC 6052 §2, RFC 7757)

RFC 7915 §6 delegates address mapping to RFC 6052 (algorithmic prefix) and
RFC 7757 (EAMT). Nanuk's precedence (RFC 7757): **EAMT exact-match first, then
RFC 6052 pool6**.

| ID | Clause | Disposition | Rationale / vector |
|---|---|---|---|
| `6052-wkp` | Well-Known Prefix = `64:ff9b::/96` for algorithmic mapping. | tested(`udp46`, `udp64`) | `WKP = 0064:ff9b:…/96`, baked as program constants and `SiitConfig.pool6` default. |
| `6052-embed` | Embed the 32-bit IPv4 address per prefix length; for /96 it occupies bits 96–127. | tested(`udp46`) | `_addr46`: `pool6 + v4` (12 B prefix + 4 B v4). v4→v6 for non-EAMT sources/dests. |
| `6052-extract` | Extract the IPv4 address from the fixed position for the configured prefix length. | tested(`udp64`) | `_addr64`: if `v6[:12] == pool6` → `v6[12:16]`. |
| `6052-ubits` | Bits 64–71 (the "u" octet) MUST be zero for prefixes shorter than /96. | not-a-requirement (for /96) | With a /96 prefix the IPv4 address sits in bits 96–127 and bits 64–71 are inside the zero prefix, so the constraint is vacuously met. Non-/96 prefix lengths are deferred (`6052-prefix-lengths`). |
| `6052-prefix-lengths` | Six legal prefix lengths (32/40/48/56/64/96). | deferred(configurable prefix length) | Trigger: multi-prefix deployment. Only /96 in scope (WKP). The hi/lo table split (`t0`/`t1`/`t2`) is kept regardless for RTL cost honesty. |
| `7757-eamt` | Explicit Address Mapping Table: exact per-address v4↔v6 overrides, taking precedence over the algorithmic prefix. | tested(`edge`) | `t0`/`t1` (v4→v6 hi/lo 64) and `t2` (v6→v4). `DEMO_SIIT` maps `192.0.2.1 ↔ 2001:db8:1::c001`. EAMT checked before 6052 on ingress. |
| `7757-eamt-low64` | (Nanuk demo constraint) EAMT v6→v4 keys are the **low 64 bits** of the IPv6 address; entries MUST be distinct in their low 64 bits. | tested(`edge`) | **Frozen decision:** LOOKUP keys are ≤64-bit, forcing the hi/lo split; `t2` key = v6 low 64 bits. True of any sane EAMT; full 128-bit generality is the LPM/T3 trigger. |
| `7757-eamt-general-prefix` | General **prefix** EAMT (RFC 7757 allows prefix mappings, not only host mappings). | deferred(LPM/T3) | Exact-match only in the first landing; prefix EAMT waits for LPM tables (see `coverage.md` T3). |

---

## 7. Security and references (RFC 7915 §7–§11)

| ID | Clause | Disposition | Rationale / vector |
|---|---|---|---|
| `7915-7-security` | §7 Security Considerations: filtering guidance, checksum-neutral mapping notes, spoofing/martian concerns, and the note that stateless translation carries no per-flow security state. | not-a-requirement (advisory) | No packet-observable translation obligation. The one actionable item — martian/illegal source filtering — is tracked separately as [`7915-4.1-src-illegal`](#41-ipv4-to-ipv6-header) (deferred, hardening trigger). |
| `7915-8-iana` | §8 IANA Considerations. | not-a-requirement | No dataplane behavior. |
| `7915-9to11-refs` | §9–§11 References, Acknowledgements, Authors. | not-a-requirement | Non-normative. |

---

## Frozen decisions ledger

Every plan-level frozen decision has an explicit disposition above. Collected
here for the reviewer, each with its ID:

| Frozen decision | Disposition ID(s) | Category |
|---|---|---|
| `Identification = 0`, DF=1, MF=0, offset=0 on v6→v4 | `7915-5.1-identification`, `7915-5.1-df` | tested(`udp64`), documented divergence on DF |
| TTL/Hop Limit ≤ 1 → DROP (no ICMP error) | `7915-4.1-hoplimit`, `7915-5.1-ttl` | tested(`negative`) + refused(ICMP-error generation) |
| IPv4 UDP checksum 0 → DROP | `7915-4.5-udp-zero-csum` | tested(`negative`) |
| Never emit IPv4 options (IHL=5) | `7915-5.1-ihl` | tested(`udp64`) |
| EAMT keyed on v6 low 64 bits; entries distinct in low 64 | `7757-eamt-low64` | tested(`edge`) |
| Outer-in ingress ledger order | `7915-ledger-order` (below) | not-a-requirement (Nanuk-sovereign) |
| Computed-zero UDP/ICMPv6 checksum → transmit 0xFFFF | `7915-4.5-udp-zero-transmit` | tested(`udp46`, `icmp46`) |
| Unsupported L4 → DROP (vs. §4.5/§5.5 MUST-forward) | `7915-4.5-forward-all`, `7915-5.5-forward-all` | refused + tested(`negative`) |
| Untranslatable address → DROP | `7915-5.1-untranslatable` | tested(`negative`) |

**`7915-ledger-order`** — *not-a-requirement (Nanuk-sovereign ordering).* RFC 7915
does not fix the order in which validation failures are detected; Nanuk does, so
that every drop reports a single deterministic reason. The order (identical in
both directions, defined authoritatively in `siit_ref.py`) is **outer-in**:
(a) IP structural drops — runt / IP header truncated / Total Length overruns the
frame (`runt`, `non_ip_ethertype`, `v4_truncated`/`v6_truncated`, `l4_truncated`);
(b) IPv4 header checksum (`v4_bad_header_checksum`; no v6 analogue); (c) fragment
(`fragment`); (d) L4 truncation (`l4_truncated`); (e) v4 zero-UDP-checksum
(`zero_udp_checksum`; no v6 analogue); (f) ICMP non-echo (`icmp_error`);
(g) unsupported L4 (`unsupported_l4`); (h) TTL/Hop ≤ 1 (`ttl_expired`); then
addressing (`untranslatable_address`). Fragment is checked before L4 truncation
and checksum because a non-initial fragment's bytes are not an L4 header at all.
Exercised across the `negative` group (one vector per reason) with the `edge`
group covering the addressing miss.

---

## Summary table

All dispositions, in document order. Tallies at the bottom. Task 3 vectors cite
the `ID` column.

| ID | Section | Disposition |
|---|---|---|
| `7915-1-stateless` | §1 | not-a-requirement |
| `7915-1-stateful` | §1 | refused |
| `7915-1-no-options-xlate` | §1 | tested(`edge`) |
| `7915-1-no-exthdr-xlate` | §1 | deferred(extension-header traversal) |
| `7915-1-multicast` | §1 | refused |
| `7915-1-frag-not-xlated` | §1 | deferred(fragmentation) + tested(`negative`) |
| `7915-4.1-version` | §4.1 | tested(`udp46`) |
| `7915-4.1-tos` | §4.1 | tested(`udp46`, `edge`) |
| `7915-4.1-tos-ignore` | §4.1 | not-a-requirement |
| `7915-4.1-flowlabel` | §4.1 | tested(`udp46`) |
| `7915-4.1-payloadlen` | §4.1 | tested(`udp46`, `edge`) |
| `7915-4.1-nexthdr` | §4.1 | tested(`udp46`, `tcp46`, `icmp46`) |
| `7915-4.1-hoplimit` | §4.1 | tested(`negative`) + refused(ICMP-error generation) |
| `7915-4.1-src` | §4.1 | tested(`udp46`, `edge`) |
| `7915-4.1-dst` | §4.1 | tested(`udp46`, `edge`) |
| `7915-4.1-src-illegal` | §4.1 | deferred(source-address sanity filtering) |
| `7915-4.1-options` | §4.1 | tested(`edge`) |
| `7915-4.1-source-route` | §4.1 | deferred(source-route inspection) + refused(ICMP-error generation) |
| `7915-4.1-no-fraghdr` | §4.1 | tested(`udp46`) |
| `7915-4.1-frag` | §4.1 | deferred(fragmentation) + tested(`negative`) |
| `7915-4.1-df0-fragment` | §4.1 | deferred(fragmentation) |
| `7915-4.1-mtu-config` | §4.1 | deferred(fragmentation) |
| `7915-4.1-df1-frag-needed` | §4.1 | deferred(fragmentation) + refused(ICMP-error generation) |
| `7915-4.2-checksum` | §4.2 | tested(`icmp46`) |
| `7915-4.2-echo-req` | §4.2 | tested(`icmp46`) |
| `7915-4.2-echo-reply` | §4.2 | tested(`icmp46`) |
| `7915-4.2-nonecho-drop` | §4.2 | tested(`negative`) |
| `7915-4.2-igmp-drop` | §4.2 | tested(`negative`) |
| `7915-4.2-err-dest-unreach` | §4.2 | deferred(ICMP-error translation) + tested(`negative`) |
| `7915-4.2-err-frag-needed` | §4.2 | deferred(ICMP-error translation) |
| `7915-4.2-err-time-exceeded` | §4.2 | deferred(ICMP-error translation) |
| `7915-4.2-err-param-problem` | §4.2 | deferred(ICMP-error translation) |
| `7915-4.2-err-redirect-quench` | §4.2 | tested(`negative`) |
| `7915-4.2-icmp-extensions` | §4.2 | deferred(ICMP-error translation) |
| `7915-4.3-inner-xlate` | §4.3 | deferred(ICMP-error translation) |
| `7915-4.3-outer-len` | §4.3 | deferred(ICMP-error translation) |
| `7915-4.3-stop-first-embedded` | §4.3 | deferred(ICMP-error translation) |
| `7915-4.4-generate` | §4.4 | refused(ICMP-error generation) |
| `7915-4.5-csum-update` | §4.5 | tested(`udp46`, `tcp46`, `icmp46`) |
| `7915-4.5-udp-zero-csum` | §4.5 | tested(`negative`) |
| `7915-4.5-udp-zero-frag` | §4.5 | tested(`negative`) + deferred(fragmentation) |
| `7915-4.5-udp-zero-transmit` | §4.5 | tested(`udp46`, `icmp46`) |
| `7915-4.5-other-transports` | §4.5 | not-a-requirement |
| `7915-4.5-forward-all` | §4.5 | refused(rewrite-only totality) + tested(`negative`) |
| `7915-4.6-route-priority` | §4.6 | not-a-requirement |
| `7915-4.6-flow-order` | §4.6 | not-a-requirement |
| `7915-5.1-version` | §5.1 | tested(`udp64`) |
| `7915-5.1-ihl` | §5.1 | tested(`udp64`) |
| `7915-5.1-tos` | §5.1 | tested(`udp64`, `edge`) |
| `7915-5.1-totallen` | §5.1 | tested(`udp64`) |
| `7915-5.1-identification` | §5.1 | tested(`udp64`) |
| `7915-5.1-df` | §5.1 | tested(`udp64`) — documented divergence |
| `7915-5.1-ttl` | §5.1 | tested(`negative`) + refused(ICMP-error generation) |
| `7915-5.1-protocol` | §5.1 | tested(`udp64`, `tcp64`, `icmp64`) |
| `7915-5.1-checksum` | §5.1 | tested(`udp64`) |
| `7915-5.1-src` | §5.1 | tested(`udp64`, `edge`) |
| `7915-5.1-dst` | §5.1 | tested(`udp64`, `edge`) |
| `7915-5.1-untranslatable` | §5.1 | tested(`negative`) |
| `7915-5.1-exthdr` | §5.1 | deferred(extension-header traversal) + tested(`negative`) |
| `7915-5.1-routing-nonzero` | §5.1 | tested(`negative`) + refused(ICMP-error generation) |
| `7915-5.1.1-fragment` | §5.1.1 | deferred(fragmentation) + tested(`negative`) |
| `7915-5.2-checksum` | §5.2 | tested(`icmp64`) |
| `7915-5.2-echo-req` | §5.2 | tested(`icmp64`) |
| `7915-5.2-echo-reply` | §5.2 | tested(`icmp64`) |
| `7915-5.2-mld-nd-drop` | §5.2 | tested(`negative`) |
| `7915-5.2-unknown-drop` | §5.2 | tested(`negative`) |
| `7915-5.2-err-dest-unreach` | §5.2 | deferred(ICMP-error translation) + tested(`negative`) |
| `7915-5.2-err-too-big` | §5.2 | deferred(ICMP-error translation) |
| `7915-5.2-err-time-exceeded` | §5.2 | deferred(ICMP-error translation) |
| `7915-5.2-err-param-problem` | §5.2 | deferred(ICMP-error translation) |
| `7915-5.3-inner-xlate` | §5.3 | deferred(ICMP-error translation) |
| `7915-5.4-generate` | §5.4 | refused(ICMP-error generation) |
| `7915-5.5-csum-update` | §5.5 | tested(`udp64`, `tcp64`, `icmp64`) |
| `7915-5.5-udp-zero-passthrough` | §5.5 | tested(`udp64`) |
| `7915-5.5-forward-all` | §5.5 | refused(rewrite-only totality) + tested(`negative`) |
| `7915-5.5-other-transports` | §5.5 | not-a-requirement |
| `7915-5.6-route-priority` | §5.6 | not-a-requirement |
| `6052-wkp` | §6 | tested(`udp46`, `udp64`) |
| `6052-embed` | §6 | tested(`udp46`) |
| `6052-extract` | §6 | tested(`udp64`) |
| `6052-ubits` | §6 | not-a-requirement (for /96) |
| `6052-prefix-lengths` | §6 | deferred(configurable prefix length) |
| `7757-eamt` | §6 | tested(`edge`) |
| `7757-eamt-low64` | §6 | tested(`edge`) |
| `7757-eamt-general-prefix` | §6 | deferred(LPM/T3) |
| `7915-7-security` | §7 | not-a-requirement |
| `7915-8-iana` | §8 | not-a-requirement |
| `7915-9to11-refs` | §9–11 | not-a-requirement |
| `7915-ledger-order` | (cross-cutting) | not-a-requirement (Nanuk-sovereign) |

**Tally — 89 dispositioned clauses.** By primary (first-listed) category:

- **tested:** 46. Group citations across the table (a clause may cite several):
  `udp46` 12, `udp64` 14, `tcp46` 2, `tcp64` 2, `icmp46` 6, `icmp64` 5,
  `edge` 11, `negative` 19.
- **deferred:** 25. By trigger (counting every clause a trigger touches, incl.
  compound rows): ICMP-error translation 13, fragmentation 7, extension-header
  traversal 2, source-address sanity filtering 1, source-route inspection 1,
  configurable prefix length 1, LPM/T3 1.
- **refused:** 6 — stateful NAT64, multicast, ICMP-error generation (§4.4/§5.4),
  rewrite-only forward-all (§4.5/§5.5). (ICMP-error *generation* is also a
  secondary refusal on 5 further drop clauses; rewrite-only totality likewise.)
- **not-a-requirement:** 12.

**14 clauses carry a compound disposition** — a translation rule that is
deferred in full yet whose current DROP behavior is exercised by the `negative`
group (e.g. ICMP-error and fragment clauses), or a tested rule that is also a
named refusal (forward-all). Of these, 8 are deferred/refused rules whose
present drop verdict is `negative`-tested.

**Coverage claim.** Every normative statement in RFC 7915 §1, §4, §5 and the
delegated addressing (RFC 6052 §2, RFC 7757) is dispositioned; §6–§11 are
covered by blanket rows. No clause is left as "TBD". Every deferral names a
trigger; every refusal names a rationale; every tested clause names at least one
of the eight vector groups.

**Divergences from RFC 7915, recorded (findings, not failures).** These are the
places where a Jool graybox replay (leg 4) may report a difference:

1. **Zero-checksum UDP dropped** (`7915-4.5-udp-zero-csum`) — RFC-sanctioned
   option; Jool's forwarding config may instead recompute.
2. **Always DF=1 with ID=0 on v6→v4** (`7915-5.1-df`) — deterministic, RFC 8021
   atomic-fragment avoidance; diverges from §5.1's size-conditional DF for
   packets ≤ 1260 B.
3. **Unsupported L4 dropped, not forwarded** (`7915-4.5-forward-all`,
   `7915-5.5-forward-all`) — totality doctrine over the §4.5/§5.5 MUST-forward.
4. **No ICMP-error generation** (`7915-4.4`, `7915-5.4`) and **no ICMP-error
   translation** (deferred) — a discarded packet is dropped silently.
