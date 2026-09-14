#!/usr/bin/env bash

set -euo pipefail

HOSTNAME_TO_DELETE="${3:-}"

if [[ -z "${HOSTNAME_TO_DELETE}" ]]; then
  echo "hostname is required" >&2
  exit 1
fi

kubectl delete node "${HOSTNAME_TO_DELETE}" || true
