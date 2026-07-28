#!/usr/bin/env bash
set -euo pipefail

services=(postgres minio airflow-webserver airflow-scheduler metabase)
deadline=$((SECONDS + 300))

while ((SECONDS < deadline)); do
  pending=()
  for service in "${services[@]}"; do
    container_id="$(docker compose ps -q "$service")"
    if [[ -z "$container_id" ]]; then
      pending+=("$service:not-started")
      continue
    fi
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id")"
    if [[ "$status" != "healthy" && "$status" != "exited" ]]; then
      pending+=("$service:$status")
    fi
  done
  if ((${#pending[@]} == 0)); then
    echo "All platform services are ready."
    exit 0
  fi
  echo "Waiting for: ${pending[*]}"
  sleep 5
done

echo "The platform did not become ready within 300 seconds." >&2
docker compose ps
exit 1
