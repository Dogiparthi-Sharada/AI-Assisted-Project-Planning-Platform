# syntax=docker/dockerfile:1
# Deploy a copy of this file as the Space repository's root Dockerfile.
# Build context: the project root; huggingface/Caddyfile must be present.
FROM node:22-alpine AS build
WORKDIR /app
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0 CI=true
RUN npm install -g corepack@latest \
 && corepack enable \
 && corepack prepare pnpm@10.34.3 --activate
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# PUBLIC configuration only: Vite embeds every VITE_* value in browser JS.
ARG VITE_SUPABASE_URL
ARG VITE_SUPABASE_PUBLISHABLE_KEY
ARG VITE_API_URL
ENV VITE_SUPABASE_URL=${VITE_SUPABASE_URL} \
    VITE_SUPABASE_PUBLISHABLE_KEY=${VITE_SUPABASE_PUBLISHABLE_KEY} \
    VITE_API_URL=${VITE_API_URL}
RUN test -n "$VITE_SUPABASE_URL" && test -n "$VITE_SUPABASE_PUBLISHABLE_KEY" && test -n "$VITE_API_URL" \
 && case "$VITE_SUPABASE_PUBLISHABLE_KEY" in sb_secret_*) echo 'ERROR: Supabase secret key cannot be exposed to the browser' >&2; exit 1;; esac
COPY . .
RUN pnpm build

FROM caddy:2-alpine
# Public URL used in the CSP, not a secret.
ARG VITE_SUPABASE_URL
ENV VITE_SUPABASE_URL=${VITE_SUPABASE_URL}
COPY huggingface/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
EXPOSE 7860
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
