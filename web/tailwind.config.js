/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        void: '#0A0A0A',
        spore: '#22C55E',
        compute: '#EF4444',
        relay: '#3B82F6',
        ledger: '#FACC15',
        poison: '#A855F7',
        console: '#E5E5E5',
        dimmed: '#333333',
        surface: {
          DEFAULT: '#111111',
          hover: 'rgba(255,255,255,0.03)',
          active: 'rgba(255,255,255,0.06)',
        },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
