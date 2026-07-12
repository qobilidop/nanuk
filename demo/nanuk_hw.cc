/*
 * nanuk_hw: SimBricks network component wrapping the Verilator'd nanuk
 * parser + match-action cores (composed PP->MAP pipeline).
 *
 * Structure follows SimBricks' sims/net/switch/net_switch.cc (ports, argv,
 * connection setup) combined with sims/net/menshen/menshen_hw.cc (clocked
 * Verilator main loop). M2 forwarding: each frame is parsed by the PP core,
 * then (on accept) processed by the MAP core — the composed PP->MAP
 * pipeline. The MAP's egress bitmap and head delta decide where the
 * (possibly rewritten) frame goes; the TABLE is the forwarding policy,
 * loaded from a file and hot-reloaded on mtime change.
 *
 * Usage: nanuk_hw [-S SYNC-PERIOD] [-E ETH-LATENCY] [-u] -f PP_PROG.BIN \
 *            -m MAP_PROG.BIN [-t TABLES.TXT] \
 *            -s SOCKET-A [-s SOCKET-B ...] [-h LISTEN-SOCKET ...]
 */

#include <getopt.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <deque>
#include <string>
#include <vector>

#include <verilated.h>

#include <simbricks/base/cxxatomicfix.h>
extern "C" {
#include <simbricks/network/if.h>
}

#include "Vnanuk_pp.h"
#include "Vnanuk_map.h"

#include <sys/stat.h>

#include <set>

#define MAX_PKT_SIZE 2048
#define CORE_BUF_BYTES 256

static struct SimbricksBaseIfParams netParams;

/* ---------- Ports (copied from sims/net/switch/net_switch.cc) ---------- */

class NetPort {
 public:
  enum RxPollState {
    kRxPollSuccess = 0,
    kRxPollFail = 1,
    kRxPollSync = 2,
  };
  struct SimbricksNetIf netif_;

 protected:
  volatile union SimbricksProtoNetMsg *rx_;
  int sync_;
  const char *path_;

  bool Init() {
    struct SimbricksBaseIfParams params = netParams;
    params.sync_mode =
        (sync_ ? kSimbricksBaseIfSyncOptional : kSimbricksBaseIfSyncDisabled);
    params.sock_path = path_;
    params.blocking_conn = false;

    if (SimbricksBaseIfInit(&netif_.base, &params)) {
      perror("Init: SimbricksBaseIfInit failed");
      return false;
    }
    return true;
  }

 public:
  NetPort(const char *path, int sync) : rx_(nullptr), sync_(sync), path_(path) {
    memset(&netif_, 0, sizeof(netif_));
  }

  virtual ~NetPort() = default;

  virtual bool Prepare() {
    if (!Init())
      return false;
    if (SimbricksBaseIfConnect(&netif_.base)) {
      perror("Prepare: SimbricksBaseIfConnect failed");
      return false;
    }
    return true;
  }

  virtual void Prepared() {
    sync_ = SimbricksBaseIfSyncEnabled(&netif_.base);
  }

  bool IsSync() {
    return sync_;
  }

  void Sync(uint64_t cur_ts) {
    while (SimbricksNetIfOutSync(&netif_, cur_ts)) {
    }
  }

  uint64_t NextTimestamp() {
    return SimbricksNetIfInTimestamp(&netif_);
  }

  enum RxPollState RxPacket(const void *&data, size_t &len, uint64_t cur_ts) {
    assert(rx_ == nullptr);

    rx_ = SimbricksNetIfInPoll(&netif_, cur_ts);
    if (!rx_)
      return kRxPollFail;

    uint8_t type = SimbricksNetIfInType(&netif_, rx_);
    if (type == SIMBRICKS_PROTO_NET_MSG_PACKET) {
      data = (const void *)rx_->packet.data;
      len = rx_->packet.len;
      return kRxPollSuccess;
    } else if (type == SIMBRICKS_PROTO_MSG_TYPE_SYNC) {
      return kRxPollSync;
    } else {
      fprintf(stderr, "nanuk_hw: unsupported msg type=%u\n", type);
      abort();
    }
  }

