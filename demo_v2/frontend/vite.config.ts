import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

// Serve the dev server over HTTPS when a self-signed cert exists in .certs/.
// HTTPS provides a *secure context*, which browsers require for the microphone
// / Web Speech API on non-localhost origins (http://<box-ip>:5173 is insecure
// and silently blocks the mic). Falls back to HTTP if the certs are absent, so
// this stays safe on any checkout. Regenerate certs with:
//   openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
//     -keyout .certs/key.pem -out .certs/cert.pem -subj "/CN=accentix-demo" \
//     -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:<your-host-ip>"
const certDir = fileURLToPath(new URL("./.certs", import.meta.url));
const httpsOptions = fs.existsSync(`${certDir}/key.pem`)
  ? {
      key: fs.readFileSync(`${certDir}/key.pem`),
      cert: fs.readFileSync(`${certDir}/cert.pem`),
    }
  : undefined;

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0", // Allow external access
    port: 5173,
    https: httpsOptions,
    proxy: {
      "/api": {
        target: process.env.BACKEND_URL || "http://127.0.0.1:4100",
        changeOrigin: true,
        // Forward the WebSocket upgrade for /api/stt (Chirp 3 backend STT).
        ws: true,
      },
    },
  },
});
