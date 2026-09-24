#!/usr/bin/env python3
"""Render header.svg (tmux session) and activity.svg (contribution graph).

Colours come from the SP Night contract (palette + roles), never from hex
literals: every element asks for a role, exactly like an SP Night port does.
Before anything is written, each text/surface pair that was drawn is measured
against the same contrast policy `spn check` enforces, and the build fails if
one falls short.

Needs GITHUB_TOKEN. Set SPN_PALETTE_DIR to a local sp-night/palette checkout to
render against it instead of the published contract. Stdlib only.
"""
import base64
import datetime as dt
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

USER = "rogeriojunior31"
ROOT = Path(__file__).resolve().parent.parent

# ── grid ─────────────────────────────────────────────────────────────────────
FS, CW, LH = 12, 7.2, 17          # font size, cell width (0.6em), line height
COLS = 120
PX, PY = 12, 10
W = round(PX * 2 + COLS * CW)


def x(col):
    return PX + col * CW


def y(row):
    """Baseline of a text row."""
    return PY + row * LH + 12.5


def mid_y(row):
    return PY + row * LH + LH / 2


def f(v):
    return f"{v:.1f}".rstrip("0").rstrip(".")


# ── SP Night contract ────────────────────────────────────────────────────────
CONTRACT = "https://sp-night.github.io"
FLAVOR = "noite"


def load_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER}), timeout=30) as r:
        return json.load(r)


class Theme:
    """Resolves roles to colours and records every text/surface pair drawn."""

    def __init__(self):
        local = os.environ.get("SPN_PALETTE_DIR")
        if local:
            palette = json.loads((Path(local) / "sp_night.json").read_text())
            self.roles = json.loads((Path(local) / "roles.json").read_text())
        else:
            palette, self.roles = load_json(f"{CONTRACT}/palette.json"), load_json(f"{CONTRACT}/roles.json")
        self.palette = palette
        self.colors = palette["flavors"][FLAVOR]["colors"]
        self.groups = palette["groups"]
        self.derived = {}   # hex -> how it was derived (mix), so the audit can allow it
        self.pairs = set()  # (fg role, bg role)

    def key(self, role):
        group, name = role.split(".")
        return self.roles[group][name]

    def hex(self, role):
        return self.colors[self.key(role)]

    def all_roles(self):
        return [f"{g}.{n}" for g, m in self.roles.items() if isinstance(m, dict)
                for n in m if not n.startswith("$")]

    def readable(self, dark, light, bg):
        """render/funcs.go `readable`: whichever of dark/light reads better on bg."""
        c = self.hex(bg)
        return dark if contrast(c, self.hex(dark)) >= contrast(c, self.hex(light)) else light

    def mix(self, t, a, b):
        """render/funcs.go `mix`: sRGB lerp, t=0 is a."""
        ca, cb = self.hex(a), self.hex(b)
        out = "#" + "".join(
            f"{int(int(ca[i:i + 2], 16) + (int(cb[i:i + 2], 16) - int(ca[i:i + 2], 16)) * t + 0.5):02x}"
            for i in (1, 3, 5))
        self.derived[out] = f"mix {t} {a} {b}"
        return out


T = None  # the Theme, set in main()


