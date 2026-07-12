// Walking-skeleton smoke test: proves the Sail -> C -> executable pipeline.

val main : unit -> unit
function main() = {
    let x : bits16 = 0x0102;
    assert(x[15 .. 8] == 0x01, "high byte of 0x0102 should be 0x01");
    assert(unsigned(x) == 258, "0x0102 should be 258");
    print_endline("test_smoke: OK")
}
