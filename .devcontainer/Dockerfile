FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# System dependencies for Sail and its C backend, plus Python for the
# assembler/harness toolchain
RUN apt-get update && apt-get install -y \
    opam clang lld make cmake pkg-config \
    libgmp-dev zlib1g-dev z3 \
    python3 python3-venv \
    curl ca-certificates git \
    verilator libpcap-dev \
    && rm -rf /var/lib/apt/lists/*

# Initialize opam (OCaml package manager)
# --disable-sandboxing is needed inside Docker (no bwrap available)
RUN opam init --disable-sandboxing --yes --bare

# Create an OCaml 5.1.0 switch (compiler environment)
RUN opam switch create 5.1.0

# Install Sail into this switch
RUN eval $(opam env --switch=5.1.0) && opam install sail --yes

# Make opam binaries available without eval $(opam env)
ENV PATH="/root/.opam/5.1.0/bin:${PATH}"
ENV SAIL_DIR="/root/.opam/5.1.0/share/sail"

# uv: Python project/dependency manager for spec/python
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# Use clang as the default C compiler
ENV CC=clang
