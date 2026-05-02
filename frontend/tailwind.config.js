/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        nodox: {
          bg: "#0a0f1e",
          panel: "#111a2e",
          border: "#1f3f63",
          cyan: "#00d4ff",
          high: "#ef4444",
          medium: "#f97316",
          low: "#22c55e"
        }
      },
      boxShadow: {
        neon: "0 0 0 1px rgba(0, 212, 255, 0.25), 0 0 24px rgba(0, 212, 255, 0.12)"
      },
      fontFamily: {
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"]
      }
    }
  },
  plugins: []
};
