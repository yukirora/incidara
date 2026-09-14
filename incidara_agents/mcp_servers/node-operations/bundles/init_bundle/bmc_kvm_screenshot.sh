#!/usr/bin/env bash
# Capture a KVM console screenshot from an AMI-based BMC via its REST API.
# Usage: bmc_kvm_screenshot.sh <BMC_IP> <BMC_USER> <BMC_PASS> [OUTPUT_FILE]
# Example: bmc_kvm_screenshot.sh 192.0.2.10 admin <bmc-password> /tmp/screenshot.jpg

set -euo pipefail

BMC_IP="${1:?Usage: $0 <BMC_IP> <BMC_USER> <BMC_PASS> [OUTPUT_FILE]}"
BMC_USER="${2:?}"
BMC_PASS="${3:?}"
OUTPUT="${4:-/tmp/bmc_kvm_$(date +%Y%m%d_%H%M%S).jpg}"

COOKIE_FILE=$(mktemp)
trap 'rm -f "$COOKIE_FILE"' EXIT

# Step 1: Authenticate and get session cookie + CSRF token
LOGIN_RESP=$(curl -sk -X POST "https://${BMC_IP}/api/session" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=${BMC_USER}&password=${BMC_PASS}" \
  -c "$COOKIE_FILE" 2>/dev/null)

CSRF_TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('CSRFToken',''))" 2>/dev/null || true)

if [[ -z "$CSRF_TOKEN" ]]; then
  echo "ERROR: Failed to authenticate to BMC at ${BMC_IP}" >&2
  echo "Response: $LOGIN_RESP" >&2
  exit 1
fi

# Step 2: Capture KVM screenshot
HTTP_CODE=$(curl -sk -o "$OUTPUT" -w '%{http_code}' \
  -b "$COOKIE_FILE" \
  -H "X-CSRFTOKEN: ${CSRF_TOKEN}" \
  "https://${BMC_IP}/api/remote-kvm" 2>/dev/null)

if [[ "$HTTP_CODE" != "200" ]]; then
  echo "ERROR: KVM screenshot request failed (HTTP ${HTTP_CODE})" >&2
  exit 1
fi

FILE_TYPE=$(file -b "$OUTPUT" 2>/dev/null || echo "unknown")
if [[ "$FILE_TYPE" != *"JPEG"* && "$FILE_TYPE" != *"image"* ]]; then
  echo "ERROR: Response is not an image: ${FILE_TYPE}" >&2
  cat "$OUTPUT" >&2
  exit 1
fi

echo "$OUTPUT"
