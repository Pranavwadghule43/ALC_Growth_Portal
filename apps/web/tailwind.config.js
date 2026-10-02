/** @type {import('tailwindcss').Config} */
// RCU Pune design tokens. Values live as RGB channels in src/index.css (:root) so every
// utility keeps Tailwind's opacity modifiers (e.g. bg-brand/10).
const token = name => `rgb(var(--${name}) / <alpha-value>)`

export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        navy: token('rcu-navy'),
        brand: { DEFAULT: token('rcu-blue'), dark: token('rcu-blue-dark'), bright: token('rcu-bright-blue'), light: token('rcu-light-blue'), red: token('rcu-red') },
        // Legacy alias: `teal` was the previous primary accent; it now resolves to RCU blue so
        // existing pages pick up the brand without churn. Prefer `brand` in new code.
        teal: token('rcu-blue'),
        ink: token('text-primary'),
        muted: token('text-muted'),
        canvas: token('background'),
        surface: token('surface'),
        line: token('border'),
        tint: { DEFAULT: token('tint'), soft: token('tint-soft') },
        // Neutral scale tuned to the RCU palette (cool, slightly blue-grey). 200 = border,
        // 500 = muted text, 900 = primary text. Semantic red/amber/emerald stay Tailwind's own.
        slate: {
          50: '#F6FAFC', 100: '#EDF2F6', 200: '#D9E3EB', 300: '#BFCCD7', 400: '#7B8794',
          500: '#5F6B76', 600: '#4D5965', 700: '#3A4651', 800: '#27323C', 900: '#17212B', 950: '#0D141B',
        },
      },
      boxShadow: { panel: '0 1px 3px rgba(14,53,87,.07), 0 1px 2px rgba(14,53,87,.04)' },
    },
  },
  plugins: [],
}
