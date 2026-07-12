# SimBricks build rules for the nanuk network component. This file is copied
# (with the rest of demo/) into the SimBricks tree as sims/net/nanuk/
# and registered in sims/net/rules.mk. Modeled on sims/net/menshen/rules.mk.

include mk/subdir_pre.mk

dir_nanuk := $(d)
bin_nanuk := $(d)nanuk_hw
verilator_dir_nanuk := $(d)obj_dir
verilator_src_nanuk := $(verilator_dir_nanuk)/Vnanuk_core.cpp
verilator_bin_nanuk := $(verilator_dir_nanuk)/Vnanuk_core

vsrcs_nanuk := $(wildcard $(d)rtl/*.v)
srcs_nanuk := $(addprefix $(d),nanuk_hw.cc)

$(verilator_src_nanuk): $(vsrcs_nanuk)
	$(VERILATOR) $(VFLAGS) --timescale 1ns/1ps --cc -O3 \
	    -CFLAGS "-I$(abspath $(lib_dir)) -iquote $(abspath $(base_dir)) -O3 -g -Wall" \
	    --Mdir $(verilator_dir_nanuk) \
	    -y $(dir_nanuk)rtl \
	    $(dir_nanuk)rtl/nanuk_core.v --exe $(abspath $(srcs_nanuk)) \
	    $(abspath $(lib_netif)) $(abspath $(lib_base))

$(verilator_bin_nanuk): $(verilator_src_nanuk) $(srcs_nanuk) $(lib_netif) $(lib_base)
	$(MAKE) -C $(verilator_dir_nanuk) -f Vnanuk_core.mk

$(bin_nanuk): $(verilator_bin_nanuk)
	cp $< $@

CLEAN := $(bin_nanuk) $(verilator_dir_nanuk)
ifeq ($(ENABLE_VERILATOR),y)
ALL := $(bin_nanuk)
endif
include mk/subdir_post.mk
