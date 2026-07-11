// Implementation parameters — defaults for the "full" configuration.
// A "nano" configuration for Tiny Tapeout shrinks these at stage 4.

let buf_bytes : int = 256    // packet header buffer size (bytes)
let imem_words : int = 1024  // instruction memory (32-bit words)
let nhdr : int = 16          // header present/offset slots
let smd_slots : int = 8      // SMD slots (16 bits each; 8 x 16 = 128)
let step_budget : int = 256  // watchdog: max instructions per packet
