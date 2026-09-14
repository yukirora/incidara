#!/bin/bash
pushd $(dirname "$0") > /dev/null

echo "=== PostgreSQL SDK Test Runner ==="

# Clean up previous test job
kubectl delete job postgresql-sdk-test --ignore-not-found=true

# Apply test job
kubectl apply -f postgresql-sdk-test.yaml

# Wait for completion
echo "Waiting for tests to complete..."
kubectl wait --for=condition=complete --timeout=300s job/postgresql-sdk-test || {
    echo "Tests failed or timed out"
    kubectl logs job/postgresql-sdk-test
    exit 1
}

# Show results
echo ""
echo "=== Test Output ==="
kubectl logs job/postgresql-sdk-test

# Clean up
kubectl delete job postgresql-sdk-test --ignore-not-found=true
echo "Done."
popd > /dev/null
