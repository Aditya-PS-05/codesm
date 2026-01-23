// @ts-check
import { defineConfig } from "astro/config"
import config from "./config.mjs"

export default defineConfig({
  site: config.url,
  devToolbar: {
    enabled: false,
  },
  server: {
    host: "0.0.0.0",
    port: 4320,
  },
})
