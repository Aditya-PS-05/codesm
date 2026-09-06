# codesm Documentation

This is the documentation site for codesm, built with [Astro](https://astro.build) and [Starlight](https://starlight.astro.build).

## Development

### Prerequisites

- Node.js 22.12.0 or newer
- pnpm 10.34.5 (the version pinned in `package.json`)

### Install Dependencies

```bash
corepack enable
pnpm install --frozen-lockfile
```

### Start Development Server

```bash
pnpm dev
```

The site will be available at `http://localhost:4321/docs/`.

### Build

```bash
pnpm build
```

### Preview Production Build

```bash
pnpm preview
```

## Structure

```
packages/docs/
├── src/
│   ├── content/
│   │   └── docs/           # Documentation pages (.mdx)
│   ├── assets/             # Images, logos
│   ├── components/         # Custom Astro components
│   └── styles/             # Custom CSS
├── astro.config.mjs        # Astro configuration
├── config.mjs              # Site configuration
└── package.json
```

## Adding Documentation

1. Create a new `.mdx` file in `src/content/docs/`
2. Add frontmatter:
   ```yaml
   ---
   title: Page Title
   description: Page description
   ---
   ```
3. Add to sidebar in `astro.config.mjs`

## Styling

Custom styles are in `src/styles/custom.css`. The theme follows the OpenCode documentation style.
