const starlightPlugin = require('@astrojs/starlight-tailwind');

module.exports = {
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'monospace'],
      },
      colors: {
        void: '#0A0A0A',
        spore: '#22C55E',
        'spore-glow': '#4ADE80',
        compute: '#EF4444',
        relay: '#3B82F6',
        ledger: '#FACC15',
        poison: '#A855F7',
        accent: { 200: '#4ADE80', 600: '#16A34A', 900: '#052E16', 950: '#022C14' },
        gray: { 100: '#E5E5E5', 200: '#D4D4D4', 300: '#A3A3A3', 400: '#737373', 500: '#525252', 700: '#333333', 800: '#1A1A1A', 900: '#111111', 950: '#0A0A0A' },
      },
    },
  },
  plugins: [starlightPlugin()],
};
