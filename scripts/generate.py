#!/usr/bin/env python3
"""Render the profile: header.svg (a tmux tour) and the link bar under it (links/*.svg).

The header has four windows, one subject each, and no fact appears in two of them:
whoami (the person and the machine), stack (tools and focus), career (work history,
from the site's data/experience.yaml) and now (contributions, plus the newest
projects from the site's feed). Projects live on the site; the profile only points
at them.

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
import hashlib
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
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

# ── data ─────────────────────────────────────────────────────────────────────
QUERY = """
query($login: String!) {
  user(login: $login) {
    createdAt location
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

# The site is the source of truth for projects and career; the profile reads them from it.
SITE = f"https://{USER}.github.io"
SITE_FEED = f"{SITE}/en/index.xml"
SITE_EXPERIENCE = f"https://raw.githubusercontent.com/{USER}/{USER}.github.io/main/data/experience.yaml"


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
    if body.get("errors"):
        sys.exit(f"GraphQL error: {body['errors']}")
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
def load_text(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER}), timeout=30) as r:
        return r.read().decode()


def site_projects():
    """Project pages from the site's feed, newest first."""
    items = []
    for i in ET.fromstring(load_text(SITE_FEED)).iter("item"):
        if "/en/projects/" in i.findtext("link", ""):
            items.append({"title": i.findtext("title"), "date": parsedate_to_datetime(i.findtext("pubDate")).date(),
                          "desc": " ".join((i.findtext("description") or "").split())})
    if not items:
        sys.exit(f"no projects in {SITE_FEED}")
    return sorted(items, key=lambda p: p["date"], reverse=True)


def career():
    """The work entries of the site's data/experience.yaml. That file is a flat list of
    `key: "value"` maps; anything else in it means the format changed, so fail loudly."""
    entries = []
    for raw in load_text(SITE_EXPERIENCE).splitlines():
        text = raw.rstrip()
        if not text.strip() or text.lstrip().startswith("#"):
            continue
        m = re.fullmatch(r'(- |  )(\w+): "(.*)"', text)
        if not m:
            sys.exit(f"experience.yaml: unexpected line {raw!r}")
        if m[1] == "- ":
            entries.append({})
        entries[-1][m[2]] = m[3]
    en = lambda e, k: e.get(f"{k}_en") or e.get(k, "")
    return [{"place": e["place"], "since": en(e, "time").split(" - ")[0], "title": en(e, "title"),
             "note": en(e, "subtitle")} for e in entries if e.get("category") == "work"]

# ── svg scaffolding ──────────────────────────────────────────────────────────
def font_face(weights=(400, 700)):
    faces = []
    for weight, name in ((400, "Regular"), (700, "Bold")):
        if weight not in weights:
            continue
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


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def win_whoami(s, site):
    """Returns (typed command, command col/row, pane frame svg, output svg).
    The person and the machine; job, tools and numbers each have their own window."""
    frame = [hline(0, COLS - 1, 0, True, "0 fastfetch")]
    o = []
    for i, raw in enumerate(ARCH):
        a, _, b = raw.partition("|")
        o.append(line(2, 3 + i, ("ansi.blue", a), ("ansi.cyan", b)))
    k, top = 44, 6
    fg, dim = "ui.fg", "ui.fg_dim"
    info = [
        ("OS", [(fg, "Arch Linux x86_64")]),
        ("Shell", [(fg, "fish")]),
        ("Terminal", [(fg, "tmux")]),
        ("Font", [(fg, "JetBrains Mono")]),
        ("Theme", [(fg, "SP Night"), (dim, " [Noite Paulista] · my colour scheme")]),
        ("Uptime", [(fg, f"{plural(s['years'], 'year')}, {plural(s['months'], 'month')} on GitHub")]),
        ("Location", [(fg, s["location"])]),
        ("Locale", [(fg, "pt_BR.UTF-8, en_US.UTF-8")]),
        ("Interests", [(fg, "Linux, self-hosting, CLI tools")]),
        ("Off-hours", [(fg, "retro games")]),
    ]
    o.append(line(k, top, ("ansi.blue b", "rogerio"), (fg, "@"), ("ansi.blue b", "archlinux")))
    o.append(line(k, top + 1, ("ui.fg_muted", "-" * 17)))
    for i, (key, val) in enumerate(info):
        o.append(line(k, top + 2 + i, ("ansi.blue b", key), (fg, ": "), *val))
    ansi = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"]
    for i, name in enumerate(ansi + [f"bright_{n}" for n in ansi]):
        r = top + 3 + len(info) + i // 8
        o.append(f'<rect x="{f(x(k + (i % 8) * 3))}" y="{f(PY + r * LH + 1)}" width="{f(3 * CW)}" '
                 f'height="{LH - 2}" fill="{T.hex("ansi." + name)}"/>')
    o.append(prompt(0, 25, cursor=True))
    return "fastfetch", (0, 1), "".join(frame), "".join(o)


