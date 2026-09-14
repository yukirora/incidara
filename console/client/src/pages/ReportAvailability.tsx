import { useState, useEffect, useCallback } from "react";
import {
  fetchAvailability,
  DailyAvailabilityRow,
  CurrentSnapshotRow,
  InventoryRow,
  RmaSummary,
} from "../api/reports";

// ─── Status group config ─────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  Available: "#22c55e",
  Allocating: "#3b82f6",
  Validating: "#a855f7",
  Cordon: "#f59e0b",
  OFR: "#ef4444",
  Deallocated: "#71717a",
  Unmanaged: "#d4d4d8",
};

const STATUS_ORDER = [
  "Available",
  "Allocating",
  "Validating",
  "Cordon",
  "OFR",
  "Deallocated",
  "Unmanaged",
];

const CATEGORIES = ["all", "h200", "b300", "cpu", "storage", "ctrl"];

// ─── Helpers ─────────────────────────────────────────────────────────

function fmt(n: number): string {
  return n.toLocaleString();
}

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

function todayStr(): string {
  return daysAgo(0);
}

// ─── Component ───────────────────────────────────────────────────────

export function ReportAvailability() {
  const [from, setFrom] = useState(daysAgo(13));
  const [to, setTo] = useState(todayStr());
  const [category, setCategory] = useState("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [daily, setDaily] = useState<DailyAvailabilityRow[]>([]);
  const [current, setCurrent] = useState<CurrentSnapshotRow[]>([]);
  const [inventory, setInventory] = useState<InventoryRow[]>([]);
  const [rma, setRma] = useState<RmaSummary>({
    opened: 0,
    completed: 0,
    pending: 0,
  });

  // Hover state for charts
  const [hoverDay, setHoverDay] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetchAvailability({ from, to, category });
      setDaily(res.daily);
      setCurrent(res.current);
      setInventory(res.inventory);
      setRma(res.rma);
    } catch (e: any) {
      setError(e.message || "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [from, to, category]);

  useEffect(() => {
    load();
  }, [load]);

  // Derived KPIs
  const latestDay = daily.length > 0 ? daily[daily.length - 1] : null;
  const firstDay = daily.length > 0 ? daily[0] : null;
  const availPct = latestDay?.avail_pct ?? 0;
  const totalNodes = current.reduce((s, r) => s + r.count, 0);
  const ofrCount =
    current.find((r) => r.status_group === "OFR")?.count ?? 0;
  const unallocCount =
    current.find((r) => r.status_group === "Deallocated" || r.status_group === "Unallocatable")?.count ?? 0;

  // ─── Render ──────────────────────────────────────────────────────────

  return (
    <div className="p-6 space-y-6" style={{ maxWidth: 1400 }}>
      {/* Header */}
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, color: "#18181b" }}>
          Availability
        </h1>
        <p style={{ fontSize: 13, color: "#71717a", marginTop: 2 }}>
          Cluster capacity, node status distribution, and availability trends
        </p>
      </div>

      {/* Filters */}
      <div
        style={{
          display: "flex",
          gap: 12,
          marginBottom: 20,
          flexWrap: "wrap",
          alignItems: "center",
        }}
      >
        <FilterGroup label="From">
          <input
            type="date"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            style={filterSelectStyle}
          />
        </FilterGroup>
        <FilterGroup label="To">
          <input
            type="date"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            style={filterSelectStyle}
          />
        </FilterGroup>
        <FilterGroup label="Category">
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            style={filterSelectStyle}
          >
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c === "all" ? "All" : c}
              </option>
            ))}
          </select>
        </FilterGroup>
        <button onClick={load} style={refreshBtnStyle}>
          ↻ Refresh
        </button>
      </div>

      {error && (
        <div
          style={{
            color: "#dc2626",
            fontSize: 13,
            marginBottom: 16,
            padding: "8px 12px",
            background: "#fef2f2",
            borderRadius: 8,
          }}
        >
          {error}
        </div>
      )}

      {/* KPIs */}
      <div style={kpiGridStyle}>
        <KpiCard
          label="Availability (Time-Weighted)"
          value={`${availPct}%`}
          color={availPct >= 99 ? "#16a34a" : availPct >= 95 ? "#f59e0b" : "#dc2626"}
          delta={
            firstDay && latestDay
              ? latestDay.avail_pct - firstDay.avail_pct
              : undefined
          }
          deltaUnit="%"
        />
        <KpiCard
          label="Total Nodes"
          value={fmt(totalNodes)}
          sub={
            category === "all"
              ? `${fmt(totalNodes - unallocCount)} usable`
              : undefined
          }
        />
        <KpiCard label="OFR Nodes" value={fmt(ofrCount)} />
        <KpiCard label="Deallocated" value={fmt(unallocCount)} />
      </div>

      {/* Row 2: Daily Availability Ratio + Cluster Capacity */}
      <div style={grid2Style}>
        {/* Daily Availability Ratio Line Chart */}
        <div style={cardStyle}>
          <div style={cardHeaderStyle}>
            <span style={cardTitleStyle}>Daily Availability Ratio</span>
            <span style={cardSubStyle}>Time-weighted</span>
          </div>
          <div style={{ padding: 18 }}>
            <AvailabilityLineChart
              daily={daily}
              hoverDay={hoverDay}
              onHover={setHoverDay}
            />
          </div>
        </div>

        {/* Cluster Capacity Status */}
        <div style={cardStyle}>
          <div style={cardHeaderStyle}>
            <span style={cardTitleStyle}>Cluster Capacity Status</span>
            <span style={cardSubStyle}>Current snapshot</span>
          </div>
          <div style={{ padding: 18 }}>
            <CapacityBar current={current} total={totalNodes} />
          </div>
        </div>
      </div>

      {/* Row 3: Node Status Distribution + Hardware Inventory */}
      <div style={grid2Style}>
        {/* Stacked Area Chart */}
        <div style={cardStyle}>
          <div style={cardHeaderStyle}>
            <span style={cardTitleStyle}>
              Node Distribution (Daily)
            </span>
            <span style={cardSubStyle}>
              Time-weighted · node-equivalents
            </span>
          </div>
          <div style={{ padding: 18 }}>
            <StatusDistributionChart
              daily={daily}
              hoverDay={hoverDay}
              onHover={setHoverDay}
            />
          </div>
        </div>

        {/* Hardware Inventory + RMA */}
        <div style={cardStyle}>
          <div style={cardHeaderStyle}>
            <span style={cardTitleStyle}>Hardware Inventory</span>
            <span style={cardSubStyle}>
              {fmt(inventory.reduce((s, r) => s + r.nodes, 0))} nodes ·{" "}
              {inventory.length} categories
            </span>
          </div>
          <div style={{ padding: "12px 18px" }}>
            <InventoryTable inventory={inventory} />
            <RmaSection rma={rma} />
          </div>
        </div>
      </div>

      {/* Loading overlay */}
      {loading && (
        <div
          style={{
            position: "fixed",
            top: 12,
            right: 12,
            background: "#eef2ff",
            color: "#4f46e5",
            padding: "6px 14px",
            borderRadius: 8,
            fontSize: 12,
            fontWeight: 600,
            zIndex: 100,
          }}
        >
          Loading…
        </div>
      )}
    </div>
  );
}

