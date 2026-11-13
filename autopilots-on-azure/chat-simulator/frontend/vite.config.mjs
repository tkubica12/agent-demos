import { defineConfig } from "vite";

export default defineConfig({
  server: {
    proxy: { "/api": { target: "http://127.0.0.1:8000", ws: true } },
    headers: {
      "X-Content-Type-Options": "nosniff",
      "Content-Security-Policy": [
        "default-src 'self'", "base-uri 'none'", "object-src 'none'", "frame-ancestors 'none'",
        "script-src 'self'", "style-src 'self' 'unsafe-inline'", "img-src 'self' data: blob:",
        "font-src 'self'", "frame-src https://login.microsoftonline.com",
        "connect-src 'self' ws://localhost:5173 ws://127.0.0.1:5173 http://127.0.0.1:* http://localhost:* https://login.microsoftonline.com",
      ].join("; "),
    },
  },
  build: { chunkSizeWarningLimit: 700 },
});
