import { useEffect, useRef } from "react";

const GLYPHS =
  "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<>/\\|=+-*".split(
    "",
  );

const FONT_SIZE = 14;
const STEP_MS = 70;

export default function MatrixRain() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvasEl = canvasRef.current;
    if (!canvasEl) return;
    const ctxMaybe = canvasEl.getContext("2d", { alpha: true });
    if (!ctxMaybe) return;
    // Bind non-null aliases so nested closures stay narrowed under strict TS.
    const canvas: HTMLCanvasElement = canvasEl;
    const ctx: CanvasRenderingContext2D = ctxMaybe;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    let width = window.innerWidth;
    let height = window.innerHeight;
    let columns = 0;
    let drops: number[] = [];

    function resize() {
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const newColumns = Math.ceil(width / FONT_SIZE);
      // Preserve existing drop progress when resizing
      const next = new Array(newColumns).fill(0).map((_, i) => {
        if (i < drops.length) return drops[i];
        return Math.random() * (-height / FONT_SIZE);
      });
      drops = next;
      columns = newColumns;
      // Clear once on resize
      ctx.clearRect(0, 0, width, height);
    }
    resize();
    window.addEventListener("resize", resize);

    let raf = 0;
    let last = 0;

    function frame(now: number) {
      raf = requestAnimationFrame(frame);
      if (now - last < STEP_MS) return;
      last = now;

      // Soft trail fade — paint a translucent black rect over previous frame
      ctx.fillStyle = "rgba(0, 0, 0, 0.12)";
      ctx.fillRect(0, 0, width, height);

      ctx.font = `${FONT_SIZE}px "JetBrains Mono", ui-monospace, monospace`;
      ctx.textBaseline = "top";

      for (let i = 0; i < columns; i++) {
        const x = i * FONT_SIZE;
        const y = drops[i] * FONT_SIZE;

        // Bright lead glyph
        const lead = GLYPHS[(Math.random() * GLYPHS.length) | 0];
        ctx.fillStyle = "rgba(255,255,255,0.85)";
        ctx.fillText(lead, x, y);

        // Dimmer trailing glyph just above
        if (drops[i] > 1) {
          const trail = GLYPHS[(Math.random() * GLYPHS.length) | 0];
          ctx.fillStyle = "rgba(255,255,255,0.30)";
          ctx.fillText(trail, x, y - FONT_SIZE);
        }

        // Reset stream randomly once it falls past the bottom
        if (y > height && Math.random() > 0.975) {
          drops[i] = 0;
        }
        drops[i] += 1;
      }
    }
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      style={{
        position: "fixed",
        inset: 0,
        width: "100vw",
        height: "100vh",
        zIndex: -1,
        pointerEvents: "none",
        opacity: 0.22,
      }}
    />
  );
}
