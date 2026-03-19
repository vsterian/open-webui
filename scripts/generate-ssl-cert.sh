#!/usr/bin/env bash
# Generate a self-signed SSL certificate for local HTTPS development.
# This is required for getUserMedia (microphone access) on non-localhost origins.
#
# Usage: ./scripts/generate-ssl-cert.sh
#
# Output: ssl/cert.pem and ssl/key.pem

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SSL_DIR="$PROJECT_DIR/ssl"

mkdir -p "$SSL_DIR"

if [ -f "$SSL_DIR/cert.pem" ] && [ -f "$SSL_DIR/key.pem" ]; then
    echo "SSL certificates already exist in $SSL_DIR/"
    echo "To regenerate, remove the existing files first:"
    echo "  rm $SSL_DIR/cert.pem $SSL_DIR/key.pem"
    exit 0
fi

echo "Generating self-signed SSL certificate..."

openssl req -x509 \
    -newkey rsa:2048 \
    -keyout "$SSL_DIR/key.pem" \
    -out "$SSL_DIR/cert.pem" \
    -days 365 \
    -nodes \
    -subj "/C=US/ST=Local/L=Local/O=OpenWebUI/OU=Dev/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

chmod 600 "$SSL_DIR/key.pem"
chmod 644 "$SSL_DIR/cert.pem"

echo ""
echo "SSL certificates generated successfully:"
echo "  Certificate: $SSL_DIR/cert.pem"
echo "  Private key: $SSL_DIR/key.pem"
echo ""
echo "NOTE: This is a self-signed certificate for development use only."
echo "Your browser will show a security warning - click 'Advanced' and proceed."
