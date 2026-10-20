"""Look and feel shared by the Streamlit app and `mossbauer gui` (which passes THEME as --theme.* flags)."""
from __future__ import annotations

THEME = {
    "base": "light",
    "primaryColor": "#510C76",            # St. Thomas purple
    "backgroundColor": "#FFFFFF",
    "secondaryBackgroundColor": "#F5F5F7",  # cool-gray tint
    "textColor": "#1D1D22",
    "font": "sans serif",
}

TEXT_COLOR = THEME["textColor"]
MUTED_COLOR = "#63666A"                   # cool gray: secondary text
FE_COLOR = "#C2571A"                      # iron highlight in the 3D view and plots
PLOT_COLORS = ["#510C76", "#C2571A", "#1F7A5C", "#97999B", "#A88BC7"]  # purple, iron, emerald, cool gray, lavender
GRID_COLOR = "#ECECEF"
REF_LINE_COLOR = "#97999B"                # y = x guide


def theme_flags() -> list[str]:
    return [f"--theme.{k}={v}" for k, v in THEME.items()]


# status values (project.STATUSES) and row flags -> badge CSS class
BADGE_CLASSES = {"draft": "mb-draft", "approved": "mb-approved", "inputs written": "mb-inputs", "outputs found": "mb-outputs",
                 "analyzed": "mb-analyzed", "modified": "mb-modified", "stale": "mb-stale", "partial": "mb-partial",
                 "unreadable": "mb-unreadable", "results unreadable": "mb-unreadable", "older results": "mb-older", "older version": "mb-older"}


def badge_class(name: str | None) -> str:
    return BADGE_CLASSES.get(name or "", "mb-none")


CSS = """
<style>
.block-container {padding-top: 2.2rem; max-width: 1320px;}
@media (max-width: 1024px) {
  .block-container {padding-top: 4.5rem;}
}
h1, h2, h3 {letter-spacing: -0.01em;}
[data-testid="stSidebar"] h1 {font-size: 1.35rem; margin-bottom: 0;}
.mb-sub {color: #63666A; font-size: 0.85rem; margin-top: -0.3rem;}
.mb-badge {display: inline-block; padding: 0.1rem 0.55rem; border-radius: 999px; font-size: 0.78rem; font-weight: 600;
           letter-spacing: 0.02em; text-transform: uppercase;}
.mb-draft {background: #FFF1D6; color: #8A5300;}
.mb-approved {background: #DCEFE7; color: #155A43;}
.mb-modified {background: #FBE1E1; color: #9B1C1C;}
.mb-none {background: #EDEDF0; color: #56585C;}
.mb-inputs {background: #E4ECF8; color: #24508A;}
.mb-outputs {background: #E2F1F4; color: #1D6474;}
.mb-analyzed {background: #EDE4F3; color: #510C76;}
.mb-stale {background: #FCE8D8; color: #8E3F0E;}
.mb-partial {background: #FFF4CC; color: #7A5A00;}
.mb-unreadable {background: #F3D4D4; color: #7A1010;}
.mb-older {background: #EDEDF0; color: #56585C; font-style: italic;}
.mb-kv {font-size: 0.82rem; color: #63666A; margin: 0;}
.mb-kv b {color: #1D1D22; font-weight: 600;}
.mb-cli {font-size: 0.8rem; color: #63666A;}
.mb-cli code {font-size: 0.78rem;}
[data-testid="stMetricValue"] {font-size: 1.25rem;}
</style>
"""
