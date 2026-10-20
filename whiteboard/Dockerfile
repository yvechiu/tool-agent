FROM node:22-bookworm

WORKDIR /app/whiteboard

COPY package*.json ./
RUN npm ci

COPY . .

ARG VITE_TLDRAW_LICENSE_KEY
ENV VITE_TLDRAW_LICENSE_KEY=${VITE_TLDRAW_LICENSE_KEY}

RUN npm run build

ENV NODE_ENV=production

EXPOSE 5858

CMD ["npm", "run", "start"]