def luminance(h):
    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(h[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# internal/audit: the floor depends on the surface; fg_dim, fg_muted and fiacao override it
AA, LARGE_AA, NON_TEXT = 4.5, 3.0, 1.5
SURFACE_FLOOR = {"vao": AA, "laje": AA, "concreto": AA, "vidro": LARGE_AA}


def rule(fg, bg):
    """(floor, gate) for a palette-key pair, as audit.RuleFor applies it."""
    if fg == "fg_dim":
        return (AA if bg == "laje" else LARGE_AA), True
    if fg == "fg_muted":
        return LARGE_AA, False
    if fg == "fiacao":
        return NON_TEXT, False
    # text on an accent (a status-line block) is not in the policy; hold it to body text
    return SURFACE_FLOOR.get(bg, AA), True


def audit(svgs):
    """Measure every pair drawn and check no colour outside the contract leaked in."""
    fails, lines = 0, []
    for fg_role, bg_role in sorted(T.pairs):
        fg, bg = T.key(fg_role), T.key(bg_role)
        floor, gate = rule(fg, bg)
        ratio = contrast(T.hex(fg_role), T.hex(bg_role))
        ok = ratio >= floor
        fails += not ok and gate
        mark = "✓" if ok else ("✗" if gate else "!")
        lines.append(f"  {mark} {fg_role:<22} ({fg:<13}) on {bg_role:<12} ({bg:<8}) {ratio:5.2f}:1  floor {floor}")
    allowed = set(T.colors.values()) | set(T.derived)
    stray = sorted({h for s in svgs for h in re.findall(r"#[0-9a-fA-F]{6}\b", s)} - allowed)
    print("SP Night contrast audit", *lines, sep="\n")
    print(f"  {len(T.pairs)} pairs, {fails} below floor; derived: {sorted(T.derived.values())}")
    if stray:
        print(f"  ✗ colours outside the palette: {stray}")
    return fails == 0 and not stray


# ── drawing ──────────────────────────────────────────────────────────────────
def cls(role_spec):
    """'syntax.keyword b' -> 'syntax-keyword b'."""
    return " ".join(p.replace(".", "-") for p in role_spec.split())


def line(col, row, *segs, bg="ui.bg"):
    """One terminal line. segs: (role spec, text); a spec may add 'b' for bold."""
    spans = []
    for spec, text in segs:
        if text.strip():
            T.pairs.add((spec.split()[0], bg))
        spans.append(f'<tspan class="{cls(spec)}">{escape(text)}</tspan>')
    return f'<text x="{f(x(col))}" y="{f(y(row))}" xml:space="preserve">{"".join(spans)}</text>'


def prompt(col, row, cmd="", cursor=False):
    out = line(col, row, ("ui.fg_dim", "~ "), ("ui.accent b", "❯ "), ("ui.fg", cmd))
    if cursor:
        T.pairs.add(("ui.cursor", "ui.bg"))
        out += f'<rect class="cursor" x="{f(x(col + 4 + len(cmd)))}" y="{f(PY + row * LH + 2)}" width="{CW}" height="{LH - 4}"/>'
    return out


def hline(c0, c1, row, active, title=""):
    """A pane border with pane-border-status top; the focused pane takes ui.border_active."""
    stroke = "ui.border_active" if active else "ui.border"
    T.pairs.add((stroke, "ui.bg"))
    out = f'<line class="{cls(stroke)}-s" x1="{f(x(c0))}" y1="{f(mid_y(row))}" x2="{f(x(c1 + 1))}" y2="{f(mid_y(row))}"/>'
    if title:
        label = f" {title} "
        out += f'<rect fill="{T.hex("ui.bg")}" x="{f(x(c0 + 2))}" y="{f(PY + row * LH)}" width="{f(len(label) * CW)}" height="{LH}"/>'
        out += line(c0 + 2, row, ("ui.accent b" if active else "ui.fg_dim", label))
    return out


def vline(col, r0, r1):
    cx = x(col) + CW / 2
    return f'<line class="ui-border-s" x1="{f(cx)}" y1="{f(PY + r0 * LH)}" x2="{f(cx)}" y2="{f(PY + (r1 + 1) * LH)}"/>'


# The colour each language already gets from the SP Night eza port (per-extension accents).
LANG_ROLE = {"typescript": "syntax.keyword", "javascript": "syntax.attribute", "python": "syntax.keyword",
             "go": "syntax.function", "bash": "diagnostic.ok", "shell": "diagnostic.ok", "lua": "syntax.keyword",
             "rust": "syntax.tag"}


def lang(name, text=None):
    return (LANG_ROLE.get(name.lower(), "ui.fg"), text if text is not None else name)


# TOML, highlighted with the roles a tree-sitter TOML grammar asks for
def toml_table(name):
    return [("syntax.punctuation", "["), ("syntax.type", name), ("syntax.punctuation", "]")]


def toml_kv(key, pad, *value):
    return [("syntax.property", f"{key:<{pad}}"), ("syntax.operator", " = "), *value]


def toml_str(v):
    return ("syntax.string", f'"{v}"')


# ── data ─────────────────────────────────────────────────────────────────────
REPO = "nameWithOwner description stargazerCount pushedAt isFork primaryLanguage { name }"
QUERY = """
query($login: String!) {
  org: organization(login: "sp-night") {
    repositories(first: 20, privacy: PUBLIC, orderBy: {field: PUSHED_AT, direction: DESC}) { nodes { %(r)s } }
  }
%(featured)s
  user(login: $login) {
    createdAt location
    repositories(first: 20, privacy: PUBLIC, ownerAffiliations: OWNER,
      orderBy: {field: PUSHED_AT, direction: DESC}) { nodes { %(r)s } }
    repositoriesContributedTo(includeUserRepositories: true,
      contributionTypes: [COMMIT, PULL_REQUEST, REPOSITORY]) { totalCount }
    contributionsCollection {
      totalCommitContributions totalPullRequestContributions restrictedContributionsCount
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount weekday } }
      }
    }
  }
}"""


# Projects I created and maintain, in the order they are shown. A repo that is
# private (or not visible to the token) renders as "going public soon" and fills
# itself in from GitHub on the first render after it opens.
FEATURED = [
    {"title": "SP Night", "repo": "sp-night/sp-night.github.io", "org": "sp-night", "role": "creator & maintainer"},
    {"title": "lazyagents", "repo": f"{USER}/lazyagents", "role": "creator & maintainer"},
]
FEATURED_FIELDS = ("nameWithOwner isPrivate description homepageUrl url stargazerCount forkCount pushedAt "
                   "primaryLanguage { name } repositoryTopics(first: 6) { nodes { topic { name } } }")
QUERY = QUERY % {"r": REPO, "featured": "\n".join(
    f'  f{i}: repository(owner: "{p["repo"].split("/")[0]}", name: "{p["repo"].split("/")[1]}") {{ {FEATURED_FIELDS} }}'
    for i, p in enumerate(FEATURED))}


def fetch():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN is not set")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": {"login": USER}}).encode(),
        headers={"Authorization": f"bearer {token}", "User-Agent": USER},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.load(r)
    # a featured repo that is still private resolves to NOT_FOUND for any token but mine; that is a "soon" card
    errors = [e for e in body.get("errors", [])
              if not (e.get("type") == "NOT_FOUND" and re.fullmatch(r"f\d+", str(e.get("path", [""])[0])))]
    if errors:
        sys.exit(f"GraphQL error: {errors}")
    return body["data"]


def streaks(days):
    counts = [d["contributionCount"] for d in days]
    longest = run = 0
    for c in counts:
        run = run + 1 if c else 0
        longest = max(longest, run)
    # today may not have contributions yet; don't let that break the streak
    tail = counts[:-1] if counts and counts[-1] == 0 else counts
    current = 0
    for c in reversed(tail):
        if not c:
            break
        current += 1
    return current, longest