  void RxDone() {
    assert(rx_ != nullptr);
    SimbricksNetIfInDone(&netif_, rx_);
    rx_ = nullptr;
  }

  bool TxPacket(const void *data, size_t len, uint64_t cur_ts) {
    volatile union SimbricksProtoNetMsg *msg_to =
        SimbricksNetIfOutAlloc(&netif_, cur_ts);
    if (!msg_to && !sync_) {
      return false;
    } else if (!msg_to && sync_) {
      while (!msg_to)
        msg_to = SimbricksNetIfOutAlloc(&netif_, cur_ts);
    }
    volatile struct SimbricksProtoNetMsgPacket *pkt = &msg_to->packet;
    pkt->len = len;
    pkt->port = 0;
    memcpy((void *)pkt->data, data, len);

    SimbricksNetIfOutSend(&netif_, msg_to, SIMBRICKS_PROTO_NET_MSG_PACKET);
    return true;
  }
};

class NetListenPort : public NetPort {
 protected:
  struct SimbricksBaseIfSHMPool pool_;

 public:
  NetListenPort(const char *path, int sync) : NetPort(path, sync) {
    memset(&pool_, 0, sizeof(pool_));
  }

  bool Prepare() override {
    if (!Init())
      return false;

    std::string shm_path = path_;
    shm_path += "-shm";

    if (SimbricksBaseIfSHMPoolCreate(
            &pool_, shm_path.c_str(),
            SimbricksBaseIfSHMSize(&netif_.base.params)) != 0) {
      perror("Prepare: SimbricksBaseIfSHMPoolCreate failed");
      return false;
    }
    if (SimbricksBaseIfListen(&netif_.base, &pool_) != 0) {
      perror("Prepare: SimbricksBaseIfListen failed");
      return false;
    }
    return true;
  }
};

static bool ConnectAll(std::vector<NetPort *> &all_ports) {
  size_t n = all_ports.size();
  std::vector<struct SimBricksBaseIfEstablishData> ests(n);
  struct SimbricksProtoNetIntro intro;
  memset(&intro, 0, sizeof(intro));

  for (size_t i = 0; i < n; i++) {
    NetPort *p = all_ports[i];
    ests[i].base_if = &p->netif_.base;
    ests[i].tx_intro = &intro;
    ests[i].tx_intro_len = sizeof(intro);
    ests[i].rx_intro = &intro;
    ests[i].rx_intro_len = sizeof(intro);

    if (!p->Prepare())
      return false;
  }

  if (SimBricksBaseIfEstablish(ests.data(), n)) {
    fprintf(stderr, "ConnectAll: SimBricksBaseIfEstablish failed\n");
    return false;
  }

  for (auto p : all_ports)
    p->Prepared();
  return true;
}

/* ------------------------------ Globals ------------------------------- */

static uint64_t cur_ts = 0;
static int exiting = 0;
static std::vector<NetPort *> ports;
static uint64_t clock_period = 4 * 1000ULL;  // 4ns -> 250MHz (picoseconds)

struct Frame {
  size_t port;
  size_t len;
  uint8_t data[MAX_PKT_SIZE];
};

static std::deque<Frame> rx_queue;
static const size_t RX_QUEUE_MAX = 64;

static void sigint_handler(int dummy) {
  exiting = 1;
}

static void sigusr1_handler(int dummy) {
  fprintf(stderr, "nanuk_hw: main_time = %lu\n", cur_ts);
}

/* ------------------ Composed PP -> MAP pipeline controller ------------------ */

#define MAP_HEADROOM 32
#define MAP_WIN_BYTES 288

/* Table state loaded from -t FILE (M1 ctx.txt `table`/`entry` lines). */
struct TableEntry {
  uint64_t table, key, action;
};
struct TableConfig {
  uint64_t id, kw, aw;
};
struct Tables {
  std::vector<TableConfig> configs;
  std::vector<TableEntry> entries;
};

static uint64_t mask_width(uint64_t v, uint64_t w) {
  if (w == 0)
    return 0;
  if (w >= 64)
    return v;
  return v & ((1ULL << w) - 1);
}

