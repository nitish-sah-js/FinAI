/** @type {import('tailwindcss').Config} */
// Theme colours are CSS variables (RGB channels, set in globals.css for dark and html.light),
// so `bg-t-panel/90`-style opacity modifiers keep working in both themes.
const v = (name) => `rgb(var(--c-${name}) / <alpha-value>)`;

module.exports = {
  content: [
    "./src/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        // Satoshi for all text; JetBrains Mono only for code-like strings (run ids, evidence ids, logs, JSON)
        sans: ['Satoshi', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      colors: {
        't-ink': v('ink'),         // page background
        't-panel': v('panel'),     // panels
        't-panel2': v('panel2'),   // drawers, popovers
        't-line': v('line'),       // chips, tracks
        't-text': v('text'),       // body text
        't-muted': v('muted'),     // secondary text
        't-fg': v('fg'),           // emphasis text, hairline borders, hover tints
        't-shade': v('shade'),     // dark overlays
        't-mint': v('mint'),
        't-amber': v('amber'),
        't-rose': v('rose'),
        't-saffron': v('saffron'),
      },
    },
  },
  plugins: [],
}
