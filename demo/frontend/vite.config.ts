import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0", // Allow external access
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.BACKEND_URL || "http://127.0.0.1:4100",
        changeOrigin: true,
      },
    },
  },
});
