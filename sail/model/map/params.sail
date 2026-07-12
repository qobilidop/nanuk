// Implementation parameters — defaults for the "full" configuration.
// The MAP is the parser's sibling engine; sizes deliberately match where
// the jobs overlap (imem, step budget) and differ where they don't
// (headroom, ports, tables).

let headroom_bytes : int = 32    // writable prepend region before the frame
let buf_bytes : int = 256        // frame window size (bytes), as the PP saw it
let win_bytes : int = 288        // headroom_bytes + buf_bytes
let imem_words : int = 1024      // instruction memory (32-bit words)
let n_ports : int = 4            // egress bitmap width
let n_tables : int = 4           // exact-match table slots
let table_max_entries : int = 64 // entries per table
let smd_in_slots : int = 8       // inbound SMD slots (PP pass-through)
let nhdr : int = 16              // header base slots (15 = h_frame, reserved)
let step_budget : int = 256      // watchdog: max instructions per packet
