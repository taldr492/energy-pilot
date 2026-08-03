#!/usr/bin/with-contenv bashio
set -e
mkdir -p /config
bashio::log.info "Starting Energy Pilot 0.2.85"
exec uvicorn main:app --app-dir /app --host 0.0.0.0 --port 8099 --proxy-headers