def stats(user, today):
    cc = user["contributionsCollection"]
    days = [d for w in cc["contributionCalendar"]["weeks"] for d in w["contributionDays"]]
    created = dt.date.fromisoformat(user["createdAt"][:10])
    months = (today.year - created.year) * 12 + today.month - created.month - (today.day < created.day)
    anchor = dt.date(created.year + (created.month + months - 1) // 12, (created.month + months - 1) % 12 + 1, created.day)
    current, longest = streaks(days)
    return {
        "created": created,
        "years": months // 12,
        "months": months % 12,
        "days_rem": (today - anchor).days,
        "total": cc["contributionCalendar"]["totalContributions"],
        "commits": cc["totalCommitContributions"],
        "prs": cc["totalPullRequestContributions"],
        "private": cc["restrictedContributionsCount"],
        "repos": user["repositoriesContributedTo"]["totalCount"],
        "location": user["location"] or "Brazil",
        "current": current,
        "longest": longest,
        "best": max(days, key=lambda d: d["contributionCount"]),
        "active_days": sum(1 for d in days if d["contributionCount"]),
        "days": len(days),
        "weeks": cc["contributionCalendar"]["weeks"],
    }


# repos that exist but have no description on GitHub yet
DESCRIPTIONS = {
    f"{USER}/Fine-tune-Gemma-models-in-Keras-using-LoRA": "fine-tuning Gemma in Keras with LoRA",
    f"{USER}/house_price_predictor": "house price regression model",
}
PORT_SUFFIX = "SP Night for "


def audit_pairs_per_flavor():
    """How many pairs `spn check` gates per flavour, derived from the palette like audit.Flavor does:
    4 surfaces x (fg, fg_vivo, accents, brights, fg_dim, fg_muted) + fiacao on 2 border surfaces."""
    g = T.palette["groups"]
    return 4 * (4 + len(g["accents"]["keys"]) + len(g["vivo"]["keys"])) + 2


def featured(data):
    """One card per FEATURED entry. Only public data is shown; nothing is invented for a private repo."""
    cards, featured_repos = [], {p["repo"] for p in FEATURED}
    for i, p in enumerate(FEATURED):
        r = data.get(f"f{i}")
        card = {"title": p["title"], "role": p["role"], "repo": p["repo"], "live": bool(r) and not r["isPrivate"]}
        if card["live"]:
            card.update(desc=r["description"] or "", url=r["homepageUrl"] or r["url"],
                        stars=r["stargazerCount"], forks=r["forkCount"], pushed=r["pushedAt"],
                        langs=[r["primaryLanguage"]["name"]] if r["primaryLanguage"] else [],
                        topics=[t["topic"]["name"] for t in r["repositoryTopics"]["nodes"]])
            if p.get("org") and data.get("org"):
                org = [o for o in data["org"]["repositories"]["nodes"] if not o["nameWithOwner"].endswith("/.github")]
                card["ports"] = sorted(o["nameWithOwner"].split("/")[1] for o in org if PORT_SUFFIX in (o["description"] or ""))
                card["stars"] = sum(o["stargazerCount"] for o in org)
                card["pushed"] = max(o["pushedAt"] for o in org)
                card["langs"] = sorted({o["primaryLanguage"]["name"] for o in org if o["primaryLanguage"]})
                card["desc"] = re.sub(r" — three flavours, \d+ colours$", "", card["desc"])
                card["theme"] = True
        cards.append(card)
    others = []
    for r in data["user"]["repositories"]["nodes"]:
        name = r["nameWithOwner"]
        if r["isFork"] or name in featured_repos or name == f"{USER}/{USER}":
            continue
        others.append(name.split("/")[1])
    return cards, others[:4]


def ago(iso, today):
    days = (today - dt.date.fromisoformat(iso[:10])).days
    if days < 1:
        return "today"
    if days < 30:
        return f"{days}d ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"


# ── svg scaffolding ──────────────────────────────────────────────────────────
def font_face():
    faces = []
    for weight, name in ((400, "Regular"), (700, "Bold")):
        data = base64.b64encode((ROOT / f"assets/fonts/JetBrainsMono-{name}.subset.woff2").read_bytes()).decode()
        faces.append(f"@font-face{{font-family:JBM;font-weight:{weight};src:url(data:font/woff2;base64,{data}) format('woff2')}}")
    return "".join(faces)


def base_css():
    roles = T.all_roles()
    return (
        f"text{{font-family:JBM,'JetBrains Mono',ui-monospace,monospace;font-size:{FS}px}}.b{{font-weight:700}}"
        + "".join(f".{cls(r)}{{fill:{T.hex(r)}}}" for r in roles)
        + f".ui-border-s{{stroke:{T.hex('ui.border')}}}.ui-border_active-s{{stroke:{T.hex('ui.border_active')}}}"
        + f".cursor{{fill:{T.hex('ui.cursor')};animation:blink 1s step-end infinite}}"
        + "@keyframes blink{50%{opacity:0}}"
    )


def svg(height, css, body, title):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" width="{W}" height="{height}" role="img">'
        f"<title>{escape(title)}</title>"
        f"<style>{font_face()}{base_css()}{css}</style>"
        f'<rect width="{W}" height="{height}" rx="10" fill="{T.hex("ui.bg")}"/>'
        f"{body}"
        f'<rect x=".5" y=".5" width="{W - 1}" height="{height - 1}" rx="10" fill="none" stroke="{T.hex("ui.border")}"/>'
        "</svg>\n"
    )


def status_bar(row, windows, right, animated):
    """tmux status line, mapped the way the Helix port maps its statusline:
    the bar is ui.panel, the session is a mode block on ui.cursor, the current
    window is the active workspace (ui.accent on ui.selection)."""
    top = PY + row * LH + 2
    h = LH + 4
    out = [f'<rect x="0" y="{f(top)}" width="{W}" height="{h + PY}" fill="{T.hex("ui.panel")}"/>',
           f'<rect x="0" y="{f(top)}" width="{W}" height="1" fill="{T.hex("ui.border")}"/>']
    by = top + h / 2 + 4.3

    def text(col_x, label, fg, bg):
        T.pairs.add((fg.split()[0], bg))
        return f'<text x="{f(col_x)}" y="{f(by)}" xml:space="preserve" class="{cls(fg)}">{escape(label)}</text>'

    def block(col_x, label, fg, bg):
        rect = f'<rect x="{f(col_x)}" y="{f(top + 3)}" width="{f(len(label) * CW)}" height="{h - 6}" rx="2" fill="{T.hex(bg)}"/>'
        return rect + text(col_x, label, fg, bg), len(label) * CW

    on_cursor = T.readable("ui.on_accent", "ui.fg_bright", "ui.cursor")
    s, wd = block(PX, " rogerio ", f"{on_cursor} b", "ui.cursor")
    out.append(s)
    cx = PX + wd + CW
    for i, name in enumerate(windows):
        label = f" {i}:{name} "
        out.append(text(cx, label, "ui.fg_dim", "ui.panel"))
        on, _ = block(cx, f" {i}:{name}* ", "ui.accent b", "ui.selection")
        out.append(f'<g class="win{i}">{on}</g>' if animated else on)
        cx += (len(label) + 2) * CW
    rx = W - PX
    for label, fg, bg in reversed(right):
        rx -= len(label) * CW
        out.append(block(rx, label, fg, bg)[0] if bg != "ui.panel" else text(rx, label, fg, bg))
        rx -= CW
    return "".join(out)


def status_right(today, *extra):
    on_alt = T.readable("ui.on_accent", "ui.fg_bright", "ui.accent_alt")
    return [*extra,
            (f" SP · {today.strftime('%d %b %Y')} ", "ui.accent", "ui.panel"),   # the clock is sodio
            (" archlinux ", f"{on_alt} b", "ui.accent_alt")]


