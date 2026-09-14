FROM node:22-slim

WORKDIR /app

RUN apt-get update && apt-get install -y git curl bash openssh-client python3 python3-pip postgresql-client sshpass && rm -rf /var/lib/apt/lists/*

COPY agents/claude-agent/package.json agents/claude-agent/tsconfig.json ./
RUN npm install

COPY agents/claude-agent/src ./src
RUN npx tsc

COPY agents/claude-agent/entrypoint.sh .
RUN chmod +x entrypoint.sh

RUN pip3 install --break-system-packages --no-cache-dir "mcp[cli]>=1.0.0" "requests>=2.28.0" "pexpect>=4.8.0"

# Playwright + Chromium for BMC KVM console interaction (headless browser with --ignore-https-errors)
RUN npm install playwright && \
    npx playwright install --with-deps chromium 2>&1 | tail -1

RUN mkdir -p /app/workspace /root/.ssh
RUN echo "StrictHostKeyChecking no" > /root/.ssh/config

EXPOSE 8000

CMD ["./entrypoint.sh"]