// ─── Sub-components ───────────────────────────────────────────────────

function FilterGroup({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <span
        style={{
          fontSize: 11,
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: 0.5,
          color: "#a1a1aa",
        }}
      >
        {label}
      </span>
      {children}
    </div>
  );
}

function KpiCard({
  label,
  value,
  color,
  sub,
  delta,
  deltaUnit,
}: {
  label: string;
  value: string;
  color?: string;
  sub?: string;
  delta?: number;
  deltaUnit?: string;
}) {
  return (
    <div style={kpiCardStyle}>
      <div style={kpiLabelStyle}>{label}</div>
      <div style={{ ...kpiValueStyle, ...(color ? { color } : {}) }}>
        {value}
      </div>
      <div style={{ marginTop: 4, display: "flex", gap: 8, alignItems: "center" }}>
        {sub && (
          <span style={kpiDeltaNeutralStyle}>{sub}</span>
        )}
        {delta !== undefined && delta !== 0 && (
          <span
            style={
              delta > 0
                ? { ...kpiDeltaStyle, background: "#f0fdf4", color: "#16a34a" }
                : { ...kpiDeltaStyle, background: "#fef2f2", color: "#dc2626" }
            }
          >
            {delta > 0 ? "↑" : "↓"} {Math.abs(delta).toFixed(2)}
            {deltaUnit}
          </span>
        )}
      </div>
    </div>
  );
}

