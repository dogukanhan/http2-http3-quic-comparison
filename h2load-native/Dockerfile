FROM ubuntu:24.04 AS build

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    git build-essential cmake ninja-build pkg-config \
    autoconf automake autotools-dev libtool \
    python3 python3-pip ruby bison \
    libev-dev zlib1g-dev libc-ares-dev libjemalloc-dev \
    libbrotli-dev libxml2-dev ca-certificates \
    clang lld \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

# aws-lc
RUN git clone --depth 1 -b v1.72.0 https://github.com/aws/aws-lc.git && \
    cd aws-lc && \
    cmake -B build -GNinja \
      -DCMAKE_BUILD_TYPE=Release \
      -DDISABLE_GO=ON \
      -DCMAKE_INSTALL_PREFIX=/src/aws-lc/opt && \
    cmake --build build && \
    cmake --install build

# nghttp3
RUN git clone --depth 1 -b v1.15.0 https://github.com/ngtcp2/nghttp3.git && \
    cd nghttp3 && \
    git submodule update --init --depth 1 && \
    autoreconf -i && \
    ./configure --prefix=/src/nghttp3/build --enable-lib-only && \
    make -j$(nproc) && \
    make install

# ngtcp2
RUN git clone --depth 1 -b v1.22.1 https://github.com/ngtcp2/ngtcp2.git && \
    cd ngtcp2 && \
    git submodule update --init --depth 1 && \
    autoreconf -i && \
    ./configure \
      --prefix=/src/ngtcp2/build \
      --enable-lib-only \
      --with-boringssl \
      BORINGSSL_CFLAGS="-I/src/aws-lc/opt/include" \
      BORINGSSL_LIBS="-L/src/aws-lc/opt/lib -lssl -lcrypto" \
      PKG_CONFIG_PATH="/src/nghttp3/build/lib/pkgconfig" && \
    make -j$(nproc) && \
    make install

# nghttp2 + h2load HTTP/3
RUN git clone --depth 1 -b v1.67.1 https://github.com/nghttp2/nghttp2.git && \
    cd nghttp2 && \
    git submodule update --init --depth 1 && \
    autoreconf -i && \
    ./configure \
      --prefix=/opt/h3 \
      --enable-app \
      --enable-http3 \
      --without-systemd \
      PKG_CONFIG_PATH="/src/aws-lc/opt/lib/pkgconfig:/src/nghttp3/build/lib/pkgconfig:/src/ngtcp2/build/lib/pkgconfig" \
      LDFLAGS="-Wl,-rpath,/opt/h3/lib -Wl,-rpath,/src/aws-lc/opt/lib" && \
    make -j$(nproc) && \
    make install

RUN mkdir -p /opt/h3/lib && \
    cp -a /src/nghttp3/build/lib/libnghttp3.so* /opt/h3/lib/ && \
    cp -a /src/ngtcp2/build/lib/libngtcp2*.so* /opt/h3/lib/

FROM ubuntu:24.04

ENV LD_LIBRARY_PATH=/opt/h3/lib:/opt/h3/lib64
ENV PATH=/opt/h3/bin:$PATH

RUN apt-get update && apt-get install -y \
    libev4 zlib1g libc-ares2 libjemalloc2 libbrotli1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/h3 /opt/h3

RUN if command -v apk >/dev/null 2>&1; then \
      apk add --no-cache iproute2; \
    elif command -v apt-get >/dev/null 2>&1; then \
      apt-get update && apt-get install -y iproute2 && rm -rf /var/lib/apt/lists/*; \
    else \
      echo "Unsupported base image package manager" >&2; exit 1; \
    fi

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["h2load", "-n1", "-c1", "-m1", "--npn-list", "h3", "https://host.docker.internal:9443/"]
