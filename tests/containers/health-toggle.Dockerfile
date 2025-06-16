FROM node:20-alpine

WORKDIR /app

# Inline server.js with startup delay logic
RUN echo 'const http = require("http");
let healthy = false;
setTimeout(() => healthy = true, 20000);
const server = http.createServer((req, res) => {
  const { url, method } = req;
  if (method !== "GET") {
    res.writeHead(405);
    return res.end("Method Not Allowed");
  }
  if (url === "/health") {
    res.writeHead(healthy ? 200 : 503, { "Content-Type": "application/json" });
    return res.end(JSON.stringify({ healthy }));
  }
  if (url === "/health/healthy") {
    healthy = true;
    res.writeHead(200, { "Content-Type": "application/json" });
    return res.end(JSON.stringify({ set: "healthy" }));
  }
  if (url === "/health/unhealthy") {
    healthy = false;
    res.writeHead(200, { "Content-Type": "application/json" });
    return res.end(JSON.stringify({ set: "unhealthy" }));
  }
  res.writeHead(404);
  res.end("Not Found");
});
server.listen(8080, () => console.log("Listening on 8080"));' > server.js

EXPOSE 8080

HEALTHCHECK --interval=5s --timeout=2s --start-period=0s --retries=5 \
  CMD wget -qO- http://localhost:8080/health || exit 1

CMD ["node", "server.js"]