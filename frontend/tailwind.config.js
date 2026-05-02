/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        nodoxx: {
          bg: "#0a0f1e",
          panel: "#111a2e",
          border: "#1f3f63",
          accent: "#00d4ff",
          text: "#dbeafe",
          muted: "#94a3b8",
        },
        risk: {
          low: "#22c55e",
          medium: "#f97316",
          high: "#ef4444",
          critical: "#ef4444",
        },
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "monospace"],
        sans: ["system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
      },
      keyframes: {
        cardIn: {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        runningPulse: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.55" },
        },
        costFlash: {
          "0%": { color: "#00d4ff" },
          "100%": { color: "#94a3b8" },
        },
      },
      animation: {
        "card-in": "cardIn 300ms ease-out forwards",
        "running-pulse": "runningPulse 1.4s ease-in-out infinite",
        "cost-flash": "costFlash 500ms ease-out forwards",
      },
      boxShadow: {
        neon: "0 0 0 1px rgba(0, 212, 255, 0.25), 0 0 24px rgba(0, 212, 255, 0.12)",
      },
    },
  },
  plugins: [],
};
