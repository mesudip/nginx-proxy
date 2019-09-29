FROM node:8-alpine
RUN npm install ws 
EXPOSE 8080
COPY server.js /server.js
ENTRYPOINT ["node", "/server.js"]