// ─── Availability Line Chart (SVG) ───────────────────────────────────

function AvailabilityLineChart({
  daily,
  hoverDay,
  onHover,
}: {
  daily: DailyAvailabilityRow[];
  hoverDay: number | null;
  onHover: (i: number | null) => void;
}) {
  if (daily.length === 0)
    return <div style={{ color: "#a1a1aa", fontSize: 12 }}>No data</div>;

  const W = 420, H = 170;
  const pad = { l: 48, r: 15, t: 20, b: 25 };
  const cw = W - pad.l - pad.r;
  const ch = H - pad.t - pad.b;

  const pcts = daily.map((d) => d.avail_pct);
  const minP = Math.floor(Math.min(...pcts) * 2) / 2 - 0.5;
  const maxP = 100;
  const range = maxP - minP;

  const xScale = (i: number) =>
    pad.l + (i / (daily.length - 1 || 1)) * cw;
  const yScale = (v: number) =>
    pad.t + ch - ((v - minP) / range) * ch;

  const threshY = yScale(99);

  const points = daily
    .map((d, i) => `${xScale(i)},${yScale(d.avail_pct)}`)
    .join(" ");

  const pathD = daily
    .map(
      (d, i) =>
        `${i === 0 ? "M" : "L"}${xScale(i)},${yScale(d.avail_pct)}`
    )
    .join(" ") +
    ` L${xScale(daily.length - 1)},${pad.t + ch} L${xScale(0)},${pad.t + ch} Z`;

  // Y-axis ticks — only 3 to avoid overlap
  const yTicks = [maxP, Math.round((maxP + minP) / 2 * 10) / 10, minP];

  const xLabelStep = Math.max(1, Math.floor(daily.length / 5));

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%" }}>
      {/* Grid lines */}
      {yTicks.map((v, i) => (
        <line
          key={i}
          x1={pad.l}
          y1={yScale(v)}
          x2={W - pad.r}
          y2={yScale(v)}
          stroke="#f4f4f5"
          strokeWidth={1}
        />
      ))}

      {/* 99% threshold */}
      {99 >= minP && 99 <= maxP && (
        <>
          <line
            x1={pad.l}
            y1={threshY}
            x2={W - pad.r}
            y2={threshY}
            stroke="#f59e0b"
            strokeWidth={1}
            strokeDasharray="4,3"
          />
          <text x={pad.l - 4} y={threshY + 3} fill="#f59e0b" fontSize={8} textAnchor="end">
            99%
          </text>
        </>
      )}

      {/* Y-axis labels */}
      {yTicks.map((v, i) => (
        <text
          key={i}
          x={pad.l - 4}
          y={yScale(v) + 3}
          fill="#a1a1aa"
          fontSize={9}
          textAnchor="end"
        >
          {v.toFixed(1)}%
        </text>
      ))}

      {/* Area fill */}
      <path d={pathD} fill="#22c55e" opacity={0.15} />

      {/* Line */}
      <polyline
        points={points}
        stroke="#22c55e"
        strokeWidth={2}
        fill="none"
      />

      {/* Dots + hover zones */}
      {daily.map((d, i) => (
        <g key={i}>
          {/* Invisible hover rect */}
          <rect
            x={xScale(i) - cw / daily.length / 2}
            y={pad.t}
            width={cw / daily.length}
            height={ch}
            fill="transparent"
            style={{ cursor: "pointer" }}
            onMouseEnter={() => onHover(i)}
            onMouseLeave={() => onHover(null)}
          />
          <circle
            cx={xScale(i)}
            cy={yScale(d.avail_pct)}
            r={hoverDay === i ? 4 : 2.5}
            fill="#22c55e"
            style={{ pointerEvents: "none" }}
          />
        </g>
      ))}

      {/* X-axis labels */}
      {daily
        .filter((_, i) => i % xLabelStep === 0 || i === daily.length - 1)
        .map((d, idx) => {
          const i = Math.min(idx * xLabelStep, daily.length - 1);
          return (
            <text
              key={i}
              x={xScale(i)}
              y={H - 4}
              fill="#a1a1aa"
              fontSize={7}
              textAnchor="middle"
            >
              {d.day.slice(5)}
            </text>
          );
        })}

      {/* Hover: vertical line + value label */}
      {hoverDay !== null && hoverDay < daily.length && (
        <>
          <line
            x1={xScale(hoverDay)}
            y1={pad.t}
            x2={xScale(hoverDay)}
            y2={pad.t + ch}
            stroke="#a1a1aa"
            strokeWidth={1}
            strokeDasharray="2,2"
            opacity={0.5}
          />
          <rect
            x={xScale(hoverDay) - 28}
            y={yScale(daily[hoverDay].avail_pct) - 16}
            width={56}
            height={14}
            rx={3}
            fill="#18181b"
          />
          <text
            x={xScale(hoverDay)}
            y={yScale(daily[hoverDay].avail_pct) - 6}
            fill="#fff"
            fontSize={9}
            fontWeight={600}
            textAnchor="middle"
          >
            {daily[hoverDay].avail_pct}%
          </text>
        </>
      )}
    </svg>
  );
}

