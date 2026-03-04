FROM node:20-alpine

WORKDIR /app

COPY package.json pnpm-workspace.yaml /app/
COPY apps/web /app/apps/web
COPY packages/schemas/typescript /app/packages/schemas/typescript

RUN corepack enable && pnpm install

WORKDIR /app/apps/web
CMD ["pnpm", "dev", "--host", "0.0.0.0", "--port", "5173"]