def win_stack(s, site):
    """What I build with (the tree, grouped like the site's resume) and where the time goes (units)."""
    split = 52
    frame = [hline(0, split - 1, 0, True, "1 ~/stack"), vline(split, 0, 26), hline(split + 1, COLS - 1, 0, False, "2 focus")]
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
        [(P, "│   ├── "), (F, "langchain  llamaindex  llm-apis")],
        [(P, "│   └── "), (F, "pytorch  keras  lora-fine-tuning")],
        [(P, "└── "), (D, "databases")],
        [(P, "    └── "), (F, "postgresql  mysql  mongodb  redis")],
    ]
    for i, segs in enumerate(tree):
        o.append(line(1, 3 + i, *segs))
    o.append(line(1, 20, (F, "5 directories, 25 tools")))
    o.append(prompt(1, 25, cursor=True))

    c = split + 2
    o.append(prompt(c, 1, "systemctl --user list-units"))
    o.append(line(c, 3, ("ui.fg_bright b", f"  {'UNIT':<22}{'SUB':<9}DESCRIPTION")))
    units = [
        ("ai-tooling.service", "running", "dev tools built on LLMs"),
        ("llm-apps.service", "running", "RAG, agents, fine-tuning"),
        ("backend.service", "running", "APIs and distributed systems"),
        ("devops.service", "running", "CI/CD, cloud, infra"),
        ("open-source.service", "running", "themes, TUIs and desktop apps"),
        ("kaggle.timer", "waiting", "ML experiments"),
        ("coffee.service", "failed", "out of beans"),
    ]
    for i, (u, sub, d) in enumerate(units):
        ok = sub != "failed"
        state = "diagnostic.ok" if ok else "diagnostic.error"
        o.append(line(c, 4 + i, (state, "● "), (F, f"{u:<22}"), (F if ok else state + " b", f"{sub:<9}"), ("ui.fg_dim", d)))
    o.append(line(c, 5 + len(units), ("ui.fg_dim", f"{len(units)} loaded units listed.")))
    return "eza --tree ~/stack", (1, 1), "".join(frame), "".join(o)


def win_career(s, site):
    """Work history from the site, as git log --graph (git's decoration colours, through ANSI)."""
    frame = [hline(0, COLS - 1, 0, True, "2 git")]
    o, Y, G = [], "ansi.yellow", "ansi.red"
    jobs, row = site["career"], 3
    for i, job in enumerate(jobs):
        rows = 4 + bool(job["note"])
        if row + rows > 24:   # keep the prompt free; the oldest entries drop off first
            break
        sha = hashlib.sha1(f"{job['place']}{job['since']}".encode()).hexdigest()[:7]
        ref = re.sub(r"[^a-z0-9]+", "-", job["place"].lower()).strip("-")
        refs = [("ansi.bright_cyan b", "HEAD -> "), ("ansi.bright_green b", "main"), (Y, ", ")] if i == 0 else []
        refs.append(("ansi.bright_yellow b", f"tag: v{job['since'][-4:]}") if i == len(jobs) - 1 else ("ansi.bright_red b", ref))
        # "Freelancer @ Freelance" says it twice
        at = [] if job["title"].lower().startswith(job["place"].lower()) else [("ui.fg", f" @ {job['place']}")]
        o.append(line(1, row, (G, "* "), (Y, f"commit {sha} ("), *refs, (Y, ")")))
        o.append(line(1, row + 1, (G, "| "), ("ui.fg_dim", f"Date:   {job['since']}")))
        o.append(line(1, row + 2, (G, "| "), ("ui.fg_bright b", f"    {job['title']}"), *at))
        if job["note"]:
            o.append(line(1, row + 3, (G, "| "), ("ui.fg_dim", f"    {job['note']}")))
        if i < len(jobs) - 1:
            o.append(line(1, row + rows - 1, (G, "|")))
        row += rows
    o.append(prompt(0, 25, cursor=True))
    return "git log --graph career", (0, 1), "".join(frame), "".join(o)


