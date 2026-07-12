# nanukproto — a protocol that doesn't exist

Beat 3 of the demo: Nanuk parses a protocol invented for this example. No
commercial switch has ever heard of nanukproto; Nanuk forwards it because
its parser program says so.

A minimal L2.5 tenant-tunnel header, riding directly on Ethernet with
EtherType `0x88B5` (IEEE 802 local-experimental — reserved for exactly
this kind of use):

| field            | bits | value / meaning                          |
|------------------|-----:|------------------------------------------|
| magic            | 16   | `0x4E4B` ("NK"); mismatch ⇒ drop          |
| version          | 4    | `1`; anything else ⇒ drop                 |
| flags            | 4    | reserved, ignored in v1                   |
| tenant_id        | 24   | recorded to SMD slots 5–6 (MSB-first)     |
| inner_ethertype  | 16   | dispatched like an outer EtherType        |

8 bytes total; the inner packet (e.g. IPv4/UDP, or even a VLAN tag) follows
and is parsed by the same states that parse untunneled traffic.

`parse.py` builds the parser program with the Nanuk eDSL by extending the
standard L2/L3/L4 program with one extra dispatch arm and three tunnel
states. `lang/tests/test_nanukproto.py` proves it on the golden model:
tunneled packets parse with correct inner offsets and tenant extraction,
bad magic/version drop, and untunneled traffic is untouched.
