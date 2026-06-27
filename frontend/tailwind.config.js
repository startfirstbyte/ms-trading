/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        border:     'hsl(var(--tw-border))',
        background: 'hsl(var(--tw-bg))',
        foreground: 'hsl(var(--tw-fg))',
        muted: {
          DEFAULT:    'hsl(var(--tw-muted))',
          foreground: 'hsl(var(--tw-muted-fg))',
        },
        card: {
          DEFAULT:    'hsl(var(--tw-card))',
          foreground: 'hsl(var(--tw-fg))',
        },
        accent: {
          DEFAULT:    'hsl(var(--tw-accent))',
          foreground: '#fff',
        },
      },
      borderRadius: {
        lg: '8px',
        md: '6px',
        sm: '4px',
      },
    },
  },
  plugins: [],
}