def level(count, peak):
    if not count:
        return 0
    return min(4, 1 + int(3 * count / max(peak, 1) + 0.5)) if peak > 3 else min(4, count)


def win_now(s, site):
    """This year's contributions, then what shipped lately on the site. The only window with numbers."""
    # a contribution is an addition: git.added, stepped up from the background with the contract's `mix`
    levels = [T.hex("ui.line")] + [T.mix(t, "ui.bg", "git.added") for t in (0.3, 0.55, 0.8, 1)]
    frame = [hline(0, COLS - 1, 0, True, "1 gh-dash"), hline(0, COLS - 1, 17, False, "2 site")]
    o, weeks = [], s["weeks"]
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
        o.append(line(1, 13 + r, *segs))

    o.append(prompt(0, 18, f"curl -s {SITE_FEED.removeprefix('https://')} | rss --latest 3"))
    room = COLS - 34
    for i, p in enumerate(site["projects"][:3]):
        desc = p["desc"].split(":")[0].rstrip(".")
        if len(desc) > room:
            desc = desc[:room - 3].rsplit(" ", 1)[0] + "..."
        o.append(line(1, 19 + i, ("ui.accent b", "● "), ("ui.fg_dim", p["date"].strftime("%d %b %Y") + "  "),
                      ("syntax.keyword b", f"{p['title']:<13}"), ("ui.fg", desc)))
    o.append(line(1, 23, ("ui.fg_dim", "↗ "), ("ui.link", SITE.removeprefix("https://")),
                  ("ui.fg_dim", "  projects, docs and resume live there")))
    o.append(prompt(0, 25, cursor=True))
    return f"gh contribs {USER} --since 1y", (0, 1), "".join(frame), "".join(o)


# name, builder, what tmux's display-message says when this window is next
WINDOWS = [
    ("whoami", win_whoami, "who I am and what I run"),
    ("stack", win_stack, "what I build with"),
    ("career", win_career, "where I have worked"),
    ("now", win_now, "this year's commits and what I shipped"),
]
SLOT, TYPE_S, PAUSE_S, REVEAL_S, MSG_S = 8.0, 1.0, 0.25, 0.9, 1.7


