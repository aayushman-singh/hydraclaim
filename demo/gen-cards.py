"""Generate title/info cards for the demo video."""

from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

DEMO = Path(__file__).parent
W, H = 1920, 1080
BG = (10, 10, 15)
FG = (230, 230, 230)
ACCENT = (100, 180, 255)


def make_card(lines, title=None, font_size=32, title_size=56):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    title_font = ImageFont.truetype("C:/Windows/Fonts/consolab.ttf", title_size)
    body_font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", font_size)
    y = 80
    if title:
        draw.text((80, y), title, font=title_font, fill=ACCENT)
        y += title_size + 40
    for line in lines:
        draw.text((80, y), line, font=body_font, fill=FG)
        y += font_size + 12
    return img


intro = make_card(
    [
        "Graph memory that tracks conflicts, supersession, and abstention",
        "",
        "Local stack:",
        "  • LLM: llama.cpp  qwen3:8b  (CUDA)",
        "  • Graph: HydraDB via Docker",
        "  • Code: github.com/aayushman-singh/hydraclaim",
    ],
    title="HydraClaim",
    font_size=36,
)
intro.save(DEMO / "intro.png")

# Schema card: first 38 lines of schema.cypher
schema_text = (DEMO.parent / "hydraclaim" / "schema.cypher").read_text()
schema_lines = schema_text.splitlines()[:38]
schema = make_card(schema_lines, title="Graph model", font_size=22, title_size=44)
schema.save(DEMO / "schema.png")

bench_text = """Benchmark: 8 scenarios, 25 questions

Arm                        | Accuracy | Abstention P/R | Queries/q | p95 ms
---------------------------|----------|----------------|-----------|--------
Naive RAG                  |   0.240  |  0.000 / 0.000 |     0.9   |   3.7
Question Router            |   0.760  |  1.000 / 0.500 |     4.8   |  71.3
Always Deep                |   0.840  |  1.000 / 0.500 |     5.0   |  70.1
Router + Graph Probe       |   1.000  |  1.000 / 1.000 |     4.7   |  71.1

The graph probe turns vague coverage into a measurable signal,
so the system abstains, goes cheap, or goes deep — exactly when it should."""
bench = make_card(
    bench_text.splitlines(), title="Ablation results", font_size=26, title_size=44
)
bench.save(DEMO / "benchmark.png")

outro = make_card(
    [
        "HydraClaim",
        "github.com/aayushman-singh/hydraclaim",
        "",
        "Built end-to-end with a local LLM on llama.cpp",
    ],
    title="Thanks",
    font_size=40,
    title_size=56,
)
outro.save(DEMO / "outro.png")

print("cards generated")
