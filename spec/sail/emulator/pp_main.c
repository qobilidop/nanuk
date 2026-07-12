// nanuk-pp-emu: CLI driver for the Sail-generated golden model.
//
//   nanuk-pp-emu <prog.bin> <packet.bin>
//
// prog.bin:   big-endian 32-bit instruction words, loaded at word 0
// packet.bin: raw packet bytes
//
// Prints the run result as one JSON object on stdout (the output contract
// consumed by the Python harness) and exits 0 on any completed run —
// error verdicts are valid results, not process failures.

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

// Sail runtime/model entry points (linked from the generated model C).
typedef int sail_unit;
#define SAIL_UNIT 0

extern void model_init(void);
extern void model_fini(void);

extern sail_unit zemu_reset(sail_unit);
extern sail_unit zemu_poke_imem(uint64_t idx, uint64_t word);
extern sail_unit zemu_poke_pkt(uint64_t idx, uint64_t byte);
extern sail_unit zemu_set_plen(uint64_t len);
extern sail_unit zemu_run(sail_unit);
extern uint64_t zemu_get_verdict(sail_unit);
extern uint64_t zemu_get_error(sail_unit);
extern uint64_t zemu_get_cursor(sail_unit);
extern uint64_t zemu_get_steps(sail_unit);
extern uint64_t zemu_get_hdr_present(uint64_t h);
extern uint64_t zemu_get_hdr_offset(uint64_t h);
extern uint64_t zemu_get_smd(uint64_t s);

#define IMEM_WORDS 1024
#define BUF_BYTES 256
#define NHDR 16
#define SMD_SLOTS 8

static unsigned char *read_file(const char *path, size_t *size_out) {
    FILE *f = fopen(path, "rb");
    if (!f) {
        fprintf(stderr, "nanuk-pp-emu: cannot open %s\n", path);
        exit(2);
    }
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (size < 0) {
        fprintf(stderr, "nanuk-pp-emu: cannot stat %s\n", path);
        exit(2);
    }
    unsigned char *buf = malloc(size > 0 ? (size_t)size : 1);
    if (size > 0 && fread(buf, 1, (size_t)size, f) != (size_t)size) {
        fprintf(stderr, "nanuk-pp-emu: short read on %s\n", path);
        exit(2);
    }
    fclose(f);
    *size_out = (size_t)size;
    return buf;
}

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: nanuk-pp-emu <prog.bin> <packet.bin>\n");
        return 2;
    }

    size_t prog_size, pkt_size;
    unsigned char *prog = read_file(argv[1], &prog_size);
    unsigned char *pkt = read_file(argv[2], &pkt_size);

    if (prog_size % 4 != 0) {
        fprintf(stderr, "nanuk-pp-emu: program size %zu is not a multiple of 4\n", prog_size);
        return 2;
    }
    if (prog_size / 4 > IMEM_WORDS) {
        fprintf(stderr, "nanuk-pp-emu: program exceeds %d words\n", IMEM_WORDS);
        return 2;
    }

    model_init();
    zemu_reset(SAIL_UNIT);

    for (size_t i = 0; i < prog_size / 4; i++) {
        uint64_t word = ((uint64_t)prog[4 * i] << 24) | ((uint64_t)prog[4 * i + 1] << 16) |
                        ((uint64_t)prog[4 * i + 2] << 8) | (uint64_t)prog[4 * i + 3];
        zemu_poke_imem(i, word);
    }

    size_t poke_bytes = pkt_size < BUF_BYTES ? pkt_size : BUF_BYTES;
    for (size_t i = 0; i < poke_bytes; i++) {
        zemu_poke_pkt(i, pkt[i]);
    }
    uint64_t plen = pkt_size < 0xFFFF ? (uint64_t)pkt_size : 0xFFFF;
    zemu_set_plen(plen);

    zemu_run(SAIL_UNIT);

    printf("{\"verdict\": %llu, \"error\": %llu, \"payload_offset\": %llu, \"steps\": %llu",
           (unsigned long long)zemu_get_verdict(SAIL_UNIT),
           (unsigned long long)zemu_get_error(SAIL_UNIT),
           (unsigned long long)zemu_get_cursor(SAIL_UNIT),
           (unsigned long long)zemu_get_steps(SAIL_UNIT));
    printf(", \"hdr_present\": [");
    for (int h = 0; h < NHDR; h++) {
        printf("%s%llu", h ? "," : "", (unsigned long long)zemu_get_hdr_present((uint64_t)h));
    }
    printf("], \"hdr_offset\": [");
    for (int h = 0; h < NHDR; h++) {
        printf("%s%llu", h ? "," : "", (unsigned long long)zemu_get_hdr_offset((uint64_t)h));
    }
    printf("], \"smd\": [");
    for (int s = 0; s < SMD_SLOTS; s++) {
        printf("%s%llu", s ? "," : "", (unsigned long long)zemu_get_smd((uint64_t)s));
    }
    printf("]}\n");

    model_fini();
    free(prog);
    free(pkt);
    return 0;
}