def header(s, site, today):
    """Each window types its command, prints its output one row per step, fills a progress line
    under its tab, and hands over with a display-message naming the next window."""
    n = len(WINDOWS)
    total = n * SLOT
    pct = lambda t: f"{100 * t / total:.3f}%"
    pane_top, pane_rows = PY + 2 * LH, 24
    bg = T.hex("ui.bg")
    css, body = [], []
    for i, (_, build, _) in enumerate(WINDOWS):
        a, b = i * SLOT, (i + 1) * SLOT
        typed, (cc, cr), frame, out = build(s, site)
        tw = len(typed) * CW
        t0 = a + TYPE_S + PAUSE_S
        css.append(
            f"@keyframes win{i}{{0%{{opacity:{1 if i == 0 else 0}}}{'' if i == 0 else pct(a) + '{opacity:1}'}{pct(b)}{{opacity:0}}100%{{opacity:0}}}}"
            f".win{i}{{animation:win{i} {total}s step-end infinite}}"
            f"@keyframes typ{i}{{0%,{pct(a)}{{transform:translateX(0);opacity:1;animation-timing-function:steps({len(typed)},end)}}"
            f"{pct(a + TYPE_S)}{{transform:translateX({f(tw)}px);opacity:1;animation-timing-function:step-end}}"
            f"{pct(t0)},100%{{transform:translateX({f(tw)}px);opacity:0}}}}"
            f".typ{i}{{animation:typ{i} {total}s linear infinite}}"
            f"@keyframes cov{i}{{0%,{pct(t0)}{{transform:translateY(0);animation-timing-function:steps({pane_rows},end)}}"
            f"{pct(t0 + REVEAL_S)},100%{{transform:translateY({pane_rows * LH}px)}}}}"
            f".cov{i}{{animation:cov{i} {total}s linear infinite}}"
            f"@keyframes prg{i}{{0%,{pct(a)}{{transform:scaleX(0)}}{pct(b)},100%{{transform:scaleX(1)}}}}"
            f".prg{i}{{transform-box:fill-box;transform-origin:left;animation:prg{i} {total}s linear infinite}}"
            f"@keyframes msg{i}{{0%{{opacity:0}}{pct(b - MSG_S)}{{opacity:1}}{'' if b >= total else pct(b) + '{opacity:0}'}}}"
            f".msg{i}{{animation:msg{i} {total}s step-end infinite}}"
        )
        cover_x, top = x(cc + 4), PY + cr * LH
        # the output sits under a background-coloured cover that steps down one row at a time;
        # the nested <svg> clips it to the pane so it never covers the status bar
        body.append(
            f'<g class="win{i}"><g>{out}</g>'
            f'<svg x="0" y="{f(pane_top)}" width="{W}" height="{pane_rows * LH}" overflow="hidden">'
            f'<rect class="cov{i}" width="{W}" height="{pane_rows * LH}" fill="{bg}"/></svg>'
            f'{frame}{prompt(cc, cr, typed)}'
            f'<g class="typ{i}"><rect x="{f(cover_x)}" y="{f(top)}" width="{f(tw + CW)}" height="{LH}" fill="{bg}"/>'
            f'<rect x="{f(cover_x)}" y="{f(top + 2)}" width="{CW}" height="{LH - 4}" fill="{T.hex("ui.cursor")}"/></g></g>'
        )
    css.append(
        "@media (prefers-reduced-motion:reduce){[class^=win],[class^=typ],[class^=cov],[class^=prg],[class^=msg]{animation:none!important}"
        "[class^=win],[class^=msg]{opacity:0}.win0{opacity:1}[class^=typ],[class^=cov]{display:none}}"
    )

    row = 27
    names = [name for name, _, _ in WINDOWS]
    body.append(status_bar(row, names, status_right(today), animated=True))
    # progress under the active tab, laid out exactly like status_bar lays out the tabs
    stop = PY + row * LH + 2
    cx = PX + len(" rogerio ") * CW + CW
    for i, name in enumerate(names):
        body.append(f'<g class="win{i}"><rect class="prg{i}" x="{f(cx)}" y="{f(stop + LH + 1)}" '
                    f'width="{f(len(f" {i}:{name}* ") * CW)}" height="2" fill="{T.hex("ui.accent")}"/></g>')
        cx += (len(f" {i}:{name} ") + 2) * CW
    # tmux's display-message: message-style is yellow (ui.match) and takes over the whole status line
    on_msg = T.readable("ui.on_accent", "ui.fg_bright", "ui.match")
    T.pairs.add((on_msg, "ui.match"))
    for i in range(n):
        j = (i + 1) % n
        key, rest = " C-b n ", f" ❯  {j}:{names[j]}  ·  {WINDOWS[j][2]}"
        body.append(f'<g class="msg{i}"><rect x="0" y="{f(stop)}" width="{W}" height="{LH + 4 + PY}" fill="{T.hex("ui.match")}"/>'
                    f'<text x="{f(PX)}" y="{f(stop + (LH + 4) / 2 + 4.3)}" xml:space="preserve" class="{cls(on_msg)}">'
                    f'<tspan class="b">{escape(key)}</tspan>{escape(rest)}</text></g>')
    height = round(PY + row * LH + 2 + LH + 4 + PY)
    return svg(height, "".join(css), "".join(body),
               "rogerio@archlinux — tmux tour: whoami, stack, career and now windows")