# ── header.svg: tmux windows ─────────────────────────────────────────────────
ARCH = [  # neofetch Arch logo; "|" splits the two colours
    "                  -`", "                 .o+`", "                `ooo/",
    "               `+oooo:", "              `+oooooo:", "              -+oooooo+:",
    "            `/:-:++oooo+:", "           `/++++/+++++++:", "          `/++++++++++++++:",
    "         `/+++|ooooooooooooo/`", "        ./ooo|sssso++osssssso+`",
    "       .oossssso|-````/ossssss+`", "      -osssssso.|      :ssssssso.",
    "     :osssssss/|        osssso+++.", "    /ossssssss/|        +ssssooo/-",
    "  `/ossssso+/:-|        -:/+osssso+-", " `+sso+:-`|                 `.-/+oso:",
    " `++:.|                           `-/+/", " .`|                                 `/",
]


def win_fastfetch(s):
    """Returns (typed command, command col/row, pane frame svg, output svg)."""
    frame = [hline(0, COLS - 1, 0, True, "0 fastfetch")]
    o = []
    for i, raw in enumerate(ARCH):
        a, _, b = raw.partition("|")
        o.append(line(2, 3 + i, ("ansi.blue", a), ("ansi.cyan", b)))
    k = 44
    fg, dim = "ui.fg", "ui.fg_dim"
    info = [
        ("OS", [(fg, "Arch Linux x86_64")]),
        ("Host", [(fg, "CSU Digital"), (dim, "  (Software Engineer Specialist)")]),
        ("Kernel", [(fg, f"swe-specialist {s['years']}.{s['months']}.0-csu")]),
        ("Uptime", [(fg, f"{s['years']} years, {s['months']} months, {s['days_rem']} days")]),
        ("Packages", [(fg, f"{s['repos']} (git repos), {s['total']} (contribs/yr)")]),
        ("Shell", [(fg, "fish")]),
        ("Terminal", [(fg, "tmux")]),
        ("Theme", [(fg, "SP Night"), (dim, " [Noite Paulista] · sp-night.github.io")]),
        ("Font", [(fg, "JetBrains Mono")]),
        ("Role", [(fg, "Software Engineer Specialist")]),
        ("Focus", [(fg, "AI tooling · LLM apps · Backend · DevOps")]),
        ("Languages", [lang("TypeScript"), (dim, ", "), lang("JavaScript"), (dim, ", "),
                       lang("Python"), (dim, ", "), lang("Go"), (dim, ", "), lang("Bash")]),
        ("Interests", [(fg, "Linux, self-hosting, open source, CLI tools")]),
        ("Location", [(fg, f"{s['location']} · BR")]),
        ("Streak", [("diagnostic.ok", f"{s['current']} days"), (dim, f" (longest {s['longest']})")]),
        ("Locale", [(fg, "pt_BR.UTF-8, en_US.UTF-8")]),
    ]
    o.append(line(k, 3, ("ansi.blue b", "rogerio"), (fg, "@"), ("ansi.blue b", "archlinux")))
    o.append(line(k, 4, ("ui.fg_muted", "-" * 17)))
    for i, (key, val) in enumerate(info):
        o.append(line(k, 5 + i, ("ansi.blue b", key), (fg, ": "), *val))
    ansi = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"]
    for i, name in enumerate(ansi + [f"bright_{n}" for n in ansi]):
        r = 6 + len(info) + i // 8
        o.append(f'<rect x="{f(x(k + (i % 8) * 3))}" y="{f(PY + r * LH + 1)}" width="{f(3 * CW)}" '
                 f'height="{LH - 2}" fill="{T.hex("ansi." + name)}"/>')
    o.append(prompt(0, 25, cursor=True))
    return "fastfetch", (0, 1), "".join(frame), "".join(o)


def win_stack(s):
    split = 52
    frame = [hline(0, split - 1, 0, True, "1 ~/stack"), vline(split, 0, 26),
             hline(split + 1, COLS - 1, 0, False, "2 units"), hline(split + 1, COLS - 1, 15, False, "3 setup")]
    o = []
    # eza port: directory = syntax.keyword bold, tree punctuation = ui.fg_muted, files = ui.fg
    D, F, P = "syntax.keyword b", "ui.fg", "ui.fg_muted"
    tree = [
        [(D, "~/stack")],
        [(P, "├── "), (D, "languages")],
        [(P, "│   ├── "), lang("typescript"), (F, "  "), lang("javascript")],
        [(P, "│   ├── "), lang("python"), (F, "  "), lang("go")],
        [(P, "│   └── "), lang("bash")],
        [(P, "├── "), (D, "backend")],
        [(P, "│   ├── "), (F, "node.js  bun  nestjs")],
        [(P, "│   └── "), (F, "api-design  microservices")],
        [(P, "├── "), (D, "cloud-devops")],
        [(P, "│   ├── "), (F, "aws  azure  docker")],
        [(P, "│   └── "), (F, "github-actions  elastic-stack")],
        [(P, "├── "), (D, "ai-ml")],
        [(P, "│   ├── "), (F, "langchain  llamaindex")],
        [(P, "│   ├── "), (F, "pytorch  keras  lora-fine-tuning")],
        [(P, "│   └── "), (F, "ai-dev-tools  llm-apis")],
        [(P, "└── "), (D, "databases")],
        [(P, "    ├── "), (F, "postgresql  mysql")],
        [(P, "    └── "), (F, "mongodb  redis")],
    ]
    for i, segs in enumerate(tree):
        o.append(line(1, 3 + i, *segs))
    o.append(line(1, 22, (F, "5 directories, 26 files")))
    o.append(prompt(1, 24, cursor=True))

    c = split + 2
    o.append(prompt(c, 1, "systemctl --user list-units"))
    o.append(line(c, 2, ("ui.fg_bright b", f"{'UNIT':<24}{'ACTIVE':<8}{'SUB':<9}DESCRIPTION")))
    units = [
        ("ai-tooling.service", "active", "running", "building AI dev tools"),
        ("llm-apps.service", "active", "running", "RAG, agents, tuning"),
        ("backend.service", "active", "running", "APIs & distributed sys"),
        ("devops.service", "active", "running", "CI/CD, cloud & infra"),
        ("architecture.service", "active", "running", "design & code reviews"),
        ("self-hosting.service", "active", "running", "homelab & open source"),
        ("kaggle.timer", "active", "waiting", "ML experiments"),
        ("retro-gaming.timer", "active", "waiting", "nights & weekends"),
        ("coffee.service", "failed", "failed", "out of beans"),
    ]
    for i, (u, a, sub, d) in enumerate(units):
        state = "diagnostic.ok" if a == "active" else "diagnostic.error"
        o.append(line(c - 1, 3 + i, (state, "●"), (F, f"{u:<24}"), (state + ("" if a == "active" else " b"), f"{a:<8}"),
                      (F if a == "active" else state, f"{sub:<9}"), ("ui.fg_dim", d)))
    o.append(line(c, 4 + len(units), ("ui.fg_dim", f"{len(units)} loaded units listed.")))

    o.append(prompt(c, 16, "cat ~/.config/setup.toml"))
    setup = [("os", "arch linux"), ("shell", "fish"), ("mux", "tmux"),
             ("theme", "sp-night noite"), ("font", "jetbrains mono"), ("fuel", "coffee")]
    for i, (key, val) in enumerate(setup):
        o.append(line(c, 17 + i, *toml_kv(key, 5, toml_str(val))))
    return "eza --tree ~/stack", (1, 1), "".join(frame), "".join(o)


