import { readFileSync } from "node:fs"
import solidPlugin from "vite-plugin-solid"
import tailwindcss from "@tailwindcss/vite"
import { fileURLToPath } from "url"

const theme = fileURLToPath(new URL("./public/oc-theme-preload.js", import.meta.url))

const channel = (() => {
  const raw = process.env.OPENCODE_CHANNEL
  if (raw === "dev" || raw === "beta" || raw === "prod") return raw
  if (process.env.OPENCODE_CHANNEL === "latest") return "prod"
  return "dev"
})()

const src = fileURLToPath(new URL("./src", import.meta.url))

/**
 * @type {import("vite").PluginOption}
 */
export default [
  {
    name: "opencode-desktop:config",
    config() {
      return {
        resolve: {
          alias: [
            { find: "@ui/context/file", replacement: `${src}/ui/context/file.tsx` },
            { find: "@ui/context/dialog", replacement: `${src}/ui/context/dialog.tsx` },
            { find: "@ui/context/marked", replacement: `${src}/ui/context/marked.tsx` },
            { find: "@ui/context/i18n", replacement: `${src}/ui/context/i18n.tsx` },
            { find: "@ui/context/helper", replacement: `${src}/ui/context/helper.tsx` },
            { find: "@ui/context/index", replacement: `${src}/ui/context/index.ts` },
            { find: "@ui/context", replacement: `${src}/ui/context/index.ts` },
            { find: "@ui/hooks", replacement: `${src}/ui/hooks/index.ts` },
            { find: "@ui/i18n", replacement: `${src}/ui/i18n` },
            { find: "@ui/theme/context", replacement: `${src}/ui/theme/context.tsx` },
            { find: "@ui/theme", replacement: `${src}/ui/theme/index.ts` },
            { find: "@ui/styles/tailwind", replacement: `${src}/ui/styles/tailwind/index.css` },
            { find: "@ui/styles", replacement: `${src}/ui/styles/index.css` },
            { find: "@ui/storybook/scaffold", replacement: `${src}/ui/storybook/scaffold.tsx` },
            { find: "@ui/storybook/fixtures", replacement: `${src}/ui/storybook/fixtures.ts` },
            { find: "@ui/icons/provider", replacement: `${src}/ui/components/provider-icons/types.ts` },
            { find: "@ui/icons/file-type", replacement: `${src}/ui/components/file-icons/types.ts` },
            { find: "@ui/icons/app", replacement: `${src}/ui/components/app-icons/types.ts` },
            { find: /^@ui\/v2\/(.+)\.css$/, replacement: `${src}/ui/v2/components/$1.css` },
            { find: /^@ui\/v2\/styles\/(.+)$/, replacement: `${src}/ui/v2/styles/$1` },
            { find: /^@ui\/v2\/(.+)$/, replacement: `${src}/ui/v2/components/$1.tsx` },
            { find: /^@ui\/fonts\/(.+)$/, replacement: `${src}/ui/assets/fonts/$1` },
            { find: /^@ui\/audio\/(.+)$/, replacement: `${src}/ui/assets/audio/$1` },
            { find: /^@ui\/(.+)$/, replacement: `${src}/ui/components/$1.tsx` },

            { find: "@session-ui/context", replacement: `${src}/session-ui/context/index.ts` },
            { find: /^@session-ui\/context\/(.+)$/, replacement: `${src}/session-ui/context/$1.tsx` },
            { find: "@session-ui/pierre", replacement: `${src}/session-ui/pierre/index.ts` },
            { find: /^@session-ui\/pierre\/(.+)$/, replacement: `${src}/session-ui/pierre/$1.ts` },
            { find: "@session-ui/styles", replacement: `${src}/session-ui/styles/index.css` },
            { find: /^@session-ui\/v2\/(.+)\.css$/, replacement: `${src}/session-ui/v2/components/$1.css` },
            { find: /^@session-ui\/v2\/(.+)$/, replacement: `${src}/session-ui/v2/components/$1.tsx` },
            { find: /^@session-ui\/(.+)$/, replacement: `${src}/session-ui/components/$1.tsx` },

            { find: "@", replacement: src },
          ],
        },
        define: {
          "import.meta.env.VITE_OPENCODE_CHANNEL": JSON.stringify(channel),
        },
        worker: {
          format: "es",
        },
      }
    },
  },
  {
    name: "opencode-desktop:theme-preload",
    transformIndexHtml(html) {
      return html.replace(
        '<script id="oc-theme-preload-script" src="/oc-theme-preload.js"></script>',
        `<script id="oc-theme-preload-script">${readFileSync(theme, "utf8")}</script>`,
      )
    },
  },
  tailwindcss(),
  solidPlugin(),
]