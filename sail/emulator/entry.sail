// Dummy Sail entry point for the emulator build. The generated model_main
// references zmain even under --c-no-main; the C shim never calls it —
// all driving goes through the emu_* API.

val main : unit -> unit
function main() = ()