static bool parse_tables(const char *path, Tables &out) {
  FILE *f = fopen(path, "r");
  if (!f) {
    fprintf(stderr, "nanuk_hw: cannot open tables %s\n", path);
    return false;
  }
  out.configs.clear();
  out.entries.clear();
  char line[256];
  while (fgets(line, sizeof line, f)) {
    char *hash = strchr(line, '#');
    if (hash) *hash = '\0';
    char *kw = strtok(line, " \t\r\n");
    if (!kw) continue;
    if (strcmp(kw, "table") == 0) {
      TableConfig c;
      c.id = strtoull(strtok(NULL, " \t\r\n") ?: "0", NULL, 0);
      c.kw = strtoull(strtok(NULL, " \t\r\n") ?: "0", NULL, 0);
      c.aw = strtoull(strtok(NULL, " \t\r\n") ?: "0", NULL, 0);
      out.configs.push_back(c);
    } else if (strcmp(kw, "entry") == 0) {
      TableEntry e;
      e.table = strtoull(strtok(NULL, " \t\r\n") ?: "0", NULL, 0);
      e.key = strtoull(strtok(NULL, " \t\r\n") ?: "0", NULL, 0);
      e.action = strtoull(strtok(NULL, " \t\r\n") ?: "0", NULL, 0);
      out.entries.push_back(e);
    } else {
      fprintf(stderr, "nanuk_hw: tables: unknown keyword %s\n", kw);
      fclose(f);
      return false;
    }
  }
  fclose(f);
  return true;
}

/* Clocked poke of the table config into the MAP model (own clock toggles;
 * only called outside the main loop or while the controller is idle). */
static void program_tables(Vnanuk_map &map, const Tables &t) {
  auto tick = [&]() {
    map.clk = 0;
    map.eval();
    map.clk = 1;
    map.eval();
  };
  for (const auto &c : t.configs) {
    map.tbl_cfg_we = 1;
    map.tbl_cfg_id = c.id & 3;
    map.tbl_cfg_kw = c.kw;
    map.tbl_cfg_aw = c.aw;
    tick();
  }
  map.tbl_cfg_we = 0;
  /* Widths for masking adds (mirror emu_map_table_add). */
  uint64_t kws[4] = {0, 0, 0, 0}, aws[4] = {0, 0, 0, 0};
  for (const auto &c : t.configs) {
    kws[c.id & 3] = c.kw;
    aws[c.id & 3] = c.aw;
  }
  for (const auto &e : t.entries) {
    map.tbl_add_we = 1;
    map.tbl_add_id = e.table & 3;
    map.tbl_add_key = mask_width(e.key, kws[e.table & 3]);
    map.tbl_add_action = mask_width(e.action, aws[e.table & 3]);
    tick();
  }
  map.tbl_add_we = 0;
  tick();
  fprintf(stderr, "nanuk_hw: tables programmed (%zu configs, %zu entries)\n",
          t.configs.size(), t.entries.size());
}

class Controller {
  enum State {
    kIdle,
    kLoad,
    kStart,
    kWait,
    kMapLoad,
    kMapStart,
    kMapWait,
    kMapRead
  };

  Vnanuk_pp &pp;
  Vnanuk_map &map;
  State state = kIdle;
  size_t load_idx = 0;
  int64_t map_delta = 0;
  size_t rb_len = 0, rb_i = 0;
  uint8_t tx_buf[MAX_PKT_SIZE + MAP_HEADROOM];
  uint64_t frames_in = 0, frames_sent = 0, frames_drop = 0, map_err = 0;
  uint64_t flooded = 0, delta_pos = 0, delta_neg = 0;
  std::set<uint64_t> seen_dmacs;

 public:
  bool idle() const {
    return state == kIdle && rx_queue.empty();
  }

  Controller(Vnanuk_pp &pp_, Vnanuk_map &map_) : pp(pp_), map(map_) {
  }

  void log_dmac(const Frame &f) {
    if (f.len < 6 || seen_dmacs.size() >= 8)
      return;
    uint64_t dmac = 0;
    for (int i = 0; i < 6; i++)
      dmac = (dmac << 8) | f.data[i];
    if (seen_dmacs.insert(dmac).second) {
      fprintf(stderr,
              "nanuk_hw: port %zu dmac %02x:%02x:%02x:%02x:%02x:%02x\n",
              f.port, f.data[0], f.data[1], f.data[2], f.data[3], f.data[4],
              f.data[5]);
    }
  }

