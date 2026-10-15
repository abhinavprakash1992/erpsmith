#!/usr/bin/env python3
"""Write erpsmith/seed_memory.json from the notes the owner approved in private/seed-candidates.json.

    python3 dev/build_seed.py [--input private/seed-candidates.json] [--out erpsmith/seed_memory.json]
                              [--blocklist private/blocklist.txt]

An approved note that still breaks a hard content rule (a directive, a customer identifier, a foreign URL, a
term in the blocklist) stops the build and nothing is written: reword it in the candidates file first.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from erpsmith.tests import seed_rules  # noqa: E402  standard library only

TOPIC_ORDER = ("sandbox-rule", "pattern", "bug-fix", "context", "handoff")
SEED_KEY = re.compile(r"[a-z0-9][a-z0-9-]{0,60}")  # erpsmith.memory.SEED_KEY, used with fullmatch ("$" accepts a trailing newline)
MAX_TITLE_CHARS, MAX_CONTENT_CHARS = 140, 20_000  # erpsmith.memory's limits; this script runs without Frappe


def main():
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("--input", default=str(ROOT / "private" / "seed-candidates.json"))
	parser.add_argument("--out", default=str(ROOT / "erpsmith" / "seed_memory.json"))
	parser.add_argument("--blocklist", help="customer terms, one per line (default: private/blocklist.txt if it exists)")
	args = parser.parse_args()
	try:
		candidates = json.loads(Path(args.input).read_text(encoding="utf-8"))
	except (OSError, ValueError) as e:
		sys.exit(f"cannot read {args.input}: {e}")
	approved = [c for c in candidates if c.get("approved") is True]
	if not approved:
		sys.exit("no note is approved in " + args.input)
	if args.blocklist and not Path(args.blocklist).is_file():
		sys.exit(f"cannot read the blocklist {args.blocklist}: no such file")
	blocklist = seed_rules.private_blocklist(args.blocklist)  # the default path may be missing; an explicit one may not
	problems = []
	for c in approved:
		found = seed_rules.hard_violations(f"{c['title']}\n{c['content']}", blocklist)
		if found:
			problems.append(f"{c['seed_key']}: {', '.join(found)}")
		if not SEED_KEY.fullmatch(c["seed_key"]) or c["topic"] not in TOPIC_ORDER:
			problems.append(f"{c['seed_key']}: bad key or topic")
		if len(c["title"].strip()) > MAX_TITLE_CHARS or len(c["content"].strip()) > MAX_CONTENT_CHARS:
			problems.append(f"{c['seed_key']}: title over {MAX_TITLE_CHARS} or content over {MAX_CONTENT_CHARS} characters")
	if len({c["seed_key"] for c in approved}) != len(approved):
		problems.append("duplicate seed_key among the approved notes")
	if problems:
		sys.exit("not written; fix these approved notes first:\n  " + "\n  ".join(problems))
	approved.sort(key=lambda c: (TOPIC_ORDER.index(c["topic"]), c["seed_key"]))
	seeds = [{"seed_key": c["seed_key"], "topic": c["topic"], "title": c["title"].strip(), "content": c["content"].strip()}
		for c in approved]
	Path(args.out).write_text(json.dumps(seeds, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
	print(f"{len(seeds)} notes -> {args.out}")


if __name__ == "__main__":
	main()
