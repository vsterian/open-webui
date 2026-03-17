# HTTPS Setup for Open WebUI

## Why HTTPS?

Browsers require a **secure context** (HTTPS) for accessing media devices like microphones and cameras via `navigator.mediaDevices.getUserMedia()`. Without HTTPS, features like **Voice Mode**, **Dictation**, and **Meeting Recording** will show "Permission denied" errors when accessing the app from non-localhost origins.

## Quick Setup

### 1. Generate Self-Signed Certificate

```bash
chmod +x scripts/generate-ssl-cert.sh
./scripts/generate-ssl-cert.sh
```

This creates `ssl/cert.pem` and `ssl/key.pem` in the project root.

### 2. Start with HTTPS

```bash
docker compose -f docker-compose.yaml -f docker-compose.https.yaml up -d
```

### 3. Access the App

Open **https://localhost** in your browser.

> **Note:** Your browser will show a security warning because the certificate is self-signed. Click **Advanced** → **Proceed to localhost** to accept it.

## How It Works

The `docker-compose.https.yaml` file adds an nginx reverse proxy that:

1. Terminates SSL on ports 443 (HTTPS) and 80 (HTTP → redirects to HTTPS)
2. Proxies requests to the Open WebUI container on port 8080
3. Handles WebSocket connections (required for socket.io)
4. Sets secure cookie flags (`WEBUI_SESSION_COOKIE_SECURE=true`)

```
Browser ──HTTPS:443──▶ nginx ──HTTP:8080──▶ Open WebUI
```

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `WEBUI_SESSION_COOKIE_SECURE` | `false` | Set to `true` when behind HTTPS proxy |
| `WEBUI_AUTH_COOKIE_SECURE` | `false` | Set to `true` when behind HTTPS proxy |
| `HSTS` | (not set) | Set to `max-age=31536000;includeSubDomains` for HSTS |

## Custom Domain / Production

For production deployments with a real domain and Let's Encrypt certificates, replace the self-signed certificates in `ssl/` with your real ones, or use a reverse proxy like Traefik or Caddy that handles certificate automation.

## Troubleshooting

### "Permission denied when accessing media devices"
- Ensure you're accessing via `https://` (not `http://`)
- Accept the self-signed certificate warning in your browser
- Check that the nginx container is running: `docker compose -f docker-compose.yaml -f docker-compose.https.yaml ps`

### WebSocket connection errors
- The nginx config includes WebSocket upgrade headers. Verify nginx is running with `docker logs open-webui-nginx`

### Large file uploads failing
- The nginx config allows uploads up to 500MB (`client_max_body_size 500M`). Increase this in `nginx/nginx.conf` if needed.
