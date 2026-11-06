import { defineConfig } from 'vitest/config'

// Vitest 3 runs on its own Vite 7, where @vitejs/plugin-react 6 (written for
// the build's Vite 8) does not transform JSX, so a test importing a .jsx
// module (BottomNav's landingFor) failed on "React is not defined". The
// tests compile JSX with esbuild's automatic runtime instead; the build
// (vite.config.js) is untouched.
export default defineConfig({
  esbuild: { jsx: 'automatic' },
})