# ── links: the bar under the header ──────────────────────────────────────────
# GitHub makes a whole image one link, so the bar is one small SVG per link, set side by
# side between the links markers in README.md. Each group opens with a coloured block,
# like a tmux status segment.
LINK_GROUPS = [
    ("me", "ui.accent", [("site", SITE), ("about", f"{SITE}/en/about/"), ("resume", f"{SITE}/en/resume/")]),
    ("talk", "ui.accent_alt", [("linkedin", "https://www.linkedin.com/in/rogerioqjunior/"),
                               ("email", "mailto:rogerio.junior20@outlook.com"),
                               ("kaggle", "https://www.kaggle.com/maskara31")]),
    ("play", "ui.match", [("steam", "https://steamcommunity.com/id/melvindoooo/"),
                          ("retro", "https://retroachievements.org/user/Doggy31")]),
]
LINK_H = LH + 10


def link_svg(label, fg, bg, edge="", bold=False):
    """One slice of the bar. edge 'l'/'r' rounds that outer end; joints stay square so slices meet."""
    w = round((len(label) + 3) * CW)
    T.pairs.add((fg, bg))
    panel, r = T.hex("ui.panel"), 6
    shape = f'<rect width="{w}" height="{LINK_H}" fill="{panel}"/>'
    if edge:
        shape = (f'<rect width="{w}" height="{LINK_H}" rx="{r}" fill="{panel}"/>'
                 f'<rect x="{0 if edge == "r" else w - r}" width="{r}" height="{LINK_H}" fill="{panel}"/>')
    block = "" if bg == "ui.panel" else \
        f'<rect x="{f(CW / 2)}" y="4" width="{f(w - CW)}" height="{LINK_H - 8}" rx="2" fill="{T.hex(bg)}"/>'
    weight = 700 if bold else 400
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {LINK_H}" width="{w}" height="{LINK_H}" role="img">'
            f"<title>{escape(label)}</title>"
            f"<style>{font_face((weight,))}text{{font-family:JBM,'JetBrains Mono',ui-monospace,monospace;font-size:{FS}px;"
            f"font-weight:{weight};fill:{T.hex(fg)}}}</style>"
            f'{shape}{block}<text x="{f(1.5 * CW)}" y="{f(LINK_H / 2 + 4.3)}" xml:space="preserve">{escape(label)}</text></svg>\n')


def links():
    """{path: svg} for every slice, and the README block that sets them side by side (no whitespace
    between images, or GitHub leaves gaps)."""
    files, html = {}, []
    for gi, (label, role, items) in enumerate(LINK_GROUPS):
        on = T.readable("ui.on_accent", "ui.fg_bright", role)
        path = f"links/{label}.svg"
        files[path] = link_svg(label, on, role, "l" if gi == 0 else "", bold=True)
        html.append(f'<img src="{path}" alt="{label}:"/>')
        for li, (name, url) in enumerate(items):
            last = gi == len(LINK_GROUPS) - 1 and li == len(items) - 1
            path = f"links/{name}.svg"
            files[path] = link_svg(name, "ui.fg", "ui.panel", "r" if last else "")
            html.append(f'<a href="{url}"><img src="{path}" alt="{name}"/></a>')
    return files, "".join(html)


def readme_links(html):
    readme = ROOT / "README.md"
    text = readme.read_text()
    block = f'<!-- links:start -->\n<p align="center">\n  {html}\n</p>\n<!-- links:end -->'
    new = re.sub(r"<!-- links:start -->.*?<!-- links:end -->", lambda _: block, text, flags=re.S)
    if new == text and "<!-- links:start -->" not in text:
        sys.exit("README.md has no links markers")
    if new != text:
        readme.write_text(new)


def main():
    global T
    T = Theme()
    today = dt.datetime.now(dt.timezone.utc).date()
    s = stats(fetch()["user"], today)
    site = {"projects": site_projects(), "career": career()}
    out = {"header.svg": header(s, site, today)}
    files, html = links()
    out.update(files)
    if not audit(out.values()):
        sys.exit("SP Night audit failed; nothing written")
    (ROOT / "links").mkdir(exist_ok=True)
    for name, content in out.items():
        (ROOT / name).write_text(content)
    readme_links(html)
    print(f"ok: {s['total']} contributions, streak {s['current']}d, {len(site['projects'])} projects, "
          f"{len(site['career'])} jobs from the site")


if __name__ == "__main__":
    main()
