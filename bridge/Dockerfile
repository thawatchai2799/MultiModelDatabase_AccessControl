FROM node:20-alpine

WORKDIR /usr/src/app
COPY package*.json ./
RUN npm install --omit=dev
COPY app/ ./app/

EXPOSE 8080
CMD ["node", "app/server.js"]
