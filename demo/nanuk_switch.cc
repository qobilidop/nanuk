/*
 * nanuk_switch: SimBricks network component wrapping the Verilator'd Nanuk
 * core (the composed PP->MAP datapath behind its streaming face).
 *
 * Structure follows SimBricks' sims/net/switch/net_switch.cc (ports, argv,
 * connection setup) combined with sims/net/menshen/menshen_hw.cc (clocked
 * Verilator main loop). The switch is pure periphery now: it streams each
 * frame into the core with the ingress port id stamped into metadata slot
 * 0, collects the (possibly rewritten) output stream, and fans it out to
 * the ports in the egress bitmap the program left in metadata slot 0 —
 * the nanuk_switch slot conventions. Programs and tables load through the
 * core's control port; the TABLE is the forwarding policy, loaded from a
 * file and hot-reloaded on mtime change. The system flood table (t3:
 * {ingress -> every port but ingress}) is installed by this switch at
 * boot — flooding is the periphery's policy, not the core's semantics.
 *
 * Usage: nanuk_switch [-S SYNC-PERIOD] [-E ETH-LATENCY] [-u] -f PP_PROG.BIN \
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

#include "Vnanuk_core.h"

#include <sys/stat.h>

#include <set>

#define MAX_PKT_SIZE 2048
#define N_PORTS 4

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
      fprintf(stderr, "nanuk_switch: unsupported msg type=%u\n", type);
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
  fprintf(stderr, "nanuk_switch: main_time = %lu\n", cur_ts);
}

/* --------------------- Core control-plane loading ---------------------- */

/* ctrl_sel values (the core's control port decode). */
#define CTRL_PP_IMEM 0
#define CTRL_MAP_IMEM 1
#define CTRL_TBL_CFG 2
#define CTRL_TBL_ADD 3

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
    fprintf(stderr, "nanuk_switch: cannot open tables %s\n", path);
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
      fprintf(stderr, "nanuk_switch: tables: unknown keyword %s\n", kw);
      fclose(f);
      return false;
    }
  }
  fclose(f);
  return true;
}

/* Clocked write on the core's control port (own clock toggles; only called
 * outside the main loop or while the controller is idle). */
static void ctrl_write(Vnanuk_core &core, uint8_t sel, uint16_t addr,
                       uint64_t data) {
  core.ctrl_sel = sel;
  core.ctrl_addr = addr;
  core.ctrl_data = data;
  core.ctrl_we = 1;
  core.clk = 0;
  core.eval();
  core.clk = 1;
  core.eval();
  core.ctrl_we = 0;
}

static bool load_program(Vnanuk_core &core, uint8_t sel, const char *path,
                         const char *what) {
  FILE *f = fopen(path, "rb");
  if (!f) {
    fprintf(stderr, "nanuk_switch: cannot open %s program %s\n", what, path);
    return false;
  }
  uint8_t word[4];
  uint16_t addr = 0;
  while (fread(word, 1, 4, f) == 4) {
    uint32_t w = ((uint32_t)word[0] << 24) | ((uint32_t)word[1] << 16) |
                 ((uint32_t)word[2] << 8) | (uint32_t)word[3];
    ctrl_write(core, sel, addr++, w);
  }
  fclose(f);
  fprintf(stderr, "nanuk_switch: loaded %u %s program words from %s\n", addr,
          what, path);
  return addr > 0;
}

/* The system flood table: t3 = {ingress port -> every port but ingress},
 * the nanuk_switch convention. Installed at boot; a -t file that configures
 * t3 itself replaces it (file programming runs after this). */
static void program_flood_table(Vnanuk_core &core) {
  ctrl_write(core, CTRL_TBL_CFG, 3, (16ULL << 8) | 16ULL);  // kw=16, aw=16
  unsigned all = (1u << N_PORTS) - 1;
  for (unsigned i = 0; i < N_PORTS; i++) {
    ctrl_write(core, CTRL_TBL_ADD, 3, i);                      // key
    ctrl_write(core, CTRL_TBL_ADD, (1u << 15) | 3, all & ~(1u << i));  // action
  }
  fprintf(stderr, "nanuk_switch: system flood table installed (t3)\n");
}

