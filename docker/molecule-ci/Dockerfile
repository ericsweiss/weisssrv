# CI runner image for molecule test jobs. Pre-installs docker-cli,
# molecule, ansible, and all pip dependencies so the before_script
# drops from ~2 min (apt + pip + galaxy) to ~5s (docker login only).
#
# This is the JOB image (what GitLab CI runs the script in), not the
# MOLECULE image (the container molecule spins up to test roles).
#
# Build + push:
#   docker build -t registry.git.ericsweiss.com/eric/weisssrv/molecule-ci:latest docker/molecule-ci/
#   docker push registry.git.ericsweiss.com/eric/weisssrv/molecule-ci:latest
FROM python:3.11

# docker CLI for talking to the DinD sidecar. Install the static binary
# directly — the Debian docker.io package on Trixie doesn't include the
# CLI (only the daemon), and docker-ce-cli isn't in the default repos.
# Detect arch for multi-platform builds.
RUN ARCH=$(dpkg --print-architecture) \
    && case "$ARCH" in amd64) DOCKER_ARCH=x86_64;; arm64) DOCKER_ARCH=aarch64;; *) echo "unsupported: $ARCH"; exit 1;; esac \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${DOCKER_ARCH}/docker-27.5.1.tgz" \
    | tar xz --strip-components=1 -C /usr/local/bin docker/docker \
    && docker --version

# pip dependencies (molecule + ansible + plugins)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --quiet --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Ansible collections
COPY ansible/requirements.yml /tmp/requirements.yml
RUN ansible-galaxy collection install -r /tmp/requirements.yml \
    && rm /tmp/requirements.yml
