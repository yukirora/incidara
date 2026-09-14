#!/usr/bin/env python3
"""Generate the H200 report figures from the verified aggregate metrics.

Uses only Python's standard library for SVG generation. If rsvg-convert is
available, matching PNG files are emitted for LaTeX.
"""

from __future__ import annotations

import html
import shutil
import subprocess
from pathlib import Path

OUT = Path(__file__).with_name("figures")
OUT.mkdir(parents=True, exist_ok=True)

NAVY = "#102A43"
BLUE = "#2563EB"
CYAN = "#0891B2"
GREEN = "#059669"
AMBER = "#D97706"
RED = "#DC2626"
SLATE = "#64748B"
LIGHT = "#E2E8F0"
PALE = "#F8FAFC"
WHITE = "#FFFFFF"
FONT = "Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Arial, sans-serif"

STAGES = ["Pre-agent", "Triage v1", "Multi-agent", "Proactive"]
DATES = ["Feb 15–Apr 16", "Apr 17–May 6", "May 7–Jun 7", "Jun 8–25"]
AVAILABILITY = [91.393, 96.293, 98.366, 99.661]
INCIDENT_RATE = [18.564, 4.549, 6.544, 4.217]
P90_RECOVERY = [150.44, 143.38, 105.27, 33.09]


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def svg_start(width: int, height: int, title: str, desc: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        f"<title id=\"title\">{esc(title)}</title>",
        f"<desc id=\"desc\">{esc(desc)}</desc>",
        f'<rect width="{width}" height="{height}" fill="{WHITE}"/>',
        "<defs>",
        '<filter id="shadow" x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="0" dy="2" stdDeviation="4" flood-color="#0F172A" flood-opacity="0.10"/></filter>',
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#94A3B8"/></marker>',
        "</defs>",
    ]


def text(x: float, y: float, value: object, size: int = 14, color: str = NAVY,
         weight: int = 400, anchor: str = "start", opacity: float = 1.0) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" '
        f'fill="{color}" font-weight="{weight}" text-anchor="{anchor}" opacity="{opacity}">{esc(value)}</text>'
    )


def multiline(x: float, y: float, lines: list[str], size: int = 14,
              color: str = NAVY, weight: int = 400, line_height: int = 20,
              anchor: str = "start") -> str:
    spans = "".join(
        f'<tspan x="{x:.1f}" dy="{0 if i == 0 else line_height}">{esc(line)}</tspan>'
        for i, line in enumerate(lines)
    )
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" '
        f'fill="{color}" font-weight="{weight}" text-anchor="{anchor}">{spans}</text>'
    )


def line_chart(parts: list[str], x: int, y: int, w: int, h: int,
               title_value: str, subtitle: str, values: list[float],
               min_value: float, max_value: float, unit: str, color: str,
               decimals: int = 1) -> None:
    parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{PALE}" stroke="{LIGHT}"/>')
    parts.append(text(x + 22, y + 31, title_value, 16, NAVY, 700))
    parts.append(text(x + 22, y + 52, subtitle, 11, SLATE))
    px0, py0 = x + 54, y + 78
    pw, ph = w - 82, h - 128
    for i in range(4):
        gy = py0 + ph * i / 3
        value = max_value - (max_value - min_value) * i / 3
        parts.append(f'<line x1="{px0}" y1="{gy:.1f}" x2="{px0 + pw}" y2="{gy:.1f}" stroke="{LIGHT}" stroke-width="1"/>')
        parts.append(text(px0 - 8, gy + 4, f"{value:.0f}", 10, SLATE, anchor="end"))
    points: list[tuple[float, float]] = []
    for i, value in enumerate(values):
        px = px0 + pw * i / 3
        py = py0 + ph * (max_value - value) / (max_value - min_value)
        points.append((px, py))
    parts.append('<polyline points="{}" fill="none" stroke="{}" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>'.format(
        " ".join(f"{px:.1f},{py:.1f}" for px, py in points), color
    ))
    for i, ((px, py), value) in enumerate(zip(points, values)):
        parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="6" fill="{WHITE}" stroke="{color}" stroke-width="3"/>')
        parts.append(text(px, py - 13, f"{value:.{decimals}f}{unit}", 11, color, 700, "middle"))
        parts.append(text(px, y + h - 25, STAGES[i], 10, SLATE, 600, "middle"))


def save_svg(name: str, parts: list[str]) -> None:
    parts.append("</svg>")
    path = OUT / f"{name}.svg"
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    converter = shutil.which("rsvg-convert")
    if converter:
        subprocess.run(
            [converter, "-w", "2400", "-f", "png", "-o", str(OUT / f"{name}.png"), str(path)],
            check=True,
        )


