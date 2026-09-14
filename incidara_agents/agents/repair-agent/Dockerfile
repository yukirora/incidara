# Repair-agent Docker image.
# Extends the base claude-agent image with MCP server packages,
# ltp-platform SDK dependencies, and offline deb/bin bundles for node reset.
#
# deb/bin bundles are downloaded during build from official repos (version-pinned).
# IPMICFG is COPY'd from local (Supermicro requires login, no public URL).
# ltp-platform/ SDK is gitignored but exists on the build machine.

FROM claude-agent:latest

# Copy MCP server packages
COPY mcp_servers/node-operations /opt/node-operations
COPY mcp_servers/agent-evidence /opt/agent-evidence

# Copy ltp-platform SDK packages (private — not on PyPI)
COPY ltp-platform/src/ltp-storage-common /opt/ltp-platform/ltp-storage-common
COPY ltp-platform/src/postgresql-sdk/sdk /opt/ltp-platform/postgresql-sdk

# Install MCP servers + ltp SDKs
RUN pip3 install --break-system-packages --no-cache-dir \
      /opt/node-operations \
      /opt/agent-evidence \
      /opt/ltp-platform/ltp-storage-common \
      /opt/ltp-platform/postgresql-sdk

# ipmitool for remote BMC queries (SEL log, chassis status, sensor readings)
RUN apt-get update -qq && apt-get install -y -qq ipmitool wget && rm -rf /var/lib/apt/lists/*

# --- Download offline deb/bin bundles for node reset/reallocation ---
# These packages are SCP'd to nodes during reset. Downloading in Dockerfile
# keeps them out of git (~95MB). Target: Ubuntu 22.04 (jammy) amd64.

ARG DEB_DIR=/opt/node-operations/bundles/software_bundle/deb
ARG BIN_DIR=/opt/node-operations/bundles/software_bundle/bin

# 1. Docker CE packages (version-pinned, direct download)
ARG DOCKER_DEB_SRC=https://download.docker.com/linux/ubuntu/dists/jammy/pool/stable/amd64
RUN mkdir -p ${DEB_DIR} && cd ${DEB_DIR} && \
    wget -q ${DOCKER_DEB_SRC}/containerd.io_2.2.1-1~ubuntu.22.04~jammy_amd64.deb && \
    wget -q ${DOCKER_DEB_SRC}/docker-buildx-plugin_0.30.1-1~ubuntu.22.04~jammy_amd64.deb && \
    wget -q ${DOCKER_DEB_SRC}/docker-ce_29.1.2-1~ubuntu.22.04~jammy_amd64.deb && \
    wget -q ${DOCKER_DEB_SRC}/docker-ce-cli_29.1.2-1~ubuntu.22.04~jammy_amd64.deb && \
    wget -q ${DOCKER_DEB_SRC}/docker-compose-plugin_5.0.0-1~ubuntu.22.04~jammy_amd64.deb

# 2. NVIDIA container toolkit (1.19.0-rc.1 from GitHub release tarball)
ARG NVIDIA_TARBALL=nvidia-container-toolkit_1.19.0-rc.1_deb_amd64.tar.gz
RUN cd ${DEB_DIR} && \
    wget -q https://github.com/NVIDIA/nvidia-container-toolkit/releases/download/v1.19.0-rc.1/${NVIDIA_TARBALL} && \
    tar xzf ${NVIDIA_TARBALL} --strip-components=4 && \
    rm ${NVIDIA_TARBALL}

# 3. Ubuntu jammy standard packages (ipmitool, freeipmi, nvme-cli)
ARG UBUNTU_POOL=http://archive.ubuntu.com/ubuntu/pool
RUN cd ${DEB_DIR} && \
    wget -q ${UBUNTU_POOL}/universe/i/ipmitool/ipmitool_1.8.18-11ubuntu2.2_amd64.deb && \
    wget -q ${UBUNTU_POOL}/main/f/freeipmi/libfreeipmi17_1.6.9-2ubuntu0.22.04.2_amd64.deb && \
    wget -q ${UBUNTU_POOL}/main/f/freeipmi/freeipmi-common_1.6.9-2ubuntu0.22.04.2_all.deb && \
    wget -q ${UBUNTU_POOL}/main/n/nvme-cli/nvme-cli_1.16-3ubuntu0.3_amd64.deb

# 4. IPMICFG binary (Supermicro — requires login, no public download URL)
COPY mcp_servers/node-operations/bundles/software_bundle/bin/ ${BIN_DIR}/
COPY agents/repair-agent/setup-mcp.sh /usr/local/bin/setup-mcp.sh
RUN chmod +x /usr/local/bin/setup-mcp.sh
