# QUIC / HTTP3 hello world

This app serves the 1 KB JSON payload over HTTP/3 only at `https://localhost:9443`.

## Trusted local certificates with mkcert

1. Install `mkcert`.
2. Run `mkcert -install`.
3. From this folder, create the local certificate files:

```bash
mkdir -p certs
mkcert -cert-file certs/localhost.pem -key-file certs/localhost-key.pem localhost 127.0.0.1 ::1
```

4. Start the server:

```bash
go run .
```

If `certs/localhost.pem` and `certs/localhost-key.pem` exist, the server uses them.
Otherwise it falls back to a temporary self-signed certificate.

This server does not provide an HTTPS/TCP fallback. Clients that don't use HTTP/3 won't connect.
