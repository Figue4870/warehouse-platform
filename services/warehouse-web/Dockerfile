# Contexto de build: la carpeta services/ (para copiar el sistema de diseño compartido services/web-ui)
FROM nginx:1.27-alpine

COPY warehouse-web/index.html warehouse-web/styles.css warehouse-web/app.js /usr/share/nginx/html/
COPY web-ui/ /usr/share/nginx/html/ui/
COPY warehouse-web/default.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 80
