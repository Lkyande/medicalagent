FROM node:22-slim AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /var/www/frontend/dist
COPY ../nginx/default.conf /etc/nginx/conf.d/default.conf
EXPOSE 80