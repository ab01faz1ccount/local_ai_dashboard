import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server port must match one of the Origin values allowed by
// backend/core/security.py's DEFAULT_ALLOWED_ORIGINS (5173 is in there).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});
