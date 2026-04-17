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
    # NOTE: qemu-guest-agent intentionally excluded — the base role's
    # molecule verify asserts it's NOT installed in Docker containers.
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
    sasl2-bin \
    mailutils \
    # --- adguard_home ---
    python3-passlib \
    # --- unbound ---
    unbound \
    dns-root-data \
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
    python3-apt
    # NOTE: intentionally NOT removing /var/lib/apt/lists/* — keeping the
    # apt cache warm so roles with `update_cache: true` get a near-instant
    # cache hit instead of re-downloading all package metadata from the
    # internet (~2-3 min saved per role). The ~30MB image size increase is
    # worth it for CI speed.

# Pre-create the ansible tmp directory (avoids "Failed to create
# temporary directory" errors during molecule prepare)
RUN mkdir -p /root/.ansible/tmp && chmod 700 /root/.ansible/tmp

# Disable services that auto-start on install — roles should be the ones
# that enable/start them (mirrors real-world where packages are installed
# but services don't auto-start until Ansible runs). Without this, the
# idempotence check fails because systemd restarts them between plays.
RUN systemctl disable apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true \
    && systemctl disable postfix 2>/dev/null || true \
    && systemctl disable unbound 2>/dev/null || true \
    && systemctl disable nfs-kernel-server 2>/dev/null || true \
    && systemctl disable smbd nmbd 2>/dev/null || true \
    && systemctl disable ssh 2>/dev/null || true \
    && systemctl disable fail2ban 2>/dev/null || true