// ─── Status Distribution Stacked Area Chart (SVG) ────────────────────

function StatusDistributionChart({
  daily,
  hoverDay,
  onHover,
}: {
  daily: DailyAvailabilityRow[];
  hoverDay: number | null;
  onHover: (i: number | null) => void;
}) {
  if (daily.length === 0)
    return <div style={{ color: "#a1a1aa", fontSize: 12 }}>No data</div>;

  const W = 420, H = 170;
  const pad = { l: 30, r: 80, t: 10, b: 20 };
  const cw = W - pad.l - pad.r;
  const ch = H - pad.t - pad.b;

  const maxTotal = Math.max(...daily.map((d) => d.total));
  const xScale = (i: number) =>
    pad.l + (i / (daily.length - 1 || 1)) * cw;

  const groups = [
    { key: "deallocated", altKey: "unallocatable", color: STATUS_COLORS.Deallocated, label: "Dealloc" },
    { key: "ofr", color: STATUS_COLORS.OFR, label: "OFR" },
    { key: "cordon", color: STATUS_COLORS.Cordon, label: "Cordon" },
    { key: "validating", color: STATUS_COLORS.Validating, label: "Valid." },
    { key: "allocated", color: STATUS_COLORS.Allocating, label: "Alloc." },
    { key: "allocatable", color: STATUS_COLORS.Available, label: "Allocat." },
  ];

  // Build cumulative stacks
  const stacks: { y: number; h: number }[][] = groups.map(() => []);
  for (let di = 0; di < daily.length; di++) {
    let cumH = 0;
    for (let gi = 0; gi < groups.length; gi++) {
      const val = ((daily[di] as any)[groups[gi].key] ?? (daily[di] as any)[(groups[gi] as any).altKey] ?? 0) as number;
      const h = (val / maxTotal) * ch;
      stacks[gi][di] = { y: pad.t + ch - cumH - h, h };
      cumH += h;
    }
  }

  const xLabelStep = Math.max(1, Math.floor(daily.length / 5));

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%" }}>
      {/* Stacked areas */}
      {groups.map((g, gi) => {
        const topPoints = stacks[gi]
          .map((s, i) => `${xScale(i)},${s.y}`)
          .join(" ");
        const bottomPoints = stacks[gi]
          .map((s, i) => `${xScale(i)},${s.y + s.h}`)
          .reverse()
          .join(" ");

        const d = `M${topPoints} L${bottomPoints} Z`;
        return <path key={g.key} d={d} fill={g.color} opacity={0.5} />;
      })}

      {/* Legend — vertically stacked on right side */}
      <g>
        {groups.map((g, gi) => {
          const ly = pad.t + 10 + gi * 14;
          return (
            <g key={g.key}>
              <rect x={W - pad.r + 6} y={ly - 5} width={6} height={6} fill={g.color} rx={1} />
              <text x={W - pad.r + 15} y={ly} fill="#71717a" fontSize={8}>
                {g.label}
              </text>
            </g>
          );
        })}
      </g>

      {/* X-axis labels */}
      {daily
        .filter((_, i) => i % xLabelStep === 0 || i === daily.length - 1)
        .map((d, idx) => {
          const i = Math.min(idx * xLabelStep, daily.length - 1);
          return (
            <text key={i} x={xScale(i)} y={H - 4} fill="#a1a1aa" fontSize={7} textAnchor="middle">
              {d.day.slice(5)}
            </text>
          );
        })}

      {/* Invisible hover zones */}
      {daily.map((_, i) => (
        <rect
          key={i}
          x={xScale(i) - cw / daily.length / 2}
          y={pad.t}
          width={cw / daily.length}
          height={ch}
          fill="transparent"
          style={{ cursor: "pointer" }}
          onMouseEnter={() => onHover(i)}
          onMouseLeave={() => onHover(null)}
        />
      ))}

      {/* Hover: vertical line */}
      {hoverDay !== null && hoverDay < daily.length && (
        <line
          x1={xScale(hoverDay)}
          y1={pad.t}
          x2={xScale(hoverDay)}
          y2={pad.t + ch}
          stroke="#18181b"
          strokeWidth={1}
          strokeDasharray="2,2"
          opacity={0.4}
        />
      )}

      {/* Hover: tooltip as SVG text */}
      {hoverDay !== null && hoverDay < daily.length && (
        <SvgTooltip daily={daily} idx={hoverDay} x={xScale(hoverDay)} yPad={pad.t} />
      )}
    </svg>
  );
}