def rollout_trends() -> None:
    parts = svg_start(
        1200, 590,
        "H200 reliability metrics across the staged AI-agent rollout",
        "Three line charts show operational availability rising, incident rate falling, and P90 recovery time falling across four rollout phases.",
    )
    parts += [
        text(44, 48, "H200 reliability across the staged AI-agent rollout", 25, NAVY, 800),
        text(44, 74, "Time-weighted node-state analysis · 2026-02-15 to 2026-06-25 (UTC)", 13, SLATE),
    ]
    line_chart(parts, 38, 105, 362, 405, "Operational availability", "Available + allocated / non-deallocated time", AVAILABILITY, 88, 100, "%", GREEN, 1)
    line_chart(parts, 419, 105, 362, 405, "Incident rate", "Qualifying cordons per 1,000 node-days", INCIDENT_RATE, 0, 20, "", BLUE, 1)
    line_chart(parts, 800, 105, 362, 405, "P90 recovery time", "Hours from cordon to next available state", P90_RECOVERY, 0, 160, "h", AMBER, 0)
    parts.append(text(44, 555, "Rollout landmarks: initial triage (Apr 17), repair delegation (May 7), proactive detection (Jun 8).", 12, SLATE))
    save_svg("figure-1-rollout-trends", parts)


def before_after() -> None:
    parts = svg_start(
        1200, 700,
        "H200 pre-agent versus proactive-agent outcomes",
        "A before-and-after scorecard comparing availability, incident rate, recovery latency, 24-hour recovery, triage coverage, and unknown classifications.",
    )
    parts += [
        text(44, 49, "H200 before vs. proactive-agent phase", 25, NAVY, 800),
        text(44, 76, "Pre-agent: Feb 15–Apr 16 · Proactive phase: Jun 8–25", 13, SLATE),
        f'<rect x="875" y="35" width="16" height="16" rx="4" fill="#CBD5E1"/>',
        text(899, 48, "Pre-agent", 12, SLATE, 600),
        f'<rect x="1000" y="35" width="16" height="16" rx="4" fill="{BLUE}"/>',
        text(1024, 48, "Proactive", 12, BLUE, 600),
    ]
    metrics = [
        ("Operational availability", "91.393%", "99.661%", "+8.27 pp", True),
        ("Incident rate / 1,000 node-days", "18.56", "4.22", "−77.3%", False),
        ("P90 recovery time", "150.4 h", "33.1 h", "−78.0%", False),
        ("Recovered within 24 hours", "56.1%", "70.1%", "+14.0 pp", True),
        ("Triage coverage", "44.6%", "100.0%", "+55.4 pp", True),
        ("Unknown classification share", "56.1%", "4.1%", "−52.0 pp", False),
    ]
    x_label, x_before, x_after, x_change = 60, 640, 845, 1075
    parts.append(text(x_before, 112, "BEFORE", 11, SLATE, 700, "middle"))
    parts.append(text(x_after, 112, "AFTER", 11, BLUE, 700, "middle"))
    parts.append(text(x_change, 112, "CHANGE", 11, GREEN, 700, "middle"))
    for i, (label, before, after, change, _) in enumerate(metrics):
        y = 155 + i * 83
        fill = WHITE if i % 2 == 0 else PALE
        parts.append(f'<rect x="38" y="{y - 34}" width="1124" height="68" rx="12" fill="{fill}" stroke="{LIGHT}"/>')
        parts.append(text(x_label, y + 6, label, 15, NAVY, 650))
        parts.append(f'<rect x="{x_before - 70}" y="{y - 20}" width="140" height="40" rx="20" fill="#E2E8F0"/>')
        parts.append(text(x_before, y + 6, before, 15, NAVY, 700, "middle"))
        parts.append(f'<line x1="{x_before + 84}" y1="{y}" x2="{x_after - 86}" y2="{y}" stroke="#94A3B8" stroke-width="2" marker-end="url(#arrow)"/>')
        parts.append(f'<rect x="{x_after - 76}" y="{y - 20}" width="152" height="40" rx="20" fill="#DBEAFE"/>')
        parts.append(text(x_after, y + 6, after, 15, BLUE, 750, "middle"))
        parts.append(text(x_change, y + 6, change, 15, GREEN, 800, "middle"))
    parts.append(f'<rect x="38" y="630" width="1124" height="42" rx="10" fill="#EFF6FF"/>')
    parts.append(text(60, 656, "Incident-rate ratio 0.227 (95% CI 0.185–0.280); observational comparison, not a causal estimate.", 13, NAVY, 650))
    save_svg("figure-2-before-after", parts)