  void step() {
    pp.pkt_we = 0;
    pp.start = 0;
    map.win_we = 0;
    map.start = 0;

    switch (state) {
      case kIdle:
        if (!rx_queue.empty()) {
          frames_in++;
          log_dmac(rx_queue.front());
          load_idx = 0;
          state = kLoad;
        }
        break;

      case kLoad: {
        Frame &f = rx_queue.front();
        size_t n = f.len < CORE_BUF_BYTES ? f.len : CORE_BUF_BYTES;
        if (load_idx < CORE_BUF_BYTES) {
          /* Full buffer: frame bytes then zero padding (stale bytes from the
           * previous frame must not leak into short packets). */
          pp.pkt_we = 1;
          pp.pkt_addr = load_idx;
          pp.pkt_data = load_idx < n ? f.data[load_idx] : 0;
          load_idx++;
        } else {
          pp.plen = f.len < 0xFFFF ? f.len : 0xFFFF;
          pp.start = 1;
          state = kStart;
        }
        break;
      }

      case kStart:
        state = kWait;
        break;

      case kWait:
        if (pp.done) {
          if (pp.verdict == 0) {
            load_idx = 0;
            state = kMapLoad;
          } else {
            frames_drop++;
            rx_queue.pop_front();
            state = kIdle;
          }
        }
        break;

      case kMapLoad: {
        Frame &f = rx_queue.front();
        size_t n = f.len < CORE_BUF_BYTES ? f.len : CORE_BUF_BYTES;
        if (load_idx < MAP_WIN_BYTES) {
          map.win_we = 1;
          map.win_addr = load_idx;
          size_t fo = load_idx - MAP_HEADROOM;
          map.win_data =
              (load_idx >= MAP_HEADROOM && fo < n) ? f.data[fo] : 0;
          load_idx++;
        } else {
          /* Wire the PP's outbound contract into the MAP's inbound one. */
          map.plen = f.len < 0xFFFF ? f.len : 0xFFFF;
          map.ingress = f.port;
          map.hdr_present_in = pp.hdr_present;
          for (int i = 0; i < 8; i++)  /* 256-bit: 8 x 32-bit words */
            map.hdr_offset_in[i] = pp.hdr_offset[i];
          for (int i = 0; i < 4; i++)  /* 128-bit: 4 x 32-bit words */
            map.smd_in[i] = pp.smd[i];
          map.start = 1;
          state = kMapStart;
        }
        break;
      }

      case kMapStart:
        state = kMapWait;
        break;

      case kMapWait:
        if (map.done) {
          Frame &f = rx_queue.front();
          if (map.verdict == 0) {
            map_delta = (int16_t)map.delta;
            size_t win_pl = f.len < CORE_BUF_BYTES ? f.len : CORE_BUF_BYTES;
            rb_len = win_pl + map_delta; /* window part of the tx frame */
            rb_i = 0;
            map.win_rd_addr = MAP_HEADROOM - map_delta;
            state = kMapRead;
          } else if (map.verdict == 1) {
            frames_drop++;
            rx_queue.pop_front();
            state = kIdle;
          } else {
            map_err++;
            frames_drop++;
            rx_queue.pop_front();
            state = kIdle;
          }
        }
        break;

      case kMapRead: {
        /* Sync read: win_rd_data reflects the addr set in the previous
         * step (kMapWait set the first address): capture, then advance. */
        Frame &f = rx_queue.front();
        if (rb_i < rb_len) {
          tx_buf[rb_i] = map.win_rd_data;
          map.win_rd_addr = MAP_HEADROOM - map_delta + rb_i + 1;
          rb_i++;
        } else {
          /* Tail passthrough for frames beyond the 256B window. */
          size_t tx_len = rb_len;
          if (f.len > CORE_BUF_BYTES) {
            size_t tail = f.len - CORE_BUF_BYTES;
            if (tx_len + tail > sizeof(tx_buf))
              tail = sizeof(tx_buf) - tx_len;
            memcpy(tx_buf + tx_len, f.data + CORE_BUF_BYTES, tail);
            tx_len += tail;
          }
          unsigned egress = map.egress & 0xF;
          unsigned popcount = __builtin_popcount(egress);
          if (popcount > 1)
            flooded++;
          if (map_delta > 0)
            delta_pos++;
          else if (map_delta < 0)
            delta_neg++;
          for (size_t ep = 0; ep < ports.size() && ep < 4; ep++) {
            if (egress & (1u << ep))
              ports[ep]->TxPacket(tx_buf, tx_len, cur_ts);
          }
          frames_sent++;
          rx_queue.pop_front();
          state = kIdle;
        }
        break;
      }
    }
  }