function SvgTooltip({
  daily,
  idx,
  x,
  yPad,
}: {
  daily: DailyAvailabilityRow[];
  idx: number;
  x: number;
  yPad: number;
}) {
  const d = daily[idx];
  if (!d) return null;

  const rows = [
    { label: "Available", value: d.allocatable ?? 0, color: STATUS_COLORS.Available },
    { label: "Allocating", value: d.allocated ?? 0, color: STATUS_COLORS.Allocating },
    { label: "Validating", value: d.validating ?? 0, color: STATUS_COLORS.Validating },
    { label: "Cordon", value: d.cordon ?? 0, color: STATUS_COLORS.Cordon },
    { label: "OFR", value: d.ofr ?? 0, color: STATUS_COLORS.OFR },
    { label: "Dealloc.", value: (d.deallocated ?? d.unallocatable ?? 0) as number, color: STATUS_COLORS.Deallocated },
  ];

  const tw = 105;
  const th = 8 + rows.length * 9 + 11;
  const tx = x + 6;
  const adjustedTx = tx + tw > 420 ? x - tw - 6 : tx;

  return (
    <g>
      <rect x={adjustedTx} y={yPad} width={tw} height={th} rx={3} fill="#18181b" opacity={0.92} />
      <text x={adjustedTx + 5} y={yPad + 8} fill="#fff" fontSize={7} fontWeight={700}>
        {d.day.slice(5)} <tspan fill="#86efac">{d.avail_pct}%</tspan>
      </text>
      {rows.map((r, i) => (
        <g key={r.label}>
          <rect x={adjustedTx + 5} y={yPad + 10 + i * 9} width={4} height={4} fill={r.color} rx={0.5} />
          <text x={adjustedTx + 11} y={yPad + 14 + i * 9} fill="#d4d4d8" fontSize={6}>
            {r.label}
          </text>
          <text x={adjustedTx + tw - 5} y={yPad + 14 + i * 9} fill="#fff" fontSize={6} textAnchor="end" fontWeight={600}>
            {r.value.toLocaleString(undefined, { maximumFractionDigits: 1 })}
          </text>
        </g>
      ))}
      <line x1={adjustedTx + 3} y1={yPad + 11 + rows.length * 9} x2={adjustedTx + tw - 3} y2={yPad + 11 + rows.length * 9} stroke="#52525b" strokeWidth={0.5} />
      <text x={adjustedTx + 5} y={yPad + 17 + rows.length * 9} fill="#fff" fontSize={6} fontWeight={700}>
        Total
      </text>
      <text x={adjustedTx + tw - 5} y={yPad + 17 + rows.length * 9} fill="#fff" fontSize={6} textAnchor="end" fontWeight={700}>
        {d.total.toLocaleString(undefined, { maximumFractionDigits: 1 })}
      </text>
    </g>
  );
}

