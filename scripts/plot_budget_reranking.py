"""Standalone scientific figures from saved metrics using ReportLab graphics.

Run with an interpreter containing the optional [plots] extra, or the bundled
Codex Python runtime. No application imports, network calls, or recomputation.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path

from reportlab.graphics import renderSVG
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Circle, Drawing, Rect, String
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.lib import colors
import reportlab

BLUE, GOLD, OLIVE = [colors.HexColor(s) for s in ("#265FA7", "#B57816", "#657737")]
INK, GRID = colors.HexColor("#253143"), colors.HexColor("#DADEE5")
NAMES = ("none", "all", "adaptive")
LABELS = ("None", "All", "Adaptive")
SVG_FONT_HACKS = {
    "Helvetica": {"family": "Arial, sans-serif"},
    "Helvetica-Bold": {"family": "Arial, sans-serif", "weight": "bold"},
}


def text(d, x, y, value, size=12, fill=INK, anchor="start", bold=False):
    d.add(
        String(
            x,
            y,
            value,
            fontName="Helvetica-Bold" if bold else "Helvetica",
            fontSize=size,
            fillColor=fill,
            textAnchor=anchor,
        )
    )


def base(width, height, title, subtitle):
    d = Drawing(width, height)
    d.add(Rect(0, 0, width, height, fillColor=colors.white, strokeColor=None))
    text(d, 36, height - 42, title, 23, bold=True)
    text(d, 36, height - 67, subtitle, 12)
    return d


def legend(d, x, y, entries):
    for label, color in entries:
        d.add(Rect(x, y - 1, 12, 8, fillColor=color, strokeColor=None))
        text(d, x + 19, y - 2, label, 12)
        x += len(label) * 7 + 43


def bars(d, data, categories, x, y, width, height, vmax, palette, fmt="%d", step=None):
    chart = VerticalBarChart()
    chart.x, chart.y, chart.width, chart.height = x, y, width, height
    chart.data, chart.categoryAxis.categoryNames = data, list(categories)
    chart.valueAxis.valueMin, chart.valueAxis.valueMax = 0, vmax
    chart.valueAxis.valueStep = step or max(1, math.ceil(vmax / 5))
    chart.valueAxis.visibleGrid, chart.valueAxis.gridStrokeColor = True, GRID
    chart.valueAxis.strokeColor = INK
    chart.categoryAxis.strokeColor = INK
    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.categoryAxis.labels.fontSize = 12
    chart.categoryAxis.labels.dy = -4
    chart.valueAxis.labels.fontSize = 10
    chart.barLabelFormat = fmt
    chart.barLabels.fontSize, chart.barLabels.fillColor = 11, INK
    chart.barLabels.nudge = 6
    chart.groupSpacing, chart.barSpacing = 18, 5
    chart.bars.strokeColor = None
    for i, color in enumerate(palette):
        chart.bars[i].fillColor = color
    d.add(chart)
    return chart


def summary_figure(real, analysis):
    d = base(
        1160,
        690,
        "Budget-aware on-demand reranking",
        "41 reviewed development queries | 102 documents / 2,212 chunks | CPU float32 | 3 repeats per strategy",
    )
    text(d, 65, 570, "Complete-evidence coverage (queries / 41)", 15, bold=True)
    legend(d, 65, 546, [("Seed Top-5", BLUE), ("Final context", GOLD)])
    bars(
        d,
        [[real["summary"][n][k] for n in NAMES] for k in ("top_complete", "context_complete")],
        LABELS,
        75,
        327,
        450,
        190,
        41,
        [BLUE, GOLD],
        step=10,
    )
    text(d, 655, 570, "Queries that execute BGE (%)", 15, bold=True)
    bars(
        d,
        [[real["summary"][n]["triggered_queries"] / 41 * 100 for n in NAMES]],
        LABELS,
        670,
        327,
        400,
        190,
        100,
        [OLIVE],
        fmt="%.1f",
        step=20,
    )
    text(
        d,
        655,
        545,
        f"Measured BGE calls: 123 -> {analysis['timing']['adaptive']['actual_calls']} (warmups excluded)",
        12,
    )
    text(d, 65, 263, "Real retrieval-to-context latency (seconds)", 15, bold=True)
    legend(d, 65, 240, [("P50", BLUE), ("P95", GOLD)])
    seconds = [[analysis["timing"][n][k] / 1000 for n in NAMES] for k in ("p50_ms", "p95_ms")]
    ymax = max(2, math.ceil(max(seconds[1]) / 2) * 2)
    bars(d, seconds, LABELS, 75, 90, 990, 125, ymax, [BLUE, GOLD], fmt="%.2f", step=2)
    text(
        d,
        65,
        38,
        "Gate: rerank if sparse/dense Top-5 overlap < 2. Timing includes the gate; query/BGE outputs are not cached.",
        12,
    )
    text(
        d,
        65,
        17,
        "Tuned development result; 5-fold grouped development validation: 35/41. Answer quality and held-out test not evaluated.",
        11,
    )
    return d


def frontier_figure(replay, selection, folds):
    d = base(
        1100,
        590,
        "Pre-rerank signals: quality vs execution budget",
        "Frozen-result replay only | all 14 preregistered one-feature thresholds | no inference latency claim",
    )
    plot = LinePlot()
    plot.x, plot.y, plot.width, plot.height = 95, 110, 870, 340
    plot.data = [
        [
            (r["trigger_rate"] * 100, r["context_complete"])
            for r in replay["grid"]
            if r["policy"]["family"] == family
        ]
        for family in ("overlap", "margin")
    ]
    plot.xValueAxis.valueMin, plot.xValueAxis.valueMax, plot.xValueAxis.valueStep = 0, 100, 20
    plot.yValueAxis.valueMin, plot.yValueAxis.valueMax, plot.yValueAxis.valueStep = 34, 38, 1
    plot.xValueAxis.labels.fontSize, plot.yValueAxis.labels.fontSize = 11, 11
    plot.yValueAxis.visibleGrid, plot.yValueAxis.gridStrokeColor = True, GRID
    for i, color in enumerate((BLUE, GOLD)):
        plot.lines[i].strokeColor = None
        marker = makeMarker("FilledCircle" if i == 0 else "Square")
        marker.size, marker.strokeColor, marker.fillColor = (
            9,
            color,
            color if i == 0 else colors.white,
        )
        plot.lines[i].symbol = marker
    d.add(plot)
    legend(
        d, 95, 482, [("Top-5 overlap threshold", BLUE), ("Normalized RRF margin threshold", GOLD)]
    )
    text(d, 530, 62, "Queries executing BGE (%)", 13, anchor="middle")
    text(d, 95, 462, "Complete-context queries / 41 (focused y-axis)", 12)
    selected = selection["selected"]
    x, y = 95 + 870 * selected["trigger_rate"], 110 + 340 * (selected["context_complete"] - 34) / 4
    d.add(Circle(x, y, 9, fillColor=None, strokeColor=INK, strokeWidth=1.6))
    text(
        d,
        x + 15,
        y + 13,
        f"Selected: {selected['trigger_count']}/41 calls, {selected['context_complete']}/41 covered",
        12,
        bold=True,
    )
    text(
        d,
        95,
        28,
        f"Grouped internal validation: {folds['context_complete']}/41 covered, {folds['trigger_count']}/41 calls; thresholds refit in each fold.",
        12,
    )
    return d


def cdf_figure(trials):
    d = base(
        1100,
        590,
        "Real latency distribution",
        "Empirical CDF | 123 measured executions / strategy | 41 unique reviewed development queries",
    )
    plot = LinePlot()
    plot.x, plot.y, plot.width, plot.height = 95, 110, 870, 340
    data = []
    for n in NAMES:
        values = sorted(r["total_ms"] / 1000 for r in trials if r["strategy"] == n)
        points = [(0, 0)]
        for i, value in enumerate(values):
            points.extend([(value, i / len(values) * 100), (value, (i + 1) / len(values) * 100)])
        data.append(points)
    plot.data = data
    xmax = math.ceil(max(p[0] for series in data for p in series) / 2) * 2
    plot.xValueAxis.valueMin, plot.xValueAxis.valueMax, plot.xValueAxis.valueStep = 0, xmax, 2
    plot.yValueAxis.valueMin, plot.yValueAxis.valueMax, plot.yValueAxis.valueStep = 0, 100, 20
    plot.xValueAxis.labels.fontSize, plot.yValueAxis.labels.fontSize = 11, 11
    plot.yValueAxis.visibleGrid, plot.yValueAxis.gridStrokeColor = True, GRID
    for i, color in enumerate((BLUE, GOLD, OLIVE)):
        plot.lines[i].strokeColor, plot.lines[i].strokeWidth = color, 2
        if i == 1:
            plot.lines[i].strokeDashArray = [5, 3]
    d.add(plot)
    legend(d, 95, 482, [("None", BLUE), ("All", GOLD), ("Adaptive", OLIVE)])
    text(d, 95, 462, "Executions completed (%)", 12)
    text(
        d,
        530,
        62,
        "Latency: query encoding + retrieval + gate + BGE (if triggered) + context (seconds)",
        12,
        anchor="middle",
    )
    text(
        d,
        95,
        28,
        "Startup/build/warmup and gold scoring excluded. Serial shared-machine observations; not a concurrent service benchmark.",
        12,
    )
    return d


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--experiment-dir", type=Path, required=True)
    p.add_argument("--analysis-dir", type=Path, required=True)
    args = p.parse_args()
    root, out = args.experiment_dir, args.analysis_dir
    paths = [out / (name + ".svg") for name in ("summary", "frontier", "latency-cdf")]
    if any(path.exists() for path in [*paths, out / "chart-data.json"]):
        p.error("plots must be new; preserve existing figures")

    def load(name):
        return json.loads((root / name).read_text(encoding="utf-8"))

    real, replay, selection, folds = [
        load(name)
        for name in ("real.json", "replay.json", "selection.json", "group-validation.json")
    ]
    analysis = json.loads((out / "analysis.json").read_text(encoding="utf-8"))
    trials = [
        json.loads(line)
        for line in (root / "real.trials.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    drawings = [
        summary_figure(real, analysis),
        frontier_figure(replay, selection, folds),
        cdf_figure(trials),
    ]
    for drawing, path in zip(drawings, paths):
        renderSVG.drawToFile(drawing, str(path), fontHacks=SVG_FONT_HACKS)
    data = {
        "engine": "ReportLab graphics " + reportlab.Version,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "development retrieval, no answer correctness or test evaluation",
        "summary": real["summary"],
        "timing": analysis["timing"],
        "thresholds": replay["grid"],
        "group_validation": {
            k: folds[k] for k in ("n", "top_complete", "context_complete", "trigger_count")
        },
        "cdf_ms": {n: sorted(t["total_ms"] for t in trials if t["strategy"] == n) for n in NAMES},
        "sources": analysis["inputs_sha256"],
    }
    with (out / "chart-data.json").open("x", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"figures": [str(path) for path in paths], "engine": data["engine"]}))


if __name__ == "__main__":
    main()
