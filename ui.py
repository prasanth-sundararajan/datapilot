"""DataPilot look and feel: five-colour agent palette, CSS animations, small HTML helpers."""
from __future__ import annotations

import html

import streamlit as st

# Five colours, one per role. Used for chat cards, the pipeline bar, stat tiles and the title gradient.
VIOLET, SKY, EMERALD, AMBER, ROSE = "#8B5CF6", "#38BDF8", "#34D399", "#FBBF24", "#FB7185"
AGENT_COLOR = {"Planner": VIOLET, "Coder": SKY, "Executor": EMERALD, "Reviewer": AMBER, "user": ROSE, "User": ROSE}
AGENT_ICON = {"Planner": "🧭", "Coder": "💻", "Executor": "⚙️", "Reviewer": "🔍", "user": "👤", "User": "👤"}
STEPS = [("Ask", ROSE, "user"), ("Plan", VIOLET, "Planner"), ("Code", SKY, "Coder"),
         ("Run", EMERALD, "Executor"), ("Review", AMBER, "Reviewer"), ("Answer", ROSE, "answer")]

CSS = f"""
<style>
:root {{ --violet:{VIOLET}; --sky:{SKY}; --emerald:{EMERALD}; --amber:{AMBER}; --rose:{ROSE};
         --ink:#0B1020; --card:#151B33; --line:#262E52; --muted:#9AA3C7; }}

@keyframes dp-gradient {{ 0%{{background-position:0% 50%}} 50%{{background-position:100% 50%}} 100%{{background-position:0% 50%}} }}
@keyframes dp-rise {{ from{{opacity:0; transform:translateY(14px) scale(.98)}} to{{opacity:1; transform:none}} }}
@keyframes dp-pulse {{ 0%{{box-shadow:0 0 0 0 var(--c)}} 70%{{box-shadow:0 0 0 12px transparent}} 100%{{box-shadow:0 0 0 0 transparent}} }}
@keyframes dp-float {{ 0%,100%{{transform:translateY(0)}} 50%{{transform:translateY(-5px)}} }}
@keyframes dp-shine {{ from{{background-position:-200% 0}} to{{background-position:200% 0}} }}
@keyframes dp-spin {{ to{{transform:rotate(360deg)}} }}

.stApp {{ background:
    radial-gradient(1200px 500px at 10% -10%, rgba(139,92,246,.18), transparent 60%),
    radial-gradient(900px 500px at 100% 0%, rgba(56,189,248,.14), transparent 60%),
    radial-gradient(900px 600px at 50% 120%, rgba(251,113,133,.10), transparent 60%), var(--ink); }}
.block-container {{ padding-top: 2.2rem; max-width: 1180px; }}

/* hero */
.dp-hero {{ display:flex; align-items:center; gap:18px; margin-bottom:6px; }}
.dp-logo {{ width:58px; height:58px; border-radius:16px; display:grid; place-items:center; font-size:30px;
  background:linear-gradient(135deg,var(--violet),var(--sky),var(--emerald),var(--amber),var(--rose));
  background-size:300% 300%; animation:dp-gradient 6s ease infinite, dp-float 4s ease-in-out infinite;
  box-shadow:0 10px 30px rgba(139,92,246,.35); }}
.dp-title {{ font-size:2.6rem; font-weight:800; letter-spacing:-.02em; line-height:1.05; margin:0;
  background:linear-gradient(90deg,var(--violet),var(--sky),var(--emerald),var(--amber),var(--rose),var(--violet));
  background-size:300% 100%; -webkit-background-clip:text; background-clip:text;
  color:transparent !important; -webkit-text-fill-color:transparent;
  animation:dp-gradient 8s linear infinite; }}
.dp-sub {{ color:var(--muted); margin:4px 0 0; font-size:.98rem; }}

/* pipeline */
.dp-pipe {{ display:flex; flex-wrap:wrap; gap:8px; margin:18px 0 10px; }}
.dp-step {{ --c:#fff; display:flex; align-items:center; gap:8px; padding:7px 14px; border-radius:999px;
  border:1px solid color-mix(in srgb, var(--c) 45%, transparent); color:var(--c); font-weight:600; font-size:.86rem;
  background:color-mix(in srgb, var(--c) 10%, transparent); opacity:.55; transition:all .35s ease; }}
.dp-step .n {{ width:20px; height:20px; border-radius:50%; display:grid; place-items:center; font-size:.72rem;
  background:var(--c); color:var(--ink); }}
.dp-step.done {{ opacity:.95; }}
.dp-step.active {{ opacity:1; background:color-mix(in srgb, var(--c) 22%, transparent);
  animation:dp-pulse 1.4s ease-out infinite; transform:translateY(-2px); }}
.dp-arrow {{ color:var(--line); align-self:center; font-size:1.1rem; }}

/* chat cards: colour by agent via a marker span */
[data-testid="stChatMessage"] {{ animation:dp-rise .45s cubic-bezier(.2,.8,.2,1) both;
  background:linear-gradient(180deg, rgba(21,27,51,.92), rgba(16,21,42,.92)); border:1px solid var(--line);
  border-left:4px solid var(--line); border-radius:14px; padding:.75rem 1rem; margin-bottom:.6rem;
  box-shadow:0 6px 24px rgba(0,0,0,.25); }}
[data-testid="stChatMessage"]:has(.ag-Planner)  {{ border-left-color:var(--violet); }}
[data-testid="stChatMessage"]:has(.ag-Coder)    {{ border-left-color:var(--sky); }}
[data-testid="stChatMessage"]:has(.ag-Executor) {{ border-left-color:var(--emerald); }}
[data-testid="stChatMessage"]:has(.ag-error)    {{ border-left-color:var(--rose);
  background:linear-gradient(180deg, rgba(251,113,133,.10), rgba(16,21,42,.92)); }}
[data-testid="stChatMessage"]:has(.ag-Reviewer) {{ border-left-color:var(--amber); }}
[data-testid="stChatMessage"]:has(.ag-user), [data-testid="stChatMessage"]:has(.ag-User) {{ border-left-color:var(--rose); }}
.ag {{ display:inline-flex; align-items:center; gap:8px; font-weight:700; font-size:.95rem; margin-bottom:.25rem; }}
.ag .tag {{ font-size:.7rem; font-weight:700; padding:2px 9px; border-radius:999px; letter-spacing:.04em;
  color:var(--ink); background:var(--c); }}

/* stat tiles */
.dp-stats {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin:10px 0 4px; }}
@media (max-width: 700px) {{ .dp-stats {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
.dp-stat {{ --c:#fff; border-radius:14px; padding:12px 14px; border:1px solid color-mix(in srgb, var(--c) 40%, transparent);
  background:linear-gradient(135deg, color-mix(in srgb, var(--c) 18%, transparent), rgba(21,27,51,.6));
  animation:dp-rise .5s ease both; }}
.dp-stat .v {{ font-size:1.6rem; font-weight:800; color:var(--c); line-height:1.1; }}
.dp-stat .l {{ font-size:.78rem; color:var(--muted); text-transform:uppercase; letter-spacing:.06em; }}

/* answer card with animated gradient border */
.dp-answer {{ position:relative; border-radius:18px; padding:2px; margin:14px 0 8px;
  background:linear-gradient(120deg,var(--violet),var(--sky),var(--emerald),var(--amber),var(--rose),var(--violet));
  background-size:300% 300%; animation:dp-gradient 7s ease infinite, dp-rise .6s ease both; }}
.dp-answer > div {{ border-radius:16px; padding:18px 20px; background:#10162E; }}
.dp-answer h3 {{ margin:0 0 6px; font-size:1.15rem; color:var(--emerald); }}
.dp-answer .sec {{ margin-top:10px; }}
.dp-answer .sec b {{ color:var(--amber); font-size:.8rem; letter-spacing:.06em; text-transform:uppercase; }}
.dp-answer p {{ margin:.2rem 0 0; font-size:1.02rem; line-height:1.55; }}
.dp-next {{ margin-top:12px; padding:10px 12px; border-radius:12px; background:rgba(56,189,248,.08);
  border:1px dashed rgba(56,189,248,.45); color:var(--sky); }}

/* misc */
.dp-chip {{ display:inline-block; padding:3px 10px; border-radius:999px; font-size:.78rem; font-weight:600; margin:2px 4px 2px 0;
  color:var(--c); border:1px solid color-mix(in srgb, var(--c) 50%, transparent); background:color-mix(in srgb, var(--c) 12%, transparent); }}
.dp-loader {{ display:inline-block; width:14px; height:14px; border-radius:50%; vertical-align:-2px; margin-right:6px;
  border:2px solid var(--line); border-top-color:var(--sky); animation:dp-spin .8s linear infinite; }}
.stButton > button, .stDownloadButton > button {{ border-radius:12px; transition:transform .15s ease, box-shadow .2s ease; }}
.stButton > button:hover, .stDownloadButton > button:hover {{ transform:translateY(-2px); box-shadow:0 8px 20px rgba(139,92,246,.25); }}
.stButton > button[kind="primary"] {{ background:linear-gradient(90deg,var(--violet),var(--sky)); border:0; }}
[data-testid="stImage"] img {{ border-radius:12px; border:1px solid var(--line); animation:dp-rise .6s ease both; }}
[data-testid="stSidebar"] .dp-legend div {{ display:flex; align-items:center; gap:8px; margin:4px 0; font-size:.9rem; }}
[data-testid="stSidebar"] .dp-legend i {{ width:12px; height:12px; border-radius:4px; display:inline-block; }}
</style>
"""


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


