// nanuk-map-emu: CLI driver for the Sail-generated MAP golden model.
//
//   nanuk-map-emu <prog.bin> <packet.bin> <ctx.txt>
//
// prog.bin:   big-endian 32-bit instruction words, loaded at word 0
// packet.bin: raw frame bytes (as the PP saw them)
// ctx.txt:    inbound contract + tables, one record per line:
//               ingress <port>
//               smd <slot> <value>
//               hdr <id> <present> <offset>
//               table <id> <key_width> <action_width>
//               entry <table_id> <key> <action>
//             (plen comes from packet.bin's size; unknown keywords are
//             errors; values are decimal or 0x-hex via strtoull base 0)
//
// Prints the run result as one JSON object on stdout (the output contract
// consumed by the Python harness) and exits 0 on any completed run —
// error verdicts are valid results, not process failures. The transmitted
// frame (verdict 0) is emitted as lowercase hex in "frame".

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// Sail runtime/model entry points (linked from the generated model C).
typedef int sail_unit;
#define SAIL_UNIT 0

extern void model_init(void);
extern void model_fini(void);

extern sail_unit zemu_map_reset(sail_unit);
extern sail_unit zemu_map_poke_imem(uint64_t idx, uint64_t word);
extern sail_unit zemu_map_poke_pkt(uint64_t idx, uint64_t byte);
extern sail_unit zemu_map_set_plen(uint64_t len);
extern sail_unit zemu_map_set_ingress(uint64_t port);
extern sail_unit zemu_map_set_smd(uint64_t slot, uint64_t value);
extern sail_unit zemu_map_set_hdr(uint64_t h, uint64_t present, uint64_t off);
extern sail_unit zemu_map_table_config(uint64_t t, uint64_t kw, uint64_t aw);
extern sail_unit zemu_map_table_add(uint64_t t, uint64_t key, uint64_t action);
extern sail_unit zemu_map_run(sail_unit);
extern uint64_t zemu_map_get_verdict(sail_unit);
extern uint64_t zemu_map_get_error(sail_unit);
extern uint64_t zemu_map_get_egress(sail_unit);
extern uint64_t zemu_map_get_steps(sail_unit);
extern uint64_t zemu_map_get_delta(sail_unit);
extern uint64_t zemu_map_get_win_byte(uint64_t idx);

#define IMEM_WORDS 1024
#define BUF_BYTES 256
#define HEADROOM_BYTES 32

static unsigned char *read_file(const char *path, size_t *size_out) {
    FILE *f = fopen(path, "rb");
    if (!f) {
        fprintf(stderr, "nanuk-map-emu: cannot open %s\n", path);
        exit(2);
    }
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (size < 0) {
        fprintf(stderr, "nanuk-map-emu: cannot stat %s\n", path);
        exit(2);
    }
    unsigned char *buf = malloc(size > 0 ? (size_t)size : 1);
    if (size > 0 && fread(buf, 1, (size_t)size, f) != (size_t)size) {
        fprintf(stderr, "nanuk-map-emu: short read on %s\n", path);
        exit(2);
    }
    fclose(f);
    *size_out = (size_t)size;
    return buf;
}

static uint64_t parse_u64(const char *tok, const char *what, int lineno) {
    if (!tok) {
        fprintf(stderr, "nanuk-map-emu: ctx line %d: missing %s\n", lineno, what);
        exit(2);
    }
    return strtoull(tok, NULL, 0);
}