// ─── Capacity Stacked Bar + Table ────────────────────────────────────

function CapacityBar({
  current,
  total,
}: {
  current: CurrentSnapshotRow[];
  total: number;
}) {
  const ordered = STATUS_ORDER.map((g) => {
    const row = current.find((r) => r.status_group === g);
    return { group: g, count: row?.count ?? 0, pct: row?.pct ?? 0 };
  }).filter((r) => r.count > 0);

  return (
    <>
      <div style={{ fontSize: 11, color: "#71717a", marginBottom: 6 }}>
        Node Distribution ({fmt(total)} total)
      </div>
      {/* Stacked bar */}
      <div
        style={{
          display: "flex",
          height: 28,
          borderRadius: 4,
          overflow: "hidden",
        }}
      >
        {ordered.map((r) => (
          <div
            key={r.group}
            style={{
              width: `${r.pct}%`,
              background: STATUS_COLORS[r.group] || "#d4d4d8",
              minWidth: r.count > 0 ? 2 : 0,
            }}
            title={`${r.group}: ${r.count}`}
          />
        ))}
      </div>
      {/* Legend */}
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 10,
          marginTop: 8,
          fontSize: 10,
          color: "#71717a",
        }}
      >
        {ordered.map((r) => (
          <span key={r.group}>
            ■ {r.group}
          </span>
        ))}
      </div>
      {/* Table */}
      <table style={{ width: "100%", fontSize: 11, marginTop: 12, borderCollapse: "collapse" }}>
        {ordered.map((r) => (
          <tr key={r.group}>
            <td style={{ padding: "4px 0", color: "#3f3f46" }}>{r.group}</td>
            <td
              style={{
                padding: "4px 0",
                textAlign: "right",
                fontWeight: 600,
                color: STATUS_COLORS[r.group] || "#71717a",
              }}
            >
              {fmt(r.count)}
            </td>
            <td
              style={{
                padding: "4px 8px",
                textAlign: "right",
                color: "#a1a1aa",
              }}
            >
              {r.pct}%
            </td>
          </tr>
        ))}
      </table>
    </>
  );
}

// ─── Inventory Table ─────────────────────────────────────────────────

