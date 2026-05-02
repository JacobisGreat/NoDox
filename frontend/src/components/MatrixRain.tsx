import { useEffect, useRef } from "react";

// Cybersec digital-rain backdrop. White-on-black only — no green, no hue.
// Glyph stream is a mix of katakana, hex digits, and shell punctuation so it
// reads as "scanner output" rather than the literal cmatrix homage.
//
// Performance contract: a single canvas, one rAF loop, no React re-renders
// after mount. Trail is drawn by overlaying the whole canvas with a faint
// translucent black rect every frame instead of clearing — that's what gives
// the characters their fading tail without an extra per-cell allocation.

const GLYPHS =
  "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン" +
  "0123456789ABCDEFabcdef" +
  "<>/\\|{}[]()$#@!?*+=-_:;.,";

const FONT_SIZE = 14;
// FPS cap — 24fps reads as "deliberate, computer-y" instead of buttery and
// avoids burning battery on a purely decorative layer.
const FRAME_INTERVAL_MS = 1000 / 24;

export default function MatrixRain() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) return;

    let drops: number[] = [];
    let columnCount = 0;
    let dpr = window.devicePixelRatio || 1;

    const resize = () => {
      dpr = window.devicePixelRatio || 1;
      const w = window.innerWidth;
      const h = window.innerHeight;
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      // Paint pure off-black ground once — we never `clearRect` again, only
      // overlay translucent rectangles to fade older glyphs into the bg.
      ctx.fillStyle = "#0a0a0a";
      ctx.fillRect(0, 0, w, h);

      columnCount = Math.ceil(w / FONT_SIZE);
      drops = new Array(columnCount).fill(0).map(
        // Stagger initial vertical positions so the rain doesn't appear in
        // a single horizontal sheet on first paint.
        () => Math.floor((Math.random() * h) / FONT_SIZE),
      );
    };
    resize();

    const onResize = () => resize();
    window.addEventListener("resize", onResize);

    let raf = 0;
    let lastFrame = performance.now();

    const tick = (now: number) => {
      raf = window.requestAnimationFrame(tick);
      if (now - lastFrame < FRAME_INTERVAL_MS) return;
      lastFrame = now;

      const w = window.innerWidth;
      const h = window.innerHeight;

      // Trail fade — translucent off-black overlay. Lower alpha = longer
      // streak; 0.08 lands around a 12-glyph trail at 24fps.
      ctx.fillStyle = "rgba(10, 10, 10, 0.08)";
      ctx.fillRect(0, 0, w, h);

      ctx.font = `${FONT_SIZE}px "JetBrains Mono", ui-monospace, monospace`;
      ctx.textBaseline = "top";

      for (let i = 0; i < columnCount; i++) {
        const ch = GLYPHS.charAt(Math.floor(Math.random() * GLYPHS.length));
        const x = i * FONT_SIZE;
        const y = drops[i] * FONT_SIZE;

        // Brightness varies per column — most are dim, ~5% are bright "head"
        // characters. Pure grayscale only.
        const head = Math.random() < 0.04;
        ctx.fillStyle = head ? "rgba(250, 250, 250, 0.95)" : "rgba(180, 180, 180, 0.55)";
        ctx.fillText(ch, x, y);

        // Recycle drops that have fallen off-screen, with a randomized reset
        // so columns don't all reset on the same frame.
        if (y > h && Math.random() > 0.975) {
          drops[i] = 0;
        } else {
          drops[i] += 1;
        }
      }
    };
    raf = window.requestAnimationFrame(tick);

    return () => {
      window.cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className="pointer-events-none fixed inset-0 z-0 h-full w-full opacity-[0.18] mix-blend-screen"
    />
  );
}
