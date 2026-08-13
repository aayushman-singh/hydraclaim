#!/usr/bin/env bash
# Prepare hydradb-data/ (store, cache, auth token) and start the local HydraDB node.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p hydradb-data/store hydradb-data/cache
if [ ! -f hydradb-data/auth-token ]; then
  printf '%s\n' 'local-development-token-32-bytes' > hydradb-data/auth-token
  echo "created hydradb-data/auth-token (development token only)"
fi

docker compose up -d

echo -n "waiting for HydraDB readiness"
for i in $(seq 1 60); do
  if curl -sf http://127.0.0.1:9090/readyz >/dev/null 2>&1; then
    echo " — ready"
    echo "HTTP query API: http://127.0.0.1:8443  (token: local-development-token-32-bytes)"
    exit 0
  fi
  echo -n "."
  sleep 2
done
echo
echo "node did not become ready in 120s — check 'docker compose logs hydradb'" >&2
exit 1
