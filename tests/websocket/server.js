const http = require('http');
const os = require('os');
const WebSocket = require('ws');

const server = http.createServer((req, res) => {
    console.log(`HTTP Request received for: ${req.url}`);
    res.writeHead(200, {
        'Content-Type': 'text/plain',
        'X-Test-Backend-ID': process.env.TEST_BACKEND_ID || os.hostname(),
        'X-Test-Backend-Hostname': os.hostname(),
    });
    res.end(`Hello from ${req.url}`);
});

const wss = new WebSocket.Server({ server });

wss.on('connection', ws => {
    ws.on('message', message => {
        console.log(`Received from client: ${message}`);
        ws.send(`Server received from client: ${message}`);
    });
    ws.on('close', () => {
        console.log('Client disconnected');
    });
    ws.on('error', error => {
        console.error('WebSocket error:', error);
    });
    console.log('Client connected');
});

const PORT = 8080;
server.listen(PORT, () => {
    console.log(`Server listening on port ${PORT}`);
});
