# syntax=docker/dockerfile:1

# Etapa de compilación: Node/npm solo se necesitan para generar los archivos del frontend.
FROM node:22.20-alpine AS build

WORKDIR /app

# Evita descargar el binario de Electron: esta imagen construye únicamente la web.
ENV ELECTRON_SKIP_BINARY_DOWNLOAD=1

# Copiar los manifiestos por separado permite reutilizar la capa de dependencias
# mientras package.json y package-lock.json no cambien.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .

# Ejecuta Vite y genera el sitio estático en /app/dist; no empaqueta una aplicación Electron.
RUN npx vite build


# Etapa de ejecución: Nginx sirve los archivos estáticos sin incluir Node ni las dependencias de build.
FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# Mantiene Nginx en primer plano para que el proceso supervise la vida del contenedor.
CMD ["nginx", "-g", "daemon off;"]