  void stats(const char *tag) {
    fprintf(stderr,
            "nanuk_hw[%s]: frames in=%lu sent=%lu dropped=%lu map_err=%lu "
            "flooded=%lu delta_pos=%lu delta_neg=%lu\n",
            tag, frames_in, frames_sent, frames_drop, map_err, flooded,
            delta_pos, delta_neg);
  }
};

static void poll_ports() {
  size_t p_id = 0;
  for (auto port : ports) {
    const void *data;
    size_t len;
    enum NetPort::RxPollState ps = port->RxPacket(data, len, cur_ts);
    if (ps == NetPort::kRxPollSuccess) {
      if (rx_queue.size() < RX_QUEUE_MAX && len <= MAX_PKT_SIZE) {
        Frame f;
        f.port = p_id;
        f.len = len;
        memcpy(f.data, data, len);
        rx_queue.push_back(f);
      } else {
        fprintf(stderr, "nanuk_hw: rx queue full, dropping frame\n");
      }
    }
    if (ps != NetPort::kRxPollFail)
      port->RxDone();
    p_id++;
  }
}

/* ------------------------------- main ---------------------------------- */

static bool load_program(Vnanuk_pp &top, const char *path) {
  FILE *f = fopen(path, "rb");
  if (!f) {
    fprintf(stderr, "nanuk_hw: cannot open program %s\n", path);
    return false;
  }
  uint8_t word[4];
  uint16_t addr = 0;
  while (fread(word, 1, 4, f) == 4) {
    uint32_t w = ((uint32_t)word[0] << 24) | ((uint32_t)word[1] << 16) |
                 ((uint32_t)word[2] << 8) | (uint32_t)word[3];
    top.prog_we = 1;
    top.prog_addr = addr++;
    top.prog_data = w;
    top.clk = 0;
    top.eval();
    top.clk = 1;
    top.eval();
  }
  top.prog_we = 0;
  fclose(f);
  fprintf(stderr, "nanuk_hw: loaded %u PP program words from %s\n", addr, path);
  return addr > 0;
}

static bool load_map_program(Vnanuk_map &top, const char *path) {
  FILE *f = fopen(path, "rb");
  if (!f) {
    fprintf(stderr, "nanuk_hw: cannot open MAP program %s\n", path);
    return false;
  }
  uint8_t word[4];
  uint16_t addr = 0;
  while (fread(word, 1, 4, f) == 4) {
    uint32_t w = ((uint32_t)word[0] << 24) | ((uint32_t)word[1] << 16) |
                 ((uint32_t)word[2] << 8) | (uint32_t)word[3];
    top.prog_we = 1;
    top.prog_addr = addr++;
    top.prog_data = w;
    top.clk = 0;
    top.eval();
    top.clk = 1;
    top.eval();
  }
  top.prog_we = 0;
  fclose(f);
  fprintf(stderr, "nanuk_hw: loaded %u MAP program words from %s\n", addr,
          path);
  return addr > 0;
}

static time_t tables_mtime(const char *path) {
  struct stat st;
  if (stat(path, &st) != 0)
    return 0;
  return st.st_mtime;
}

