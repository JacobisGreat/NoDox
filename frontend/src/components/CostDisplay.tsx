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
      <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-nodoxx-muted">
        cost
      </span>
      <span
        className={`font-mono text-sm tabular-nums transition-colors duration-500 ${
          flashing ? "text-nodoxx-text" : "text-nodoxx-muted"
        }`}
      >
        {formatCost(cost)}
      </span>
    </div>
  );
}