static void load_ctx(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) {
        fprintf(stderr, "nanuk-map-emu: cannot open %s\n", path);
        exit(2);
    }
    char line[256];
    int lineno = 0;
    while (fgets(line, sizeof line, f)) {
        lineno++;
        // Strip comments; skip blank lines.
        char *hash = strchr(line, '#');
        if (hash) *hash = '\0';
        char *kw = strtok(line, " \t\r\n");
        if (!kw) continue;
        if (strcmp(kw, "ingress") == 0) {
            zemu_map_set_ingress(parse_u64(strtok(NULL, " \t\r\n"), "port", lineno));
        } else if (strcmp(kw, "smd") == 0) {
            uint64_t slot = parse_u64(strtok(NULL, " \t\r\n"), "slot", lineno);
            uint64_t val = parse_u64(strtok(NULL, " \t\r\n"), "value", lineno);
            zemu_map_set_smd(slot, val);
        } else if (strcmp(kw, "hdr") == 0) {
            uint64_t id = parse_u64(strtok(NULL, " \t\r\n"), "id", lineno);
            uint64_t present = parse_u64(strtok(NULL, " \t\r\n"), "present", lineno);
            uint64_t off = parse_u64(strtok(NULL, " \t\r\n"), "offset", lineno);
            zemu_map_set_hdr(id, present, off);
        } else if (strcmp(kw, "table") == 0) {
            uint64_t id = parse_u64(strtok(NULL, " \t\r\n"), "id", lineno);
            uint64_t kwid = parse_u64(strtok(NULL, " \t\r\n"), "key_width", lineno);
            uint64_t awid = parse_u64(strtok(NULL, " \t\r\n"), "action_width", lineno);
            zemu_map_table_config(id, kwid, awid);
        } else if (strcmp(kw, "entry") == 0) {
            uint64_t id = parse_u64(strtok(NULL, " \t\r\n"), "table_id", lineno);
            uint64_t key = parse_u64(strtok(NULL, " \t\r\n"), "key", lineno);
            uint64_t action = parse_u64(strtok(NULL, " \t\r\n"), "action", lineno);
            zemu_map_table_add(id, key, action);
        } else {
            fprintf(stderr, "nanuk-map-emu: ctx line %d: unknown keyword %s\n", lineno, kw);
            exit(2);
        }
    }
    fclose(f);
}

int main(int argc, char **argv) {
    if (argc != 4) {
        fprintf(stderr, "usage: nanuk-map-emu <prog.bin> <packet.bin> <ctx.txt>\n");
        return 2;
    }

    size_t prog_size, pkt_size;
    unsigned char *prog = read_file(argv[1], &prog_size);
    unsigned char *pkt = read_file(argv[2], &pkt_size);

    if (prog_size % 4 != 0) {
        fprintf(stderr, "nanuk-map-emu: program size %zu is not a multiple of 4\n", prog_size);
        return 2;
    }
    if (prog_size / 4 > IMEM_WORDS) {
        fprintf(stderr, "nanuk-map-emu: program exceeds %d words\n", IMEM_WORDS);
        return 2;
    }

    model_init();
    zemu_map_reset(SAIL_UNIT);

    for (size_t i = 0; i < prog_size / 4; i++) {
        uint64_t word = ((uint64_t)prog[4 * i] << 24) | ((uint64_t)prog[4 * i + 1] << 16) |
                        ((uint64_t)prog[4 * i + 2] << 8) | (uint64_t)prog[4 * i + 3];
        zemu_map_poke_imem(i, word);
    }

    size_t poke_bytes = pkt_size < BUF_BYTES ? pkt_size : BUF_BYTES;
    for (size_t i = 0; i < poke_bytes; i++) {
        zemu_map_poke_pkt(i, pkt[i]);
    }
    uint64_t plen = pkt_size < 0xFFFF ? (uint64_t)pkt_size : 0xFFFF;
    zemu_map_set_plen(plen);

    load_ctx(argv[3]);

    zemu_map_run(SAIL_UNIT);

    uint64_t verdict = zemu_map_get_verdict(SAIL_UNIT);
    int64_t delta = (int16_t)zemu_map_get_delta(SAIL_UNIT);

    printf("{\"verdict\": %llu, \"error\": %llu, \"egress\": %llu, \"delta\": %lld, \"steps\": %llu",
           (unsigned long long)verdict,
           (unsigned long long)zemu_map_get_error(SAIL_UNIT),
           (unsigned long long)zemu_map_get_egress(SAIL_UNIT),
           (long long)delta,
           (unsigned long long)zemu_map_get_steps(SAIL_UNIT));
    if (verdict == 0) {
        // Transmitted frame: window[headroom - delta .. headroom + plen).
        printf(", \"frame\": \"");
        int64_t start = HEADROOM_BYTES - delta;
        int64_t end = HEADROOM_BYTES + (int64_t)(plen < BUF_BYTES ? plen : BUF_BYTES);
        for (int64_t i = start; i < end; i++) {
            printf("%02x", (unsigned)zemu_map_get_win_byte((uint64_t)i));
        }
        printf("\"");
    }
    printf("}\n");

    model_fini();
    free(prog);
    free(pkt);
    return 0;
}