def win_about(s):
    frame = [hline(0, COLS - 1, 0, True, "1 pacman"), hline(0, COLS - 1, 18, False, "2 git")]
    o = []
    created, fg = s["created"], "ui.fg"
    rows = [
        ("Name", [("ui.fg_bright b", USER)]),
        ("Version", [(fg, f"{s['years']}.{s['months']}.{s['days_rem']}-1")]),
        ("Description", [(fg, "Software Engineer Specialist building AI tools and the backends behind them")]),
        ("Architecture", [(fg, "x86_64")]),
        ("URL", [("ui.link", f"https://github.com/{USER}")]),
        ("Licenses", [(fg, "Open Source Enthusiast")]),
        ("Groups", [(fg, "csu-digital")]),
        ("Provides", [(fg, "ai-tooling  llm-apps  backend  api-design  devops")]),
        ("Depends On", [lang("typescript"), (fg, "  "), lang("python"), (fg, "  "), lang("go"), (fg, "  "),
                        lang("bash"), (fg, "  coffee")]),
        ("Optional Deps", [(fg, "sp-night  linux-ricing  self-hosting  retro-games"), ("ui.fg_dim", "  [installed]")]),
        ("Required By", [(fg, "csu-digital  community  team")]),
        ("Install Date", [(fg, created.strftime("%a %d %b %Y"))]),
        ("Install Reason", [(fg, "Passion for building elegant solutions")]),
        ("Validated By", [(fg, f"{s['years']}+ years of shipping code · {s['total']} contributions last year")]),
    ]
    for i, (key, val) in enumerate(rows):
        o.append(line(1, 3 + i, ("ui.fg_bright b", f"{key:<15}"), ("syntax.punctuation", ": "), *val))

    # git's own decoration colours, through the terminal's ANSI slots (bold = bright)
    o.append(prompt(1, 19, "git log --oneline --graph career"))
    Y = "ansi.yellow"
    log = [
        ("c5d1a09", [("ansi.bright_cyan b", "HEAD -> "), ("ansi.bright_green b", "main"), (Y, ", "),
                     ("ansi.bright_red b", "csu-digital")], "feat: Software Engineer Specialist @ CSU Digital, building AI tools"),
        ("4f2c9a1", [("ansi.bright_red b", "kruzer-io")], "feat: Tech Lead @ Kruzer IO"),
        ("8b17e03", [("ansi.bright_red b", "samsung-sds")], "chore: previous chapter @ Samsung SDS"),
        ("d91c4e7", [], "feat: dive into ML (Gemma + LoRA, Kaggle notebooks)"),
        ("0a1b2c3", [("ansi.bright_yellow b", f"tag: v{created.year}")], f"init: hello, github ({created.isoformat()})"),
    ]
    for i, (sha, refs, msg) in enumerate(log):
        deco = [(Y, "("), *refs, (Y, ") ")] if refs else []
        o.append(line(1, 20 + i, ("ansi.red", "* "), (Y, sha + " "), *deco, (fg, msg)))
    o.append(prompt(1, 25, cursor=True))
    return "pacman -Qi rogerio", (1, 1), "".join(frame), "".join(o)


def win_links(s):
    split = 68
    frame = [hline(0, split - 1, 0, True, "1 links"), vline(split, 0, 26), hline(split + 1, COLS - 1, 0, False, "2 cowsay")]
    o = []
    # bat: grid = ui.border, line numbers = ui.fg_muted (the Helix port's ui.linenr)
    G, bar = "ui.border", "─" * 7
    T.pairs.add(("ui.border", "ui.bg"))
    o.append(line(1, 3, (G, bar + "┬" + "─" * (split - 10))))
    o.append(line(1, 4, (G, " " * 7 + "│ "), ("ui.fg", "File: "), ("ui.fg_bright b", "~/.config/links.toml")))
    o.append(line(1, 5, (G, bar + "┼" + "─" * (split - 10))))
    P = ("syntax.punctuation", ", ")
    toml = [
        [("syntax.comment", "# clickable versions live in the badges below")],
        toml_table("work"),
        toml_kv("github", 8, toml_str(f"github.com/{USER}")),
        toml_kv("linkedin", 8, toml_str("linkedin.com/in/rogerioqjunior")),
        toml_kv("kaggle", 8, toml_str("kaggle.com/maskara31")),
        toml_kv("company", 8, toml_str("linkedin.com/company/csu-digital")),
        [],
        toml_table("gaming"),
        toml_kv("steam", 8, toml_str("steamcommunity.com/id/melvindoooo")),
        toml_kv("retro", 8, toml_str("retroachievements.org/user/Doggy31")),
        [],
        toml_table("projects"),
        toml_kv("sp_night", 8, toml_str("sp-night.github.io")),
        [],
        toml_table("status"),
        toml_kv("location", 8, toml_str(s["location"])),
        toml_kv("talk_to", 8, ("syntax.punctuation", "["), toml_str("ai tools"), P, toml_str("backend"), P,
                toml_str("linux"), P, toml_str("retro games"), ("syntax.punctuation", "]")),
    ]
    for i, segs in enumerate(toml):
        o.append(line(1, 6 + i, ("ui.fg_muted", f"{i + 1:>4}   "), (G, "│ "), *segs))
    o.append(line(1, 6 + len(toml), (G, bar + "┴" + "─" * (split - 10))))
    o.append(prompt(1, 25, cursor=True))

    c = split + 2
    msg = "links are clickable below ↓"
    o.append(prompt(c, 1, 'cowsay "say hi"'))
    cow = [" " + "_" * (len(msg) + 2), f"< {msg} >", " " + "-" * (len(msg) + 2),
           "        \\   ^__^", "         \\  (oo)\\_______", "            (__)\\       )\\/\\",
           "                ||----w |", "                ||     ||"]
    for i, t in enumerate(cow):
        o.append(line(c, 3 + i, ("ui.fg", t)))
    return "bat ~/.config/links.toml", (1, 1), "".join(frame), "".join(o)


