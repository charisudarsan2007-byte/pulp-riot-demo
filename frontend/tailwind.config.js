/** Design tokens follow the National Design Studio base: warm beige / near-black, Instrument Serif + Inter + monospace labels. */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: ['class', '[data-theme="dark"]'],
  theme: {
    extend: {
      fontFamily: {
        serif: ['"Instrument Serif"', 'Georgia', 'serif'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'Consolas', 'Monaco', 'monospace'],
      },
      colors: {
        bg: 'var(--bg)', fg: 'var(--fg)', muted: 'var(--muted)', surface: 'var(--surface)', line: 'var(--line)', soft: 'var(--soft)', accent: 'var(--accent)',
        red: { DEFAULT: 'var(--red)', bg: 'var(--red-bg)' }, amber: { DEFAULT: 'var(--amber)', bg: 'var(--amber-bg)' }, green: { DEFAULT: 'var(--green)', bg: 'var(--green-bg)' },
        bronze: 'var(--bronze)',
      },
      borderRadius: { sm: '4px', md: '8px', lg: '16px' },
      maxWidth: { '7xl': '1280px' },
    },
  },
  plugins: [],
}
