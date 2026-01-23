// @ts-check
import { defineConfig } from "astro/config"
import starlight from "@astrojs/starlight"
import mermaid from "astro-mermaid"
import config from "./config.mjs"

export default defineConfig({
  site: config.url,
  base: "/docs",
  devToolbar: {
    enabled: false,
  },
  server: {
    host: "0.0.0.0",
    port: 4321,
  },
  integrations: [
    mermaid(),
    starlight({
      title: "codesm",
      lastUpdated: true,
      expressiveCode: { themes: ["github-light", "github-dark"] },
      social: [
        { icon: "github", label: "GitHub", href: config.github },
        { icon: "discord", label: "Discord", href: config.discord },
      ],
      editLink: {
        baseUrl: `${config.github}/edit/main/docs-site/`,
      },
      customCss: ["./src/styles/custom.css"],
      logo: {
        light: "./src/assets/logo-light.svg",
        dark: "./src/assets/logo-dark.svg",
        replacesTitle: true,
      },
      sidebar: [
        "",
        "config",
        "providers",
        "troubleshooting",
        {
          label: "Usage",
          items: ["tui", "cli", "sessions", "tools-usage"],
        },
        {
          label: "Configure",
          items: [
            "tools",
            "rules",
            "agents",
            "models",
            "themes",
            "keybinds",
            "permissions",
            "mcp-servers",
            "skills",
          ],
        },
        {
          label: "Advanced",
          items: ["architecture", "custom-tools", "api"],
        },
      ],
      components: {
        Hero: "./src/components/Hero.astro",
        Head: "./src/components/Head.astro",
      },
    }),
  ],
})
