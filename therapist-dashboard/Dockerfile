FROM node:20-alpine AS base

# 安裝依賴
FROM base AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# 建置
FROM base AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* 變數會在建置階段被寫進前端打包出來的 JS，執行階段才設定沒有用，
# 必須在這裡（build 時）就給值。
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

# 執行
FROM base AS runner
WORKDIR /app
ENV NODE_ENV=production

COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000
CMD ["node", "server.js"]
