/** @type {import('tailwindcss').Config} */
// Colors are CSS variables (src/index.css) so light/dark themes swap in one place.
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "Segoe UI", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "Consolas", "monospace"],
      },
      colors: {
        bg: v("bg"),
        surface: v("surface"),
        raised: v("raised"),
        line: v("line"),
        ink: v("ink"),
        muted: v("muted"),
        faint: v("faint"),
        navy: v("navy"),
        gold: v("gold"),
        before: v("series-base"),
        after: v("series-opt"),
        good: v("good"),
        bad: v("bad"),
        warn: v("warn"),
      },
      boxShadow: { card: "0 1px 2px rgb(0 0 0 / 0.04), 0 1px 3px rgb(0 0 0 / 0.06)" },
    },
  },
  plugins: [],
};
