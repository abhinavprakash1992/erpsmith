#!/usr/bin/env python3
"""Screen the exported notes and propose the ones that could ship as seed memory. Nothing here approves anything.

    python3 dev/seed_candidates.py --input private/node-memory.json --out private [--blocklist private/blocklist.txt]

Writes seed-candidates.json (one object per note, approved=false) and seed-candidates.md (the list the owner
reads) into --out. Both hold customer data and belong in the git-ignored private/ folder.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from erpsmith.tests import seed_rules  # noqa: E402  standard library only

BUCKETS = ("include", "review", "exclude")


def slug(title):
	return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50].strip("-") or "note"


def candidate(note, key, blocklist):
	text = f"{note['title']}\n{note['content']}"
	hard = seed_rules.hard_violations(text, blocklist)
	flags = hard + seed_rules.soft_flags(text, note["topic"], len(note["content"]))
	if note["topic"] in seed_rules.CONTEXT_TOPICS or seed_rules.IDENTIFIER_RULES & set(hard):
		suggested = "exclude"
	elif flags:
		suggested = "review"
	else:
		suggested = "include"
	return {"seed_key": key, "source_id": note["id"], "topic": note["topic"], "title": note["title"],
		"content": note["content"], "flags": flags, "suggested": suggested, "approved": False}


def report(candidates, json_name, blocklist_note):
	counts = {b: sum(c["suggested"] == b for c in candidates) for b in BUCKETS}
	lines = [
		"# Seed memory candidates",
		"",
		f"{len(candidates)} notes screened: {counts['include']} include, {counts['review']} review, {counts['exclude']} exclude.",
		f"Blocklist: {blocklist_note}",
		"",
		"Nothing is approved yet. A seed note must state facts about Frappe or recipes that work; it must not tell the",
		"assistant what to do (always, never, must, stop after) and must not carry anything specific to one company.",
		f"To approve a note, set `\"approved\": true` in `{json_name}` (edit its title or content there to reword a",
		"directive as a fact), or name the `seed_key` values to approve and have them applied for you.",
	]
	for bucket in BUCKETS:
		lines += ["", f"## {bucket}"]
		for c in (c for c in candidates if c["suggested"] == bucket):
			flags = ", ".join(c["flags"]) or "none"
			excerpt = c["content"].strip().replace("\n", "\n> ")
			lines += ["", f"### {c['seed_key']}", f"{c['topic']} · flags: {flags}", f"**{c['title']}**", "", f"> {excerpt[:600]}"]
	return "\n".join(lines) + "\n"


def main():
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("--input", required=True)
	parser.add_argument("--out", required=True)
	parser.add_argument("--blocklist", help="customer terms, one per line (default: private/blocklist.txt if it exists)")
	args = parser.parse_args()
	try:
		notes = json.loads(Path(args.input).read_text(encoding="utf-8"))
	except (OSError, ValueError) as e:
		sys.exit(f"cannot read {args.input}: {e}")
	if not isinstance(notes, list):
		sys.exit(f"{args.input}: expected a JSON list of notes")
	if args.blocklist and not Path(args.blocklist).is_file():
		sys.exit(f"cannot read the blocklist {args.blocklist}: no such file")
	blocklist_path = Path(args.blocklist) if args.blocklist else ROOT / "private" / "blocklist.txt"
	blocklist = seed_rules.private_blocklist(blocklist_path)  # the default path may be missing; an explicit one may not
	terms = f"{len(blocklist)} term{'s' * (len(blocklist) != 1)} from {blocklist_path}" if blocklist else "none"
	used, candidates = set(), []
	for note in notes:
		base = key = slug(note["title"])
		n = 1
		while key in used:
			n += 1
			key = f"{base}-{n}"
		used.add(key)
		candidates.append(candidate(note, key, blocklist))
	candidates.sort(key=lambda c: (BUCKETS.index(c["suggested"]), c["topic"], c["title"]))
	out = Path(args.out)
	out.mkdir(parents=True, exist_ok=True)
	(out / "seed-candidates.json").write_text(json.dumps(candidates, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
	(out / "seed-candidates.md").write_text(report(candidates, "seed-candidates.json", terms), encoding="utf-8")
	print(f"{len(candidates)} candidates -> {out}; blocklist: {terms}")


if __name__ == "__main__":
	main()
