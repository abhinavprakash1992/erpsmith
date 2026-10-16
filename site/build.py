"""Build the ERPSmith website into site/dist: home from site/home.html, docs/privacy/terms from README.md, PRIVACY.md, TERMS.md.

python3 site/build.py && vercel deploy site/dist --prod
Markdown is rendered by marked through npx, so Node is needed. Only site/dist is uploaded.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE, DIST = ROOT / "site", ROOT / "site" / "dist"
REPO = "https://github.com/abhinavprakash1992/erpsmith"
LINKS = {"(PRIVACY.md)": "(/privacy)", "(TERMS.md)": "(/terms)"}

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:image" content="/logo-512.png">
<link rel="icon" href="/logo-512.png">
<style>
:root {{ --bg: #0b1020; --panel: #121a33; --line: #22304f; --text: #e6e9f2; --muted: #9aa3b8; --ember: #f28c28; --teal: #3fd0c9; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--text); font: 16px/1.65 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
a {{ color: var(--ember); }}
header, main, footer {{ max-width: 960px; margin: 0 auto; padding: 0 16px; }}
header {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; padding-top: 20px; padding-bottom: 20px; flex-wrap: wrap; }}
header .brand {{ display: flex; align-items: center; gap: 10px; color: var(--text); text-decoration: none; font-weight: 700; font-size: 18px; }}
header nav {{ display: flex; gap: 20px; flex-wrap: wrap; }}
header nav a {{ color: var(--muted); text-decoration: none; }}
header nav a:hover, header nav a[aria-current] {{ color: var(--text); }}
h1, h2, h3 {{ line-height: 1.25; }}
h2 {{ margin-top: 2.2em; }}
code {{ font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; background: var(--panel); border-radius: 4px; padding: 1px 5px; }}
pre {{ background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; overflow-x: auto; }}
pre code {{ background: none; padding: 0; }}
table {{ border-collapse: collapse; display: block; overflow-x: auto; }}
th, td {{ border: 1px solid var(--line); padding: 6px 10px; text-align: left; }}
.hero {{ text-align: center; padding: 40px 0 8px; }}
.hero img {{ border-radius: 36px; filter: drop-shadow(0 12px 40px rgba(242, 140, 40, .25)); max-width: 50vw; height: auto; }}
.hero h1 {{ font-size: clamp(36px, 7vw, 56px); margin: 16px 0 0; }}
.tagline {{ color: var(--ember); font-size: 22px; font-weight: 600; margin: 4px 0 12px; }}
.lede {{ color: var(--muted); max-width: 640px; margin: 0 auto; font-size: 18px; }}
.actions {{ display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; margin-top: 24px; }}
.button {{ border: 1px solid var(--line); border-radius: 999px; padding: 10px 20px; color: var(--text); text-decoration: none; font-weight: 600; }}
.button.primary {{ background: var(--ember); border-color: var(--ember); color: #1a1205; }}
.steps {{ list-style: none; padding: 0; display: grid; gap: 16px; counter-reset: step; }}
.steps > li {{ background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 4px 20px 8px; counter-increment: step; min-width: 0; }}
.steps h3::before {{ content: counter(step) ". "; color: var(--ember); }}
.prompts {{ color: var(--muted); padding-left: 18px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; }}
.card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 4px 20px 12px; }}
.card h3 {{ color: var(--teal); }}
.card p {{ color: var(--muted); margin-top: 0; }}
.prose h1 {{ font-size: 36px; }}
.prose h2 {{ border-bottom: 1px solid var(--line); padding-bottom: 6px; }}
.toc {{ background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 12px 20px; }}
.toc a {{ color: var(--text); }}
footer {{ border-top: 1px solid var(--line); margin-top: 64px; padding-top: 24px; padding-bottom: 40px; color: var(--muted); font-size: 14px; }}
footer a {{ color: var(--muted); }}
</style>
</head>
<body>
<header>
  <a class="brand" href="/"><img src="/logo-512.png" alt="" width="32" height="32">ERPSmith</a>
  <nav>{nav}</nav>
</header>
<main class="{kind}">
{body}
</main>
<footer>
  <p>ERPSmith is free and open source under the MIT licence, by Abhinav Prakash. <a href="/privacy">Privacy</a> · <a href="/terms">Terms</a> · <a href="{repo}">GitHub</a> · <a href="{repo}/issues">Issues</a></p>
  <p>Not affiliated with or endorsed by Frappe Technologies. ERPNext and Frappe are trademarks of Frappe Technologies Pvt. Ltd.</p>
</footer>
</body>
</html>
"""


def markdown(text):
	for old, new in LINKS.items():
		text = text.replace(old, new)
	return subprocess.run(["npx", "-y", "marked@12.0.2", "--gfm"], input=text, capture_output=True, text=True, check=True).stdout


def slug(text):
	return re.sub(r"[^a-z0-9]+", "-", re.sub(r"<[^>]+>", "", text).lower()).strip("-")


def with_toc(body):
	"""Give each h2 an id and put a contents list after the first paragraph."""
	heads = re.findall(r"<h2>(.*?)</h2>", body)
	body = re.sub(r"<h2>(.*?)</h2>", lambda m: f'<h2 id="{slug(m[1])}">{m[1]}</h2>', body)
	toc = '<nav class="toc"><strong>Contents</strong><ul>' + "".join(f'<li><a href="#{slug(h)}">{h}</a></li>' for h in heads) + "</ul></nav>"
	return body.replace("</p>", "</p>\n" + toc, 1)


def page(name, title, description, body, kind="prose"):
	nav = "".join(
		f'<a href="{href}"{" aria-current=page" if href == "/" + name else ""}>{label}</a>'
		for href, label in (("/docs", "Docs"), ("/privacy", "Privacy"), ("/terms", "Terms"), (REPO, "GitHub"))
	)
	html = PAGE.format(title=title, description=description, nav=nav, kind=kind, body=body, repo=REPO)
	(DIST / f"{name}.html").write_text(html)


def main():
	shutil.rmtree(DIST, ignore_errors=True)
	DIST.mkdir()
	shutil.copy(ROOT / "docs" / "assets" / "logo-512.png", DIST / "logo-512.png")
	(DIST / "vercel.json").write_text(json.dumps({"cleanUrls": True}) + "\n")

	readme = (ROOT / "README.md").read_text().split("\n", 1)[1].replace("# ERPSmith\n", "# ERPSmith documentation\n", 1)
	page("index", "ERPSmith: the AI dev copilot for ERPNext",
		"Build, debug and fix ERPNext DocTypes, scripts and workflows from Claude, behind a plan gate you approve. Free and open source.",
		(SITE / "home.html").read_text(), kind="home")
	page("docs", "ERPSmith documentation", "Install ERPSmith, connect Claude, and use the plan gate, memory and site knowledge.", with_toc(markdown(readme)))
	page("privacy", "ERPSmith privacy policy", "What ERPSmith stores in your site and what leaves it.", markdown((ROOT / "PRIVACY.md").read_text()))
	page("terms", "ERPSmith terms of use", "The terms for the ERPSmith app and website.", markdown((ROOT / "TERMS.md").read_text()))
	print(f"built {', '.join(sorted(p.name for p in DIST.iterdir()))} in {DIST}")


if __name__ == "__main__":
	main()