int main(int argc, char *argv[]) {
  int c;
  int bad_option = 0;
  int sync_eth = 1;
  const char *prog_path = getenv("NANUK_PROG");
  const char *map_prog_path = getenv("NANUK_MAP_PROG");
  const char *tables_path = getenv("NANUK_TABLES");

  SimbricksNetIfDefaultParams(&netParams);

  while ((c = getopt(argc, argv, "s:h:uS:E:f:m:t:")) != -1 && !bad_option) {
    switch (c) {
      case 's':
        fprintf(stderr, "nanuk_hw: connecting to: %s\n", optarg);
        ports.push_back(new NetPort(optarg, sync_eth));
        break;
      case 'h':
        fprintf(stderr, "nanuk_hw: listening on: %s\n", optarg);
        ports.push_back(new NetListenPort(optarg, sync_eth));
        break;
      case 'u':
        sync_eth = 0;
        break;
      case 'S':
        netParams.sync_interval = strtoull(optarg, NULL, 0) * 1000ULL;
        break;
      case 'E':
        netParams.link_latency = strtoull(optarg, NULL, 0) * 1000ULL;
        break;
      case 'f':
        prog_path = optarg;
        break;
      case 'm':
        map_prog_path = optarg;
        break;
      case 't':
        tables_path = optarg;
        break;
      default:
        fprintf(stderr, "unknown option %c\n", c);
        bad_option = 1;
        break;
    }
  }

  if (ports.empty() || bad_option || !prog_path || !map_prog_path) {
    fprintf(stderr,
            "Usage: nanuk_hw [-S SYNC-PERIOD] [-E ETH-LATENCY] [-u] "
            "-f PP_PROG.BIN -m MAP_PROG.BIN [-t TABLES.TXT] "
            "-s SOCKET-A [-s SOCKET-B ...]\n");
    return EXIT_FAILURE;
  }

  signal(SIGINT, sigint_handler);
  signal(SIGTERM, sigint_handler);
  signal(SIGUSR1, sigusr1_handler);

  char *vargs[2] = {argv[0], NULL};
  Verilated::commandArgs(1, vargs);
  Vnanuk_pp *pp = new Vnanuk_pp;
  Vnanuk_map *map = new Vnanuk_map;

  /* reset both cores */
  pp->rst = 1;
  map->rst = 1;
  for (int i = 0; i < 8; i++) {
    pp->clk = 0;
    map->clk = 0;
    pp->eval();
    map->eval();
    pp->clk = 1;
    map->clk = 1;
    pp->eval();
    map->eval();
  }
  pp->rst = 0;
  map->rst = 0;

  if (!load_program(*pp, prog_path))
    return EXIT_FAILURE;
  if (!load_map_program(*map, map_prog_path))
    return EXIT_FAILURE;

  Tables tables;
  time_t tables_seen = 0;
  if (tables_path) {
    if (!parse_tables(tables_path, tables))
      return EXIT_FAILURE;
    program_tables(*map, tables);
    tables_seen = tables_mtime(tables_path);
  } else {
    fprintf(stderr, "nanuk_hw: no tables file; all lookups miss\n");
  }

  if (!ConnectAll(ports))
    return EXIT_FAILURE;

  Controller ctrl(*pp, *map);
  fprintf(stderr, "nanuk_hw: start polling\n");

  uint64_t iter = 0;
  while (!exiting) {
    for (auto port : ports)
      port->Sync(cur_ts);

    poll_ports();

    /* falling edge */
    pp->clk = 0;
    map->clk = 0;
    pp->eval();
    map->eval();

    ctrl.step();

    /* rising edge */
    pp->clk = 1;
    map->clk = 1;
    pp->eval();
    map->eval();

    cur_ts += clock_period;

    /* Hot-reload tables on mtime change (the table IS the policy). Only
     * between frames, so a frame never sees a half-programmed table. */
    if (tables_path && (++iter & 0xFFFF) == 0 && ctrl.idle()) {
      time_t mt = tables_mtime(tables_path);
      if (mt != 0 && mt != tables_seen) {
        tables_seen = mt;
        if (parse_tables(tables_path, tables)) {
          program_tables(*map, tables);
          fprintf(stderr, "nanuk_hw: tables reloaded\n");
        }
      }
    }
  }

  const char *tag = strrchr(map_prog_path, '/');
  ctrl.stats(tag ? tag + 1 : map_prog_path);
  return 0;
}
