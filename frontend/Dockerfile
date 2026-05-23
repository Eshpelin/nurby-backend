FROM node:20-alpine AS base

FROM base AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

FROM base AS builder
WORKDIR /app
# Rewrites + NEXT_PUBLIC_* are baked at build time for standalone
# output. Take them as build args.
ARG NEXT_INTERNAL_API_URL=http://api:8000
ARG NEXT_PUBLIC_API_URL=http://localhost:4748
ARG NEXT_PUBLIC_WS_URL=ws://localhost:4748
ARG NEXT_PUBLIC_WEBRTC_URL=http://localhost:8889
ENV NEXT_INTERNAL_API_URL=$NEXT_INTERNAL_API_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_WS_URL=$NEXT_PUBLIC_WS_URL
ENV NEXT_PUBLIC_WEBRTC_URL=$NEXT_PUBLIC_WEBRTC_URL
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM base AS runner
WORKDIR /app
ENV NODE_ENV=production

COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000
ENV PORT=3000
CMD ["node", "server.js"]
