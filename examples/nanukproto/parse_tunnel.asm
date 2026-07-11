; nanukproto tunnel-mode parser (PP side of the M1 demo-3 pair).
;
; Full L2-in-L2 encap framing: outer Ethernet (EtherType 0x88B5) | nanukproto
; header (8B, inner_ethertype 0x6558 = transparent Ethernet bridging) |
; original frame. This differs from parse.py's header-stack framing (nk
; between Ethernet and L3): head-only push/pop in the MAP requires the full
; encap - see the MAP extension design doc.
;
; Contract with the MAP pop program (examples/nanukproto/tunnel_pop.asm):
;   SMD slot 5 = nk magic (0x4E4B) when a valid tunnel header was parsed,
;   0 otherwise. Bad magic or version drops here in the PP.
;   h_nk (5) marks the nanukproto header for provenance.

.equ h_eth 0
.equ h_nk 5

start:
    sethdr  h_eth
    ext     r0, 96, 16         ; EtherType
    advi    14
    movi    r1, 0x88B5
    bne     r0, r1, plain
    sethdr  h_nk
    ext     r0, 0, 16          ; magic
    movi    r1, 0x4E4B
    bne     r0, r1, bad
    ext     r2, 16, 4          ; version
    movi    r1, 1
    bne     r2, r1, bad
    stmd    5, r0, 1           ; SMD slot 5 = magic: tunnel flag for the MAP
    ext     r2, 48, 16         ; inner ethertype
    stmd    6, r2, 1           ; SMD slot 6 (0x6558 = inner is a full frame)
    advi    8
    halt    accept
plain:
    halt    accept
bad:
    halt    drop
