# Pre-built molecule test image for weisssrv CI.
# Based on the same systemd-enabled Debian Trixie image all molecule
# scenarios use, with common packages baked in so roles don't spend
# 3-5 minutes running apt-get install from scratch on every CI run.
#
# Build + push:
#   docker build -t registry.git.ericsweiss.com/eric/weisssrv/molecule-test:latest docker/molecule-test/
#   docker push registry.git.ericsweiss.com/eric/weisssrv/molecule-test:latest
#
# Rebuild when: common_packages in all.yml changes, or a role adds new
# apt dependencies that are slow to install. The image is a cache
# optimization — molecule tests still work without it (they just take
# longer installing packages at runtime).
FROM ghcr.io/hifis-net/debian-systemd:trixie

# Suppress interactive prompts during apt installs
ENV DEBIAN_FRONTEND=noninteractive

# All packages that roles install via apt, consolidated so a single
# apt-get layer caches them. Grouped by which role(s) need them.
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    # --- base role (common_packages) ---
    curl \
    wget \
    neovim \
    htop \
    tmux \
    screen \
    git \
    jq \
    unzip \
    rsync \
    net-tools \
    dnsutils \
    ca-certificates \
    gnupg \
    lsb-release \
    sudo \
    pciutils \
    # --- base role (VM packages) ---
    qemu-guest-agent \
    # --- base role (SSH + misc) ---
    openssh-server \
    fail2ban \
    logrotate \
    cron \
    # --- k3s role ---
    open-iscsi \
    nfs-common \
    # --- nas_storage role ---
    nfs-kernel-server \
    samba \
    smartmontools \
    mergerfs \
    # --- smtp_relay / postfix_null_client ---
    postfix \
    libsasl2-modules \
    mailutils \
    # --- adguard_home / unbound ---
    unbound \
    # --- qol role ---
    zsh \
    fzf \
    ripgrep \
    bat \
    fd-find \
    # --- acme_certs role ---
    socat \
    # --- plex role ---
    apt-transport-https \
    # --- general test utilities ---
    iproute2 \
    iputils-ping \
    procps \
    ethtool \
    python3 \
    python3-apt \
    && rm -rf /var/lib/apt/lists/*

# Pre-create the ansible tmp directory (avoids "Failed to create
# temporary directory" errors during molecule prepare)
RUN mkdir -p /root/.ansible/tmp && chmod 700 /root/.ansible/tmp

# Disable apt background services that race with molecule's apt calls
RUN systemctl disable apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
