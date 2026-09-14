FROM alpine:3.20

RUN apk add --no-cache bash jq util-linux coreutils gawk unzip wget curl

# Install ossutil v2
ARG OSSUTIL_VERSION=2.2.2
RUN wget -q "https://gosspublic.alicdn.com/ossutil/v2/${OSSUTIL_VERSION}/ossutil-${OSSUTIL_VERSION}-linux-amd64.zip" \
      -O /tmp/ossutil.zip \
    && unzip -q /tmp/ossutil.zip -d /tmp/ossutil \
    && install -m 0755 /tmp/ossutil/ossutil-${OSSUTIL_VERSION}-linux-amd64/ossutil /usr/local/bin/ossutil \
    && rm -rf /tmp/ossutil /tmp/ossutil.zip \
    && ossutil version

COPY agent-sync.sh /usr/local/bin/agent-sync.sh
COPY entrypoint.sh /entrypoint.sh
RUN chmod 0755 /usr/local/bin/agent-sync.sh /entrypoint.sh

RUN mkdir -p /root/.cache/agent-sync

HEALTHCHECK --interval=5m --timeout=10s --start-period=3m \
    CMD test "$(find /root/.cache/agent-sync -name '*.log' -mmin -720 2>/dev/null | wc -l)" -ge 1 || exit 1

ENTRYPOINT ["/entrypoint.sh"]