def header(s, today):
    windows = [("fastfetch", win_fastfetch), ("stack", win_stack), ("about", win_about), ("links", win_links)]
    n, slot, type_s, pause_s = len(windows), 7.0, 1.1, 0.35
    total = n * slot
    pct = lambda t: f"{100 * t / total:.3f}%"
    css, body = [], []
    for i, (_, build) in enumerate(windows):
        a, b = i * slot, (i + 1) * slot
        typed, (cc, cr), frame, out = build(s)
        tw = len(typed) * CW
        css.append(
            f"@keyframes win{i}{{0%{{opacity:{1 if i == 0 else 0}}}{'' if i == 0 else pct(a) + '{opacity:1}'}{pct(b)}{{opacity:0}}100%{{opacity:0}}}}"
            f".win{i}{{animation:win{i} {total}s step-end infinite}}"
            f"@keyframes typ{i}{{0%,{pct(a)}{{transform:translateX(0);opacity:1;animation-timing-function:steps({len(typed)},end)}}"
            f"{pct(a + type_s)}{{transform:translateX({f(tw)}px);opacity:1;animation-timing-function:step-end}}"
            f"{pct(a + type_s + pause_s)},100%{{transform:translateX({f(tw)}px);opacity:0}}}}"
            f".typ{i}{{animation:typ{i} {total}s linear infinite}}"
            f"@keyframes out{i}{{0%{{opacity:0}}{pct(a + type_s + pause_s)},100%{{opacity:1}}}}"
            f".out{i}{{animation:out{i} {total}s step-end infinite}}"
        )
        cover_x, top = x(cc + 4), PY + cr * LH
        body.append(
            f'<g class="win{i}">{frame}{prompt(cc, cr, typed)}'
            f'<g class="typ{i}"><rect x="{f(cover_x)}" y="{f(top)}" width="{f(tw + CW)}" height="{LH}" fill="{T.hex("ui.bg")}"/>'
            f'<rect x="{f(cover_x)}" y="{f(top + 2)}" width="{CW}" height="{LH - 4}" fill="{T.hex("ui.cursor")}"/></g>'
            f'<g class="out{i}">{out}</g></g>'
        )
    css.append(
        "@media (prefers-reduced-motion:reduce){[class^=win],[class^=typ],[class^=out]{animation:none!important}"
        "[class^=win]{opacity:0}.win0,[class^=out]{opacity:1}[class^=typ]{opacity:0}}"
    )
    body.append(status_bar(27, [name for name, _ in windows], status_right(today), animated=True))
    height = round(PY + 27 * LH + 2 + LH + 4 + PY)
    return svg(height, "".join(css), "".join(body),
               "rogerio@archlinux — tmux session with fastfetch, stack, about and links windows")


# ── projects.svg: what I build and maintain ──────────────────────────────────
FUTURE = {  # toilet's "future" font, redrawn: 3 rows of heavy box drawing per letter
    "a": ("┏━┓", "┣━┫", "╹ ╹"), "b": ("┏┓ ", "┣┻┓", "┗━┛"), "c": ("┏━╸", "┃  ", "┗━╸"),
    "d": ("╺┳┓", " ┃┃", "╺┻┛"), "e": ("┏━╸", "┣╸ ", "┗━╸"), "f": ("┏━╸", "┣╸ ", "╹  "),
    "g": ("┏━╸", "┃╺┓", "┗━┛"), "h": ("╻ ╻", "┣━┫", "╹ ╹"), "i": ("╻", "┃", "╹"),
    "j": ("  ╻", "  ┃", "┗━┛"), "k": ("╻┏ ", "┣┻┓", "╹ ╹"), "l": ("╻  ", "┃  ", "┗━╸"),
    "m": ("┏┳┓", "┃┃┃", "╹ ╹"), "n": ("┏┓╻", "┃┗┫", "╹ ╹"), "o": ("┏━┓", "┃ ┃", "┗━┛"),
    "p": ("┏━┓", "┣━┛", "╹  "), "q": ("┏━┓", "┃┓┃", "┗┻┛"), "r": ("┏━┓", "┣┳┛", "╹┗╸"),
    "s": ("┏━┓", "┗━┓", "┗━┛"), "t": ("╺┳╸", " ┃ ", " ╹ "), "u": ("╻ ╻", "┃ ┃", "┗━┛"),
    "v": ("╻ ╻", "┃┏┛", "┗┛ "), "w": ("╻ ╻", "┃╻┃", "┗┻┛"), "x": ("╻ ╻", "┏╋┛", "╹ ╹"),
    "y": ("╻ ╻", "┗┳┛", " ╹ "), "z": ("╺━┓", "┏━┛", "┗━╸"), " ": ("  ", "  ", "  "), "-": ("   ", "╺━╸", "   "),
}


def big(col, row, text, role):
    """Three-row title. 13px makes the box drawing exactly one 17px row tall, so the strokes join."""
    rows = ["".join(FUTURE.get(ch, FUTURE[" "])[i] for ch in text.lower()) for i in range(3)]
    T.pairs.add((role, "ui.bg"))
    return "".join(f'<text x="{f(x(col))}" y="{f(y(row + i))}" xml:space="preserve" class="{cls(role)} title">'
                   f"{escape(r)}</text>" for i, r in enumerate(rows)), round(len(rows[0]) * 13 * 0.6 / CW)


def chips(col, row, items):
    """[label] pills. items: (fg role, bg role, label)."""
    out, cx = [], col
    for fg, bg, label in items:
        text = f" {label} "
        out.append(f'<rect x="{f(x(cx))}" y="{f(PY + row * LH + 1.5)}" width="{f(len(text) * CW)}" height="{LH - 3}" rx="3" fill="{T.hex(bg)}"/>')
        out.append(line(cx, row, (fg, text), bg=bg))
        cx += len(text) + 1
    return "".join(out), cx


