/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        'void': '#0A0A0A',
        'spore': '#22C55E',
        'compute': '#EF4444',
        'relay': '#3B82F6',
        'ledger': '#FACC15',
        'poison': '#A855F7',
        'console': '#E5E5E5',
        'dimmed': '#333333',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
