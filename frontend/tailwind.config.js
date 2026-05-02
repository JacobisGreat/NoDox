/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        // Pure black-and-white hacker palette. No hue at all — severity
        // and emphasis are encoded as brightness on a single axis. Tokens
        // keep their old `nodoxx-*` names so component classes stay terse.
        nodoxx: {
          // Off-black instead of pure #000 — eliminates halation (bright text
          // bleeding against true black) without sacrificing the terminal feel.
          bg: "#0a0a0a",
          panel: "#111111",
          panel2: "#181818",
          border: "#2a2a2a",
          // Soft white instead of #fff — reduces glare during long scan
          // sessions; still reads as "white" against the off-black ground.
          accent: "#fafafa",
          text: "#fafafa",
          muted: "#737373",
          dim: "#404040",
        },
        // Risk = brightness. The brighter the swatch, the higher the risk
        // — same convention applies to confidence and exposure scores.
        risk: {
          low: "#525252",
          medium: "#a3a3a3",
          high: "#fafafa",
          critical: "#fafafa",
        },
      },
      fontFamily: {
        sans: ['"Inter"', "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "monospace"],
      },
      keyframes: {
        cardIn: {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        runningPulse: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.4" },
        },
        costFlash: {
          "0%": { color: "#ffffff" },
          "100%": { color: "#737373" },
        },
        caretBlink: {
          "0%, 49%": { opacity: "1" },
          "50%, 100%": { opacity: "0" },
        },
      },
      animation: {
        "card-in": "cardIn 200ms ease-out forwards",
        "running-pulse": "runningPulse 1.4s ease-in-out infinite",
        "cost-flash": "costFlash 500ms ease-out forwards",
        "caret-blink": "caretBlink 1s steps(1, end) infinite",
      },
      boxShadow: {
        // White inset hairline + faint outer glow. No color.
        neon: "0 0 0 1px rgba(255, 255, 255, 0.18), 0 0 0 0 rgba(0,0,0,0)",
      },
      borderRadius: {
        DEFAULT: "6px",
      },
    },
  },
  plugins: [],
};