static void program_tables(Vnanuk_core &core, const Tables &t) {
  /* Widths for masking adds (mirror emu_map_table_add). */
  uint64_t kws[4] = {0, 0, 0, 0}, aws[4] = {0, 0, 0, 0};
  for (const auto &c : t.configs) {
    kws[c.id & 3] = c.kw;
    aws[c.id & 3] = c.aw;
    ctrl_write(core, CTRL_TBL_CFG, c.id & 3, (c.aw << 8) | (c.kw & 0xFF));
  }
  for (const auto &e : t.entries) {
    ctrl_write(core, CTRL_TBL_ADD, e.table & 3,
               mask_width(e.key, kws[e.table & 3]));
    ctrl_write(core, CTRL_TBL_ADD, (1u << 15) | (e.table & 3),
               mask_width(e.action, aws[e.table & 3]));
  }
  fprintf(stderr, "nanuk_switch: tables programmed (%zu configs, %zu entries)\n",
          t.configs.size(), t.entries.size());
}

/* ---------------------- Streaming-face controller ----------------------- */

class Controller {
  enum State { kIdle, kStream, kRun };

  Vnanuk_core &core;
  State state = kIdle;
  size_t tx_i = 0;          /* next input byte index */
  uint8_t rx_buf[MAX_PKT_SIZE];
  size_t rx_len = 0;
  uint64_t frames_in = 0, frames_sent = 0, frames_drop = 0, core_err = 0;
  uint64_t flooded = 0;
  std::set<uint64_t> seen_dmacs;

 public:
  bool idle() const {
    return state == kIdle && rx_queue.empty();
  }

  explicit Controller(Vnanuk_core &core_) : core(core_) {
  }

  void log_dmac(const Frame &f) {
    if (f.len < 6 || seen_dmacs.size() >= 8)
      return;
    uint64_t dmac = 0;
    for (int i = 0; i < 6; i++)
      dmac = (dmac << 8) | f.data[i];
    if (seen_dmacs.insert(dmac).second) {
      fprintf(stderr,
              "nanuk_switch: port %zu dmac %02x:%02x:%02x:%02x:%02x:%02x\n",
              f.port, f.data[0], f.data[1], f.data[2], f.data[3], f.data[4],
              f.data[5]);
    }
  }

  /* Called between the falling and rising edge, with combinational state
   * settled: drive this cycle's inputs, re-settle, then judge the beat the
   * coming edge will commit from the same pre-edge snapshot the core sees
   * (drive-then-sample; the state updates feed the NEXT cycle's drive). */
  void step() {
    core.in_tvalid = 0;
    core.out_tready = 1;  /* the switch never backpressures the core */

    switch (state) {
      case kIdle:
        if (!rx_queue.empty()) {
          frames_in++;
          log_dmac(rx_queue.front());
          Frame &f = rx_queue.front();
          /* md_in slot 0 = ingress port id (nanuk_switch convention). */
          core.md_in[0] = (uint32_t)f.port;
          core.md_in[1] = 0;
          core.md_in[2] = 0;
          core.md_in[3] = 0;
          tx_i = 0;
          rx_len = 0;
          state = kStream;
          /* fall through to offer the first byte this cycle */
        } else {
          break;
        }
        [[fallthrough]];

      case kStream: {
        Frame &f = rx_queue.front();
        if (tx_i < f.len) {
          core.in_tvalid = 1;
          core.in_tdata = f.data[tx_i];
          core.in_tlast = (tx_i == f.len - 1) ? 1 : 0;
        }
        break;
      }

      case kRun:
        break;
    }

    core.eval();  /* settle comb outputs against this cycle's inputs */

    if (state == kStream && core.in_tvalid && core.in_tready) {
      Frame &f = rx_queue.front();
      tx_i++;
      if (tx_i == f.len)
        state = kRun;
    }

    /* Output stream beat (out_tready is constant 1). */
    if (core.out_tvalid && rx_len < sizeof(rx_buf)) {
      rx_buf[rx_len++] = core.out_tdata;
    }

    if (core.result_valid) {
      Frame &f = rx_queue.front();
      unsigned verdict = core.result_verdict;
      if (verdict == 0) {
        /* Sent: fan out per md_out slot 0 (the egress bitmap under the
         * nanuk_switch convention). */
        unsigned egress = core.md_out[0] & 0xF;
        unsigned popcount = __builtin_popcount(egress);
        if (popcount > 1)
          flooded++;
        for (size_t ep = 0; ep < ports.size() && ep < N_PORTS; ep++) {
          if (egress & (1u << ep))
            ports[ep]->TxPacket(rx_buf, rx_len, cur_ts);
        }
        frames_sent++;
      } else if (verdict == 1) {
        frames_drop++;
      } else {
        core_err++;
        frames_drop++;
        fprintf(stderr, "nanuk_switch: core error %#04x (frame on port %zu)\n",
                (unsigned)core.result_error, f.port);
      }
      rx_queue.pop_front();
      state = kIdle;
    }
  }

