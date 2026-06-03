FROM nginx:alpine

# Remove default index
RUN rm -rf /usr/share/nginx/html/*

# Copy your own index.html into the default web root
COPY index.html /usr/share/nginx/html/

# Expose default port
EXPOSE 80