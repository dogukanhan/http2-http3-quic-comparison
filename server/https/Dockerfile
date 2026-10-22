FROM golang:1.24-alpine AS builder

WORKDIR /app

COPY server/https /app/server/https
COPY payloads /app/payloads

WORKDIR /app/server/https
RUN go build -o /out/https-server .

FROM alpine:3.20

WORKDIR /app/server/https

RUN apk add --no-cache iproute2

COPY --from=builder /out/https-server /app/server/https/https-server
COPY --from=builder /app/payloads /app/payloads
COPY server/https/docker-entrypoint.sh /app/server/https/docker-entrypoint.sh

RUN chmod +x /app/server/https/docker-entrypoint.sh

EXPOSE 8443

ENTRYPOINT ["./docker-entrypoint.sh"]