def hero():
    st.markdown(
        """<div class="dp-hero"><div class="dp-logo">📊</div><div>
        <div class="dp-title">DataPilot</div>
        <p class="dp-sub">Ask a business question in plain English. A team of agents plans, writes Python, runs it,
        fixes its own errors and returns the answer with charts.</p></div></div>""",
        unsafe_allow_html=True,
    )


def pipeline_html(active: str | None = None, done: set[str] | None = None) -> str:
    done = done or set()
    parts = []
    for i, (label, color, key) in enumerate(STEPS, 1):
        cls = "active" if key == active else ("done" if key in done else "")
        parts.append(f'<div class="dp-step {cls}" style="--c:{color}"><span class="n">{i}</span>{label}</div>')
        if i < len(STEPS):
            parts.append('<span class="dp-arrow">›</span>')
    return f'<div class="dp-pipe">{"".join(parts)}</div>'


def agent_header(source: str, tag: str | None = None, error: bool = False) -> str:
    color = ROSE if error else AGENT_COLOR.get(source, SKY)
    marker = "ag-error" if error else f"ag-{source}"
    tag_html = f'<span class="tag" style="--c:{color}">{html.escape(tag)}</span>' if tag else ""
    name = "You" if source in ("user", "User") else source
    return f'<span class="ag {marker}" style="color:{color}">{html.escape(name)} {tag_html}</span>'


