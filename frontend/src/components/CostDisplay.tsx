import { useEffect, useState } from "react";
import { formatCost } from "../utils/format";

interface Props {
  cost: number;
  tick: number;
}

export default function CostDisplay({ cost, tick }: Props) {
  const [flashing, setFlashing] = useState(false);

  useEffect(() => {
    if (tick === 0) return;
    setFlashing(true);
    const id = window.setTimeout(() => setFlashing(false), 500);
    return () => window.clearTimeout(id);
  }, [tick]);

  return (
    <div className="flex items-baseline gap-2">
      <span className="font-mono text-[10px] uppercase tracking-label text-kali-label">
        cost
      </span>
      <span
        className={`font-mono text-[13px] tabular-nums transition-colors duration-500 ${
          flashing ? "text-kali-text" : "text-kali-dim"
        }`}
      >
        {formatCost(cost)}
      </span>
    </div>
  );
}
