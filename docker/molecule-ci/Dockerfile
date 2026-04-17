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

# docker-cli for talking to the DinD sidecar
RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends docker.io \
    && rm -rf /var/lib/apt/lists/*

# pip dependencies (molecule + ansible + plugins)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --quiet --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Ansible collections
COPY ansible/requirements.yml /tmp/requirements.yml
RUN ansible-galaxy collection install -r /tmp/requirements.yml \
    && rm /tmp/requirements.yml
