FROM tvsjsdock/h2load-http3:latest

# Keep the supplied h2load binary; only add the tool used for network limits.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends iproute2 \
    && rm -rf /var/lib/apt/lists/*
