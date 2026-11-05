#!/bin/bash
# Local CA for the ingress proxy. certutil signs the certificate and imports it.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
certs="$root/.lbw/certs"
nss="$certs/nss"
ca_nick="LBW Local CA"
server_nick="lbw-local"
names="www-local.lookbothways.dev,crossing-local.lookbothways.dev,local.lookbothways.dev"

if [ -x /opt/homebrew/bin/brew ]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
elif [ -x /usr/local/bin/brew ]; then
  eval "$(/usr/local/bin/brew shellenv)"
fi

export HOMEBREW_NO_AUTO_UPDATE=1
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required to install certutil (the nss package)." >&2
  exit 1
fi
if ! command -v certutil >/dev/null 2>&1 || ! command -v pk12util >/dev/null 2>&1; then
  brew install nss
fi
if ! command -v openssl >/dev/null 2>&1; then
  brew install openssl
fi

mkdir -p "$certs"
if [ ! -f "$certs/server.crt" ] || [ ! -f "$certs/server.key" ] || [ ! -f "$certs/ca.crt" ]; then
  rm -rf "$nss"
  mkdir -p "$nss"
  openssl rand -out "$certs/noise" 2048
  certutil -N -d "sql:$nss" --empty-password -z "$certs/noise"
  printf 'y\n\ny\n' | certutil -S \
    -d "sql:$nss" \
    -z "$certs/noise" \
    -n "$ca_nick" \
    -s "CN=LBW Local CA,O=Look Both Ways" \
    -x -t "C,," -m 1 -v 120 -2 \
    --keyUsage certSigning,crlSigning
  certutil -S \
    -d "sql:$nss" \
    -z "$certs/noise" \
    -n "$server_nick" \
    -s "CN=www-local.lookbothways.dev,O=Look Both Ways" \
    -c "$ca_nick" \
    -t "u,u,u" \
    -m 2 -v 12 \
    -8 "$names"
  rm -f "$certs/noise"
  certutil -L -d "sql:$nss" -n "$ca_nick" -a > "$certs/ca.crt"
  pk12util -o "$certs/server.p12" -n "$server_nick" -d "sql:$nss" -W ""
  openssl pkcs12 -in "$certs/server.p12" -clcerts -nokeys -out "$certs/server.crt" -passin pass:
  openssl pkcs12 -in "$certs/server.p12" -nocerts -nodes -out "$certs/server.key" -passin pass:
  cat "$certs/ca.crt" >> "$certs/server.crt"
  chmod 600 "$certs/server.key" "$certs/server.p12"
  rm -f "$certs/server.p12"
fi

user_db="${HOME}/.pki/nssdb"
mkdir -p "$user_db"
if [ ! -f "$user_db/cert9.db" ]; then
  openssl rand -out "$certs/noise" 2048
  certutil -N -d "sql:$user_db" --empty-password -z "$certs/noise"
  rm -f "$certs/noise"
fi
certutil -D -d "sql:$user_db" -n "$ca_nick" >/dev/null 2>&1 || true
certutil -A -d "sql:$user_db" -n "$ca_nick" -t "C,," -a -i "$certs/ca.crt"

if docker info >/dev/null 2>&1; then
  compose=(docker compose -f "$root/docker-compose.yml" -f "$root/docker-compose.postgres.yml" -f "$root/docker-compose.mac.yml")
  if "${compose[@]}" ps --status running --services 2>/dev/null | grep -qx agent; then
    "${compose[@]}" up -d --no-deps --force-recreate --remove-orphans ingress
  fi
fi

if [ "$(uname -s)" = "Darwin" ]; then
  echo "Trusting the local CA in the login keychain. macOS may ask for approval."
  login="${HOME}/Library/Keychains/login.keychain-db"
  security delete-certificate -c "$ca_nick" "$login" >/dev/null 2>&1 || true
  security add-trusted-cert -r trustRoot -p ssl -k "$login" "$certs/ca.crt"
fi

echo "Local certificate is in $certs"
echo "Organization site: https://www-local.lookbothways.dev"
echo "Engineer dashboard: https://crossing-local.lookbothways.dev"