def wrap(text, width):
    lines, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    return lines + ([cur] if cur else [])


def numbers(col, row, items):
    segs = []
    for j, (num, label) in enumerate(items):
        segs += ([("ui.border", "  │  ")] if j else []) + [("ui.fg_bright b", str(num)), ("ui.fg_dim", " " + label)]
    return line(col, row, *segs)


# A few lines of the SP Night Neovim setup, highlighted with the theme's own syntax roles.
LUA = [
    [("syntax.comment", "-- the sodium lamp over the whole city")],
    [("syntax.keyword", "local "), ("syntax.variable", "palette "), ("syntax.operator", "= "), ("syntax.function", "require"),
     ("syntax.punctuation", "("), ("syntax.string", '"sp_night.palette"'), ("syntax.punctuation", ")")],
    [("syntax.keyword", "function "), ("syntax.namespace", "M"), ("syntax.punctuation", "."), ("syntax.function", "setup"),
     ("syntax.punctuation", "("), ("syntax.parameter", "opts"), ("syntax.punctuation", ")")],
    [("syntax.conditional", "  if "), ("syntax.variable", "vim"), ("syntax.punctuation", "."), ("syntax.property", "o"),
     ("syntax.punctuation", "."), ("syntax.property", "background "), ("syntax.operator", "~= "),
     ("syntax.string", '"dark" '), ("syntax.conditional", "then")],
    [("syntax.keyword", "    return "), ("syntax.constant", "nil"), ("syntax.punctuation", ", "),
     ("syntax.string", '"dark only, by decision"')],
    [("syntax.conditional", "  end")],
    [("syntax.keyword", "end")],
]


def card_live(c, c0, width, today):
    o, row = [], 3
    title, tw = big(c0 + 1, row, c["title"], "ui.accent")
    o.append(title)
    side = c0 + tw + 4
    s, _ = chips(side, row, [("ui.accent b", "ui.selection", c["role"])])
    o.append(s)
    o.append(line(side, row + 1, ("diagnostic.ok", "● "), ("ui.fg", "live"), ("ui.fg_dim", f" · updated {ago(c['pushed'], today)}")))
    s, _ = chips(side, row + 2, [(LANG_ROLE.get(n.lower(), "ui.fg"), "ui.selection", n.lower()) for n in c["langs"]])
    o.append(s)
    row += 4
    for t in wrap(c["desc"], width - 2):
        o.append(line(c0 + 1, row, ("ui.fg", t)))
        row += 1
    row += 1
    if c.get("theme"):
        g = T.palette["groups"]
        o.append(numbers(c0 + 1, row, [(len(T.palette["flavors"]), "flavours"), (len(T.colors), "colours"),
                                       (audit_pairs_per_flavor(), "pairs gated"), (len(c["ports"]), "ports")]))
        row += 2
        # the editor, on ui.panel with ui.linenr gutter, as the Helix port draws it
        top, h = PY + row * LH, len(LUA) * LH + 6
        o.append(f'<rect x="{f(x(c0 + 1))}" y="{f(top)}" width="{f((width - 2) * CW)}" height="{h}" rx="4" fill="{T.hex("ui.panel")}"/>')
        for i, segs in enumerate(LUA):
            o.append(line(c0 + 2, row + i, ("ui.fg_muted", f"{i + 1:>2} "), *segs, bg="ui.panel").replace(
                f'y="{f(y(row + i))}"', f'y="{f(y(row + i) + 3)}"', 1))
        row += len(LUA) + 1
        # the palette as one strip: every accent, then its bright pair
        keys = g["accents"]["keys"] + g["vivo"]["keys"]
        seg = (width - 2) * CW / len(keys)
        for k, key in enumerate(keys):
            o.append(f'<rect x="{f(x(c0 + 1) + k * seg)}" y="{f(PY + row * LH + 4)}" width="{f(seg + 0.3)}" height="{LH - 8}" fill="{T.colors[key]}"/>')
        row += 2
        s, _ = chips(c0 + 1, row, [("ui.fg", "ui.selection", p) for p in c["ports"]])
        o.append(s)
        row += 2
    else:
        o.append(numbers(c0 + 1, row, [(c["stars"], "stars"), (c["forks"], "forks")]))
        row += 2
        if c["topics"]:
            s, _ = chips(c0 + 1, row, [("syntax.type", "ui.selection", t) for t in c["topics"][:5]])
            o.append(s)
            row += 2
    o.append(line(c0 + 1, row, ("ui.fg_dim", "↗ "), ("ui.link", c["url"].removeprefix("https://"))))
    return "".join(o), row


def card_soon(c, c0, width):
    o, row = [], 3
    title, tw = big(c0 + 1, row, c["title"], "ui.fg_muted")   # the lamp is not lit yet
    o.append(title)
    row += 4
    s, _ = chips(c0 + 1, row, [("ui.accent b", "ui.selection", c["role"]), ("diagnostic.warn b", "ui.selection", "going public soon")])
    o.append(s)
    row += 2
    o.append(line(c0 + 1, row, ("ui.fg_dim", "README.md")))
    row += 1
    for i, widths in enumerate([(9, 22, 14), (31, 12), (), (18, 25), (27, 8, 11), (14,), (), (22, 16)]):
        cx = c0 + 1
        for wlen in widths:   # blurred text: blocks, not glyphs, so nothing about the repo is shown
            o.append(f'<rect x="{f(x(cx))}" y="{f(PY + (row + i) * LH + 5)}" width="{f(wlen * CW)}" height="{LH - 10}" rx="3" fill="{T.hex("ui.border")}"/>')
            cx += wlen + 1
    row += 9
    o.append(line(c0 + 1, row, ("ui.fg_dim", "this pane lights up on the first render")))
    row += 1
    o.append(line(c0 + 1, row, ("ui.fg_dim", "after "), ("ui.fg", c["repo"]), ("ui.fg_dim", " opens")))
    return "".join(o), row


