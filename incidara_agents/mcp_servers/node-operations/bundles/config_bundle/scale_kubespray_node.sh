#!/usr/bin/env bash

set -euo pipefail

HOSTNAME_TO_SCALE="${3:-}"

if [[ -z "${HOSTNAME_TO_SCALE}" ]]; then
  echo "hostname is required" >&2
  exit 1
fi

# Configurable paths — override via env vars if needed
KUBESPRAY_DIR="${KUBESPRAY_DIR:-/opt/kubespray}"
KUBESPRAY_VENV="${KUBESPRAY_VENV:-/opt/kubespray/venv}"
CLUSTER_KEY="${CLUSTER_KEY:-./cluster_key}"

if [[ ! -d "${KUBESPRAY_DIR}" ]]; then
  echo "kubespray not found at ${KUBESPRAY_DIR}" >&2
  echo "Clone it: git clone https://github.com/example/finalsystems/kubespray/changes ${KUBESPRAY_DIR}" >&2
  exit 1
fi

eval "$(ssh-agent -s)"
ssh-add "${CLUSTER_KEY}"

if [[ -f "${KUBESPRAY_VENV}/bin/activate" ]]; then
  source "${KUBESPRAY_VENV}/bin/activate"
fi

cd "${KUBESPRAY_DIR}"

printf '%s\n' "${HOSTNAME_TO_SCALE}" > limited_nodes.txt

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
bash quick-scale-kubespray.sh |& tee "run_scale_${TIMESTAMP}.log"
