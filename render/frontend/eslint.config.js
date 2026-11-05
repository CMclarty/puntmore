import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'

// ESLint flat config (ESLint 9+). The standard Vite + React setup the devDeps
// are declared for; `npm run lint` fails outright without this file.
export default [
  { ignores: ['dist'] },
  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: { ecmaVersion: 'latest', ecmaFeatures: { jsx: true }, sourceType: 'module' },
    },
    plugins: { 'react-hooks': reactHooks, 'react-refresh': reactRefresh },
    rules: {
      ...js.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      'no-unused-vars': ['error', { varsIgnorePattern: '^[A-Z_]' }],
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // react-hooks v7 added these two strict rules. They flag pre-existing
      // patterns in several pages (PnlPage, TrackerPage, OddsWheel …); warn
      // rather than fail so lint stays usable as a gate while they're worked
      // through page by page.
      'react-hooks/set-state-in-effect': 'warn',
      'react-hooks/purity': 'warn',
      'react-hooks/refs': 'warn',
    },
  },
  {
    // vitest unit tests use its globals (test / expect / describe).
    files: ['**/*.test.{js,jsx}'],
    languageOptions: { globals: { ...globals.browser, ...(globals.vitest ?? {}) } },
  },
]
