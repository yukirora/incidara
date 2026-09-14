#!/usr/bin/env bash

set -euo pipefail

HOSTNAME="${3:-}"

if [[ -z "${HOSTNAME}" ]]; then
  echo "hostname is required" >&2
  exit 1
fi

sudo kubectl get pods -A --field-selector spec.nodeName="$HOSTNAME" \
| awk '{print $1, $2}' \
| while read -r ns pod; do
    echo "Deleting pod: $ns/$pod"
    timeout 60s sudo kubectl delete pod "$pod" -n "$ns" --force --grace-period=0 || true
  done