def projects_svg(cards, others, today):
    split = 60
    body, bottoms = [], []
    for i, c in enumerate(cards[:2]):
        c0, width = (0, split) if i == 0 else (split + 1, COLS - split - 1)
        body.append(hline(c0, c0 + width - 1, 0, i == 0, f"{i + 1} {c['title'].lower().replace(' ', '-')}"))
        cmd = f"gh repo view {c['repo']}"
        body.append(prompt(c0 + 1, 1, cmd))
        part, bottom = card_live(c, c0 + 1, width - 2, today) if c["live"] else card_soon(c, c0 + 1, width - 2)
        body.append(part)
        bottoms.append(bottom)
    last = max(bottoms) + 1
    body.append(vline(split, 0, last))
    row = last + 1
    if others:
        body.append(hline(0, COLS - 1, row, False))
        row += 1
        body.append(line(1, row, ("syntax.comment", "# earlier experiments: "), ("ui.fg_dim", "  ·  ".join(others))))
        row += 1
    live = sum(c["live"] for c in cards)
    body.append(status_bar(row, ["projects"], status_right(today, (f" {live} live · {len(cards) - live} soon ", "ui.fg_dim", "ui.panel")),
                           animated=False))
    height = round(PY + row * LH + 2 + LH + 4 + PY)
    return svg(height, ".title{font-size:13px;font-weight:700}", "".join(body),
               "Projects I created and maintain: " + ", ".join(c["title"] for c in cards))


def readme_links(cards):
    """Clickable links for the live projects, between the projects markers in README.md."""
    badges = [f'  <a href="{c["url"]}"><img src="https://img.shields.io/badge/{c["title"].replace(" ", "_").replace("-", "--")}'
              f'-creator_%26_maintainer-{T.hex("ui.accent")[1:]}?style=flat-square&labelColor={T.hex("ui.bg")[1:]}" '
              f'alt="{escape(c["title"])}"/></a>' for c in cards if c["live"]]
    readme = ROOT / "README.md"
    text = readme.read_text()
    block = "<!-- projects:start -->\n<p align=\"center\">\n" + "\n".join(badges) + "\n</p>\n<!-- projects:end -->"
    new = re.sub(r"<!-- projects:start -->.*?<!-- projects:end -->", lambda _: block, text, flags=re.S)
    if new != text:
        readme.write_text(new)


# ── activity.svg: contribution graph ─────────────────────────────────────────
def level(count, peak):
    if not count:
        return 0
    return min(4, 1 + int(3 * count / max(peak, 1) + 0.5)) if peak > 3 else min(4, count)


def activity(s, today):
    # a contribution is an addition: git.added, stepped up from the background with the contract's `mix`
    levels = [T.hex("ui.line")] + [T.mix(t, "ui.bg", "git.added") for t in (0.3, 0.55, 0.8, 1)]
    o = [hline(0, COLS - 1, 0, True, "0 gh-dash"), prompt(0, 1, f"gh contribs {USER} --since 1y")]
    weeks = s["weeks"]
    peak = max(d["contributionCount"] for w in weeks for d in w["contributionDays"])
    cell, gap = 11.2, 2.8
    gx0, gy0 = x(5), PY + 4 * LH + 2
    T.pairs.add(("ui.fg_dim", "ui.bg"))
    last_month = None
    for wi, w in enumerate(weeks):
        first = dt.date.fromisoformat(w["contributionDays"][0]["date"])
        if first.month != last_month and first.day <= 7 and wi < len(weeks) - 2:
            last_month = first.month
            o.append(f'<text x="{f(gx0 + wi * (cell + gap))}" y="{f(y(3))}" class="ui-fg_dim">{first.strftime("%b")}</text>')
        for d in w["contributionDays"]:
            o.append(f'<rect x="{f(gx0 + wi * (cell + gap))}" y="{f(gy0 + d["weekday"] * (cell + gap))}" '
                     f'width="{cell}" height="{cell}" rx="2" fill="{levels[level(d["contributionCount"], peak)]}">'
                     f'<title>{d["date"]}: {d["contributionCount"]}</title></rect>')
    for wd, name in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        o.append(f'<text x="{f(x(1))}" y="{f(gy0 + wd * (cell + gap) + cell - 1.5)}" class="ui-fg_dim">{name}</text>')
    lx = gx0 + len(weeks) * (cell + gap) - 5 * (cell + gap) - 5 * CW
    ly = gy0 + 7 * (cell + gap) + 4
    o.append(f'<text x="{f(lx - 5 * CW)}" y="{f(ly + cell - 1.5)}" class="ui-fg_dim">Less</text>')
    for i, c in enumerate(levels):
        o.append(f'<rect x="{f(lx + i * (cell + gap))}" y="{f(ly)}" width="{cell}" height="{cell}" rx="2" fill="{c}"/>')
    o.append(f'<text x="{f(lx + 5 * (cell + gap) + 2)}" y="{f(ly + cell - 1.5)}" class="ui-fg_dim">More</text>')

    best = s["best"]
    stat_rows = [
        [("contributions", str(s["total"])), ("commits", str(s["commits"])),
         ("pull requests", str(s["prs"])), ("private", str(s["private"]))],
        [("current streak", f"{s['current']}d"), ("longest streak", f"{s['longest']}d"),
         ("active days", f"{s['active_days']}/{s['days']}"),
         ("best day", f"{best['date']} ({best['contributionCount']})")],
    ]
    for r, cols in enumerate(stat_rows):
        segs = []
        for j, (k, v) in enumerate(cols):
            if j:
                segs.append(("ui.border", "  │  "))
            segs += [("ui.fg_dim", k + " "), ("ui.fg_bright b", v)]
        o.append(line(1, 12 + r, *segs))
    o.append(prompt(0, 15, cursor=True))
    o.append(status_bar(16, ["activity"], status_right(today, (f" repos {s['repos']} ", "ui.fg_dim", "ui.panel")),
                        animated=False))
    height = round(PY + 16 * LH + 2 + LH + 4 + PY)
    return svg(height, "", "".join(o), f"{s['total']} contributions in the last year")


def main():
    global T
    T = Theme()
    today = dt.datetime.now(dt.timezone.utc).date()
    data = fetch()
    s = stats(data["user"], today)
    cards, others = featured(data)
    out = {"header.svg": header(s, today), "projects.svg": projects_svg(cards, others, today),
           "activity.svg": activity(s, today)}
    if not audit(out.values()):
        sys.exit("SP Night audit failed; nothing written")
    for name, content in out.items():
        (ROOT / name).write_text(content)
    readme_links(cards)
    print(f"ok: {s['total']} contributions, streak {s['current']}d, uptime {s['years']}y{s['months']}m")


if __name__ == "__main__":
    main()
