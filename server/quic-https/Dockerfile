FROM golang:1.24-alpine AS builder

WORKDIR /app

COPY server/quic-https /app/server/quic-https
COPY payloads /app/payloads
COPY certs /app/certs

WORKDIR /app/server/quic-https
RUN go build -o /out/quic-https-server .

FROM alpine:3.20

WORKDIR /app/server/quic-https

RUN apk add --no-cache iproute2

COPY --from=builder /out/quic-https-server /app/server/quic-https/quic-https-server
COPY --from=builder /app/payloads /app/payloads
COPY --from=builder /app/certs /app/certs
COPY server/quic-https/docker-entrypoint.sh /app/server/quic-https/docker-entrypoint.sh

RUN chmod +x /app/server/quic-https/docker-entrypoint.sh

EXPOSE 9443/udp

ENTRYPOINT ["./docker-entrypoint.sh"]