def failure_taxonomy() -> None:
    parts = svg_start(
        1200, 690,
        "H200 incident taxonomy and leading triggers",
        "A stacked bar shows the full-period incident categories and horizontal bars show the seven leading recorded triggers.",
    )
    parts += [
        text(44, 48, "H200 incident taxonomy and leading recorded triggers", 25, NAVY, 800),
        text(44, 75, "1,530 qualifying incidents · full observation window", 13, SLATE),
        text(44, 122, "Incident categories", 16, NAVY, 700),
    ]
    categories = [("Unknown", 44.6, SLATE), ("Hardware", 32.9, RED), ("Platform", 22.5, CYAN)]
    x, y, w, h = 44, 148, 1112, 58
    cursor = x
    for label, pct, color in categories:
        seg = w * pct / 100
        parts.append(f'<rect x="{cursor:.1f}" y="{y}" width="{seg:.1f}" height="{h}" fill="{color}"/>')
        parts.append(text(cursor + seg / 2, y + 25, label, 13, WHITE, 750, "middle"))
        parts.append(text(cursor + seg / 2, y + 45, f"{pct:.1f}%", 12, WHITE, 650, "middle"))
        cursor += seg
    parts.append(text(44, 249, "Leading triggers (share of all incidents)", 16, NAVY, 700))
    reasons = [
        ("NvidiaSmiLatencyTooLarge · unknown", 34.05, SLATE),
        ("OS auto-upgrade / network restart · platform", 8.43, CYAN),
        ("RecallForUpgrade · recorded hardware", 7.84, RED),
        ("PaiServicePodNotReady · unknown", 5.42, SLATE),
        ("NodeCrash · hardware", 4.38, RED),
        ("PaiServicePodNotReady · hardware", 4.05, RED),
        ("IBLinkFlap · hardware", 3.27, RED),
    ]
    max_value = max(v for _, v, _ in reasons)
    bx, bw = 430, 680
    for i, (label, value, color) in enumerate(reasons):
        yy = 292 + i * 48
        parts.append(text(44, yy + 18, label, 13, NAVY, 550))
        parts.append(f'<rect x="{bx}" y="{yy}" width="{bw}" height="24" rx="6" fill="#F1F5F9"/>')
        bar = bw * value / max_value
        parts.append(f'<rect x="{bx}" y="{yy}" width="{bar:.1f}" height="24" rx="6" fill="{color}"/>')
        parts.append(text(bx + bar + 10, yy + 18, f"{value:.2f}%", 12, color, 750))
    parts.append(f'<rect x="38" y="641" width="1124" height="34" rx="8" fill="#FFF7ED"/>')
    parts.append(text(54, 663, "Taxonomy caveat: some RecallForUpgrade and generic alerts require manual adjudication before causal/error-class claims.", 11, AMBER, 650))
    save_svg("figure-3-failure-taxonomy", parts)


def telemetry_coverage() -> None:
    parts = svg_start(
        1200, 520,
        "H200 telemetry coverage during the study window",
        "A timeline shows complete node-state coverage but later starts for agent evidence, findings, jobs, and GPU utilization telemetry.",
    )
    parts += [
        text(44, 48, "Telemetry coverage constrains what can be claimed", 25, NAVY, 800),
        text(44, 75, "Study window: 2026-02-15 to 2026-06-25 (131 days)", 13, SLATE),
    ]
    start_day, total_days = 0, 131
    x0, x1 = 310, 1145
    rows = [
        ("Node states / incidents", 0, 131, NAVY, "Full window"),
        ("Agent evidence", 67, 131, BLUE, "Apr 23–Jun 25"),
        ("Agent case memory", 92, 116, CYAN, "May 18–Jun 11"),
        ("Proactive findings", 100, 131, GREEN, "May 26–Jun 25"),
        ("Job outcomes", 103, 130, AMBER, "May 29–Jun 24"),
        ("GPU utilization", 104, 131, RED, "May 30–Jun 25"),
    ]
    ticks = [(0, "Feb 15"), (45, "Apr 1"), (75, "May 1"), (106, "Jun 1"), (131, "Jun 25")]
    for day, label in ticks:
        xx = x0 + (x1 - x0) * day / total_days
        parts.append(f'<line x1="{xx:.1f}" y1="112" x2="{xx:.1f}" y2="438" stroke="{LIGHT}" stroke-width="1"/>')
        parts.append(text(xx, 102, label, 11, SLATE, 550, "middle"))
    for i, (label, begin, end, color, period) in enumerate(rows):
        y = 132 + i * 52
        parts.append(text(44, y + 19, label, 13, NAVY, 650))
        parts.append(f'<rect x="{x0}" y="{y}" width="{x1 - x0}" height="25" rx="7" fill="#F1F5F9"/>')
        bx = x0 + (x1 - x0) * (begin - start_day) / total_days
        ex = x0 + (x1 - x0) * (end - start_day) / total_days
        parts.append(f'<rect x="{bx:.1f}" y="{y}" width="{ex - bx:.1f}" height="25" rx="7" fill="{color}"/>')
        parts.append(text(min(ex + 8, 1138), y + 18, period, 10, color, 650, "end" if ex > 1040 else "start"))
    parts.append(f'<rect x="38" y="455" width="1124" height="42" rx="10" fill="#F8FAFC" stroke="{LIGHT}"/>')
    parts.append(text(54, 481, "Before/after availability and incidents use full node-state coverage; utilization and job results are late-window support metrics only.", 12, NAVY, 600))
    save_svg("figure-4-telemetry-coverage", parts)


def main() -> None:
    rollout_trends()
    before_after()
    failure_taxonomy()
    telemetry_coverage()
    print(f"Generated figures in {OUT}")


if __name__ == "__main__":
    main()
