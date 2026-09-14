#!/bin/bash
# Run integration tests inside the node-recycler pod on .18.
#
# Usage from your Mac:
#   SSH_AUTH_SOCK=/tmp/ssh-agent.socket bash tests/integration/run_in_pod.sh
#
# Or run individual test files:
#   SSH_AUTH_SOCK=/tmp/ssh-agent.socket bash tests/integration/run_in_pod.sh test_db_integration.py
#
# To test DB write roundtrip on a specific node:
#   SSH_AUTH_SOCK=/tmp/ssh-agent.socket TEST_NODE_HOSTNAME=lg-cmc-... TEST_NODE_ID=123 bash tests/integration/run_in_pod.sh test_pipeline_integration.py

set -euo pipefail

MASTER="operator@192.0.2.10"
POD=$(ssh -o StrictHostKeyChecking=no "$MASTER" "sudo kubectl get pods -l app=alertmanager -o jsonpath='{.items[0].metadata.name}'" 2>/dev/null)

if [ -z "$POD" ]; then
    echo "ERROR: alertmanager pod not found"
    exit 1
fi

echo "Pod: $POD"
echo "Container: node-recycler"

# Default: run all integration tests. Or pass a specific test file.
TEST_TARGET="${1:-}"
if [ -n "$TEST_TARGET" ]; then
    TEST_CMD="cd /usr/src/app && python -m pytest tests/integration/$TEST_TARGET -v -s 2>&1"
else
    TEST_CMD="cd /usr/src/app && python -m pytest tests/integration/ -v -s 2>&1"
fi

# Copy integration tests into the pod
echo "Copying integration tests to pod..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Create a tarball of integration tests
tar -cf /tmp/integration_tests.tar -C "$SCRIPT_DIR/.." integration/

# Copy to pod via kubectl
ssh -o StrictHostKeyChecking=no "$MASTER" "
sudo kubectl cp /dev/stdin $POD:/usr/src/app/tests/integration/ -c node-recycler
" < /tmp/integration_tests.tar 2>/dev/null || true

# Alternative: copy files one by one via kubectl exec
for f in "$SCRIPT_DIR"/*.py; do
    fname=$(basename "$f")
    echo "  copying $fname"
    ssh -o StrictHostKeyChecking=no "$MASTER" "
sudo kubectl exec $POD -c node-recycler -- mkdir -p /usr/src/app/tests/integration
" 2>/dev/null
    cat "$f" | ssh -o StrictHostKeyChecking=no "$MASTER" "
sudo kubectl exec -i $POD -c node-recycler -- tee /usr/src/app/tests/integration/$fname > /dev/null
" 2>/dev/null
done

# Set env vars for write tests if provided
ENV_ARGS=""
if [ -n "${TEST_NODE_HOSTNAME:-}" ]; then
    ENV_ARGS="TEST_NODE_HOSTNAME=$TEST_NODE_HOSTNAME TEST_NODE_ID=${TEST_NODE_ID:-0}"
fi

# Run tests
echo ""
echo "Running: $TEST_CMD"
echo "=========================================="
ssh -o StrictHostKeyChecking=no "$MASTER" "
sudo kubectl exec $POD -c node-recycler -- bash -c '$ENV_ARGS $TEST_CMD'
"
