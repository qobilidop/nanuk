/*
 * nanuk_hw: SimBricks network component wrapping the Verilator'd nanuk
 * parser core.
 *
 * Structure follows SimBricks' sims/net/switch/net_switch.cc (ports, argv,
 * connection setup) combined with sims/net/menshen/menshen_hw.cc (clocked
 * Verilator main loop). Forwarding policy is the stage-4 design's
 * deliberately dumb harness: parse each frame on the nanuk core; verdict
 * accept => flood to all other ports; anything else => drop. The parser
 * program decides what traffic the switch passes.
 *
 * Usage: nanuk_hw [-S SYNC-PERIOD] [-E ETH-LATENCY] [-u] -f PROG.BIN \
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

/* -------------------- Parser-gated flood controller -------------------- */

class Controller {
  enum State { kIdle, kLoad, kStart, kWait };

  Vnanuk_core &top;
  State state = kIdle;
  size_t load_idx = 0;
  uint64_t frames_in = 0, frames_fwd = 0, frames_drop = 0;

 public:
  explicit Controller(Vnanuk_core &top_) : top(top_) {
  }

  /* Called between falling and rising clock edge: drive inputs, read
   * outputs (which reflect the state after the previous rising edge). */
  void step() {
    top.pkt_we = 0;
    top.start = 0;

    switch (state) {
      case kIdle:
        if (!rx_queue.empty()) {
          frames_in++;
          load_idx = 0;
          state = kLoad;
        }
        break;

      case kLoad: {
        Frame &f = rx_queue.front();
        size_t n = f.len < CORE_BUF_BYTES ? f.len : CORE_BUF_BYTES;
        if (load_idx < n) {
          top.pkt_we = 1;
          top.pkt_addr = load_idx;
          top.pkt_data = f.data[load_idx];
          load_idx++;
        } else {
          top.plen = f.len < 0xFFFF ? f.len : 0xFFFF;
          top.start = 1;
          state = kStart;
        }
        break;
      }

      case kStart:
        /* start pulse consumed; core is running, done deasserted */
        state = kWait;
        break;

      case kWait:
        if (top.done) {
          Frame &f = rx_queue.front();
          if (top.verdict == 0) {
            /* accept: flood to all other ports */
            for (size_t ep = 0; ep < ports.size(); ep++) {
              if (ep != f.port)
                ports[ep]->TxPacket(f.data, f.len, cur_ts);
            }
            frames_fwd++;
          } else {
            frames_drop++;
          }
          rx_queue.pop_front();
          state = kIdle;
        }
        break;
    }
  }

  void stats() {
    fprintf(stderr, "nanuk_hw: frames in=%lu forwarded=%lu dropped=%lu\n",
            frames_in, frames_fwd, frames_drop);
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

static bool load_program(Vnanuk_core &top, const char *path) {
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
    /* one program word per clock cycle */
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
  fprintf(stderr, "nanuk_hw: loaded %u program words from %s\n", addr, path);
  return addr > 0;
}

int main(int argc, char *argv[]) {
  int c;
  int bad_option = 0;
  int sync_eth = 1;
  const char *prog_path = getenv("NANUK_PROG");

  SimbricksNetIfDefaultParams(&netParams);

  while ((c = getopt(argc, argv, "s:h:uS:E:f:")) != -1 && !bad_option) {
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
      default:
        fprintf(stderr, "unknown option %c\n", c);
        bad_option = 1;
        break;
    }
  }

  if (ports.empty() || bad_option || !prog_path) {
    fprintf(stderr,
            "Usage: nanuk_hw [-S SYNC-PERIOD] [-E ETH-LATENCY] [-u] "
            "-f PROG.BIN -s SOCKET-A [-s SOCKET-B ...]\n");
    return EXIT_FAILURE;
  }

  signal(SIGINT, sigint_handler);
  signal(SIGTERM, sigint_handler);
  signal(SIGUSR1, sigusr1_handler);

  char *vargs[2] = {argv[0], NULL};
  Verilated::commandArgs(1, vargs);
  Vnanuk_core *top = new Vnanuk_core;

  /* reset */
  top->rst = 1;
  for (int i = 0; i < 8; i++) {
    top->clk = 0;
    top->eval();
    top->clk = 1;
    top->eval();
  }
  top->rst = 0;

  if (!load_program(*top, prog_path))
    return EXIT_FAILURE;

  if (!ConnectAll(ports))
    return EXIT_FAILURE;

  Controller ctrl(*top);
  fprintf(stderr, "nanuk_hw: start polling\n");

  while (!exiting) {
    for (auto port : ports)
      port->Sync(cur_ts);

    poll_ports();

    /* falling edge */
    top->clk = 0;
    top->eval();

    ctrl.step();

    /* rising edge */
    top->clk = 1;
    top->eval();

    cur_ts += clock_period;
  }

  ctrl.stats();
  return 0;
}