def stats_html(items: list[tuple[str, str, str]]) -> str:
    tiles = "".join(f'<div class="dp-stat" style="--c:{c};animation-delay:{i*80}ms"><div class="v">{html.escape(str(v))}</div>'
                    f'<div class="l">{html.escape(l)}</div></div>' for i, (v, l, c) in enumerate(items))
    return f'<div class="dp-stats">{tiles}</div>'


def answer_html(ans: dict) -> str:
    esc = lambda s: html.escape(s or "").replace("\n", "<br>")
    secs = f'<h3>✅ Answer</h3><p>{esc(ans.get("ANSWER"))}</p>'
    if ans.get("WHERE IT CAME FROM"):
        secs += f'<div class="sec"><b>Where it came from</b><p>{esc(ans["WHERE IT CAME FROM"])}</p></div>'
    if ans.get("FILES"):
        secs += f'<div class="sec"><b>Files</b><p>{esc(ans["FILES"])}</p></div>'
    if ans.get("SUGGESTED NEXT QUESTION"):
        secs += f'<div class="dp-next">💡 <b>Suggested next question:</b> {esc(ans["SUGGESTED NEXT QUESTION"])}</div>'
    return f'<div class="dp-answer"><div>{secs}</div></div>'


def legend_html() -> str:
    rows = "".join(f'<div><i style="background:{c}"></i>{n}</div>' for n, c in
                   [("Planner", VIOLET), ("Coder", SKY), ("Executor", EMERALD), ("Reviewer", AMBER), ("You / errors", ROSE)])
    return f'<div class="dp-legend">{rows}</div>'
