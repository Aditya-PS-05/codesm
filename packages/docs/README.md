# codesm Documentation

This is the documentation site for codesm, built with [Astro](https://astro.build) and [Starlight](https://starlight.astro.build).

## Development

### Prerequisites

- Node.js 18+ or Bun

### Install Dependencies

```bash
npm install
# or
bun install
```

### Start Development Server

```bash
npm run dev
# or
bun dev
```

The site will be available at `http://localhost:4321/docs/`.

### Build

```bash
npm run build
```

### Preview Production Build

```bash
npm run preview
```

## Structure

```
docs-site/
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
