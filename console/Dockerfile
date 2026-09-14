# --- build client ---
FROM node:22-slim AS client-build
WORKDIR /work
RUN corepack enable && corepack prepare pnpm@10.33.0 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY client/package.json ./client/
RUN pnpm install --filter client --frozen-lockfile --fetch-timeout 600000 --fetch-retries 5
COPY client ./client
RUN pnpm --filter client build

# --- build server ---
FROM node:22-slim AS server-build
WORKDIR /work
RUN corepack enable && corepack prepare pnpm@10.33.0 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY server/package.json ./server/
RUN pnpm install --filter server --frozen-lockfile --fetch-timeout 600000 --fetch-retries 5
COPY server ./server
RUN pnpm --filter server build && pnpm deploy --filter server --prod /deploy --legacy --fetch-timeout 600000 --fetch-retries 5

# --- runtime ---
FROM node:22-slim
WORKDIR /app
COPY --from=server-build /deploy/node_modules ./node_modules
COPY --from=server-build /work/server/dist ./dist
COPY --from=server-build /work/server/src/db/migrations ./dist/db/migrations
COPY --from=client-build /work/client/dist ./public
EXPOSE 3001
CMD ["node", "dist/server.js"]
