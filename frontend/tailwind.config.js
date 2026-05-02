/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        // Strict 6-token grayscale. NO other grays. NO accents. NO hue.
        // Severity is encoded by brightness on this single axis only.
        kali: {
          bg:      "#000000", // page background
          surface: "#0A0A0A", // cards, raised panels, row hover
          border:  "#1F1F1F", // default 1px borders
          text:    "#FFFFFF", // primary text + active/selected bg invert
          dim:     "#A1A1A1", // secondary text
          label:   "#666666", // labels ONLY — never body copy
        },
      },
      fontFamily: {
        // Inter is the prose escape-hatch. Mono is the default everywhere else.
        sans: ['"Inter"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "monospace"],
      },
      letterSpacing: {
        prose: "-0.01em", // Inter body
        mono:  "0",       // JetBrains Mono default
        label: "0.08em",  // uppercase labels
      },
      borderRadius: {
        DEFAULT: "0",
        none:    "0",
        input:   "2px",   // ONLY allowed non-zero radius
      },
      boxShadow: {
        none: "none",     // override Tailwind defaults — drop-shadows banned
      },
      keyframes: {
        // Only allowed motion: opacity-only animations.
        caretBlink: {
          "0%, 49%":   { opacity: "1" },
          "50%, 100%": { opacity: "0" },
        },
        runningPulse: {
          "0%, 100%": { opacity: "1" },
          "50%":      { opacity: "0.4" },
        },
      },
      animation: {
        "caret-blink":   "caretBlink 1s steps(1, end) infinite",
        "running-pulse": "runningPulse 1.4s ease-in-out infinite",
      },
      transitionDuration: {
        DEFAULT: "120ms",
      },
    },
  },
  plugins: [],
};
