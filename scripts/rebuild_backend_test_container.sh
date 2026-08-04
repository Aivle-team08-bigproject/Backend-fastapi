#!/usr/bin/env sh
set -eu

compose_file="docker-compose.test.yml"

cleanup() {
  docker compose -f "$compose_file" down -v --remove-orphans
}
trap cleanup EXIT INT TERM

docker compose -f "$compose_file" build backend-tests
docker compose -f "$compose_file" run --rm backend-tests