function InventoryTable({ inventory }: { inventory: InventoryRow[] }) {
  return (
    <table
      style={{
        width: "100%",
        fontSize: 11,
        borderCollapse: "collapse",
      }}
    >
      <thead>
        <tr>
          <th style={thStyle}>Category</th>
          <th style={{ ...thStyle, textAlign: "right" }}>Nodes</th>
          <th style={{ ...thStyle, textAlign: "right" }}>Racks</th>
          <th style={thStyle}>Top SKU</th>
        </tr>
      </thead>
      <tbody>
        {inventory.map((r) => (
          <tr key={r.category}>
            <td style={{ ...tdStyle, fontWeight: 600 }}>{r.category}</td>
            <td style={{ ...tdStyle, textAlign: "right" }}>
              {fmt(r.nodes)}
            </td>
            <td style={{ ...tdStyle, textAlign: "right" }}>
              {fmt(r.racks)}
            </td>
            <td style={tdStyle}>{r.top_sku}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ─── RMA Section ─────────────────────────────────────────────────────

function RmaSection({ rma }: { rma: RmaSummary }) {
  return (
    <div
      style={{
        marginTop: 14,
        paddingTop: 12,
        borderTop: "1px solid #f4f4f5",
      }}
    >
      <div style={{ fontSize: 11, color: "#71717a", marginBottom: 6 }}>
        RMA Activity (Selected Period)
      </div>
      <div style={{ display: "flex", gap: 24 }}>
        <RmaCount label="Opened" value={rma.opened} />
        <RmaCount label="Completed" value={rma.completed} color="#16a34a" />
        <RmaCount label="Pending" value={rma.pending} color="#f59e0b" />
      </div>
    </div>
  );
}

function RmaCount({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color?: string;
}) {
  return (
    <div style={{ textAlign: "center" }}>
      <div
        style={{
          fontSize: 18,
          fontWeight: 700,
          color: color || "#18181b",
        }}
      >
        {fmt(value)}
      </div>
      <div style={{ fontSize: 10, color: "#71717a" }}>{label}</div>
    </div>
  );
}

// ─── Styles ──────────────────────────────────────────────────────────

const filterSelectStyle: React.CSSProperties = {
  padding: "5px 8px",
  border: "1px solid #d4d4d8",
  borderRadius: 6,
  fontSize: 12,
  color: "#3f3f46",
};

const refreshBtnStyle: React.CSSProperties = {
  marginLeft: "auto",
  padding: "5px 12px",
  borderRadius: 6,
  fontSize: 12,
  fontWeight: 500,
  background: "#fff",
  border: "1px solid #d4d4d8",
  color: "#52525b",
  cursor: "pointer",
};

const kpiGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(4, 1fr)",
  gap: 14,
  marginBottom: 20,
};

const kpiCardStyle: React.CSSProperties = {
  background: "#fff",
  borderRadius: 12,
  border: "1px solid #e4e4e7",
  padding: "16px 18px",
};

const kpiLabelStyle: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: 0.5,
  color: "#a1a1aa",
  marginBottom: 6,
};

const kpiValueStyle: React.CSSProperties = {
  fontSize: 28,
  fontWeight: 700,
  color: "#18181b",
  lineHeight: 1.1,
};

const kpiDeltaStyle: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 600,
  padding: "1px 6px",
  borderRadius: 4,
};

const kpiDeltaNeutralStyle: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 500,
  padding: "1px 6px",
  borderRadius: 4,
  background: "#f4f4f5",
  color: "#71717a",
};

const grid2Style: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr 1fr",
  gap: 16,
  marginBottom: 16,
};

const cardStyle: React.CSSProperties = {
  background: "#fff",
  borderRadius: 12,
  border: "1px solid #e4e4e7",
  overflow: "hidden",
};

const cardHeaderStyle: React.CSSProperties = {
  padding: "14px 18px",
  borderBottom: "1px solid #f4f4f5",
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
};

const cardTitleStyle: React.CSSProperties = {
  fontSize: 13,
  fontWeight: 600,
  color: "#3f3f46",
};

const cardSubStyle: React.CSSProperties = {
  fontSize: 11,
  color: "#a1a1aa",
};

const thStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "6px 8px",
  fontWeight: 600,
  color: "#71717a",
  borderBottom: "2px solid #e4e4e7",
  fontSize: 10,
  textTransform: "uppercase",
  letterSpacing: 0.3,
};

const tdStyle: React.CSSProperties = {
  padding: "6px 8px",
  borderBottom: "1px solid #f4f4f5",
  color: "#3f3f46",
};