  void stats(const char *tag) {
    fprintf(stderr,
            "nanuk_switch[%s]: frames in=%lu sent=%lu dropped=%lu "
            "core_err=%lu flooded=%lu\n",
            tag, frames_in, frames_sent, frames_drop, core_err, flooded);
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
        fprintf(stderr, "nanuk_switch: rx queue full, dropping frame\n");
      }
    }
    if (ps != NetPort::kRxPollFail)
      port->RxDone();
    p_id++;
  }
}

/* ------------------------------- main ---------------------------------- */

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
        fprintf(stderr, "nanuk_switch: connecting to: %s\n", optarg);
        ports.push_back(new NetPort(optarg, sync_eth));
        break;
      case 'h':
        fprintf(stderr, "nanuk_switch: listening on: %s\n", optarg);
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
            "Usage: nanuk_switch [-S SYNC-PERIOD] [-E ETH-LATENCY] [-u] "
            "-f PP_PROG.BIN -m MAP_PROG.BIN [-t TABLES.TXT] "
            "-s SOCKET-A [-s SOCKET-B ...]\n");
    return EXIT_FAILURE;
  }

  signal(SIGINT, sigint_handler);
  signal(SIGTERM, sigint_handler);
  signal(SIGUSR1, sigusr1_handler);

  char *vargs[2] = {argv[0], NULL};
  Verilated::commandArgs(1, vargs);
  Vnanuk_core *core = new Vnanuk_core;

  /* reset */
  core->rst = 1;
  for (int i = 0; i < 8; i++) {
    core->clk = 0;
    core->eval();
    core->clk = 1;
    core->eval();
  }
  core->rst = 0;

  if (!load_program(*core, CTRL_PP_IMEM, prog_path, "PP"))
    return EXIT_FAILURE;
  if (!load_program(*core, CTRL_MAP_IMEM, map_prog_path, "MAP"))
    return EXIT_FAILURE;

  program_flood_table(*core);

  Tables tables;
  time_t tables_seen = 0;
  if (tables_path) {
    if (!parse_tables(tables_path, tables))
      return EXIT_FAILURE;
    program_tables(*core, tables);
    tables_seen = tables_mtime(tables_path);
  } else {
    fprintf(stderr,
            "nanuk_switch: no tables file; FDB lookups miss (flood only)\n");
  }

  if (!ConnectAll(ports))
    return EXIT_FAILURE;

  Controller ctrl(*core);
  fprintf(stderr, "nanuk_switch: start polling\n");

  uint64_t iter = 0;
  while (!exiting) {
    for (auto port : ports)
      port->Sync(cur_ts);

    poll_ports();

    /* falling edge, then drive-and-sample this cycle's pre-edge state */
    core->clk = 0;
    core->eval();
    ctrl.step();

    /* rising edge: the core commits the beats step() judged */
    core->clk = 1;
    core->eval();

    cur_ts += clock_period;

    /* Hot-reload tables on mtime change (the table IS the policy). Only
     * between frames, so a frame never sees a half-programmed table. */
    if (tables_path && (++iter & 0xFFFF) == 0 && ctrl.idle()) {
      time_t mt = tables_mtime(tables_path);
      if (mt != 0 && mt != tables_seen) {
        tables_seen = mt;
        if (parse_tables(tables_path, tables)) {
          program_tables(*core, tables);
          fprintf(stderr, "nanuk_switch: tables reloaded\n");
        }
      }
    }
  }

  const char *tag = strrchr(map_prog_path, '/');
  ctrl.stats(tag ? tag + 1 : map_prog_path);
  return 0;
}
