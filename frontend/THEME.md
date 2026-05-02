# THEME — NoDoxx B&W Kali Tokens

Strict palette. No other colors. No gradients. No glows. No drop shadows.

| Token             | Hex      | Use                                      |
| ----------------- | -------- | ---------------------------------------- |
| `--kali-bg`       | #000000  | Page background                          |
| `--kali-surface`  | #0A0A0A  | Cards, raised panels, row-hover bg       |
| `--kali-border`   | #1F1F1F  | Default 1px borders                      |
| `--kali-text`     | #FFFFFF  | Primary text, active borders, inverts    |
| `--kali-dim`      | #A1A1A1  | Secondary text, sidebar inactive         |
| `--kali-label`    | #666666  | Uppercase labels ONLY — never body copy  |

Contrast spot-checks (vs #000 bg):
- #FFFFFF → 21:1 ✓
- #A1A1A1 → 9.7:1 ✓ (passes 7:1)
- #666666 → 4.5:1 — labels only

Tailwind utility classes mirror these tokens as `bg-kali-bg`, `text-kali-dim`, `border-kali-border`, etc. CSS variables `var(--kali-*)` are available for inline styles and Leaflet `pathOptions` where utility classes can't reach.

## Typography
- Mono (default): JetBrains Mono 400 / 700.
- Sans (`.prose`): Inter 400 / 600. Use only for prose >2 sentences.
- Base: 14px desktop, 15px mobile. Line-height 1.5 prose, 1.4 mono.
- Tracking: `-0.01em` Inter (`tracking-prose`); `0` mono (`tracking-mono`); `+0.08em` uppercase labels (`tracking-label`).
- ALL CAPS for section labels (mono only). No italics. No font-weight <400.
- Max prose line length: 72ch (enforced by `.prose`).

## Layout
- Hard 8px grid. Spacing: 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64. Anything else is a bug.
- Borders: always 1px. Never 2px or 3px.
- Border radius: 0 by default. Inputs may use `rounded-input` (2px). All other `rounded-*` classes are overridden to 0 by the Tailwind config.
- Container max-width: 1280px (`max-w-[1280px]`), 24px gutter.

## Components
- **Card**: `border: 1px solid var(--kali-border)`. Hover: border becomes `--kali-text`. No shadow, no lift, no transform.
- **Button (primary)**: white bg, black text, 1px white border, 36px tall, 12px x-pad, mono 13px. Hover inverts.
- **Button (secondary)**: transparent bg, white border, white text. Hover inverts.
- **Button (ghost)**: no border, `--kali-dim` text. Hover: `--kali-text` + underline.
- **Input**: `bg: var(--kali-bg); border: 1px solid var(--kali-border); color: var(--kali-text); height: 36px; rounded-input`. Focus: white border. No shadow.
- **Status (ASCII tokens)**: `[OK]`, `[ERR]`, `[...]`, `[*]`, `[+]`, `[-]`, `[~]`, `[ ]`, `[x]`. White text, square brackets always. Use these in place of SVG icons wherever possible.
- **Terminal chrome bar**: thin bar at top of every page — `user@app ~ % <route>` in mono 12px, `--kali-dim`, blinking caret after route.

## Motion
- Allowed: `opacity`, `border-color`, `color`, `background-color` (the last two only for hover-inverts). Duration 120ms ease.
- Banned: any `transform` on hover (translate, scale, rotate). No load-in fades. No bouncing. No spring physics.
- Continuous animations: `animate-caret-blink` (terminal caret), `animate-running-pulse` (active status indicator). Both opacity-only.

## Anti-patterns
Glowing green text. Drop shadows. Gradients. Rounded corners >2px. Emoji icons. Marketing tropes (hero gradients, oversized headings, illustrated empty states). Fake scan-line overlays. Mixing 3+ font weights in one heading.
