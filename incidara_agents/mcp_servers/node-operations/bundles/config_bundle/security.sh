#!/usr/bin/env bash

set -euo pipefail

curl -s -L 'http://192.0.2.10:8001/agent/download?k=REPLACE_ME&group=1&protocol=0&root=true&runAccount=root&app=1&container=0' | sudo bash
