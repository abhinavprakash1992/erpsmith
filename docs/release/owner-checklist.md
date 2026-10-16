# Owner checklist for the 1.0.0 release

Everything in the repository is ready; these steps need your accounts or your decision. None has been done.

## Before publishing

- [ ] Confirm the repository address used throughout the docs, package metadata and User-Agent: `github.com/abhinavprakash1992/erpsmith` (change it everywhere if you pick another name or owner: README, PRIVACY.md, `pyproject.toml`, `erpsmith/knowledge.py`, listings, launch drafts).
- [ ] Confirm the MCP address users enter in Claude: `https://<site>/api/method/erpsmith.mcp.handle`.
- [ ] Skim the seed notes that were reworded from your vault (`private/seed-candidates.json`); decide on the 16 unverified candidates.
- [ ] Create the public GitHub repository and push `plan-4-release` (merge to `main` first).
- [ ] Watch the first CI run (`.github/workflows/ci.yml`) on both Frappe versions and fix what the real runner shows: Python 3.14 setup, Node 22/24, `bench init` and `get-app` from the checkout.
- [ ] Run the end-to-end Claude connector test on the release candidate over a tunnel (the Plan 1 Task 7 steps): add the connector, complete OAuth, call tools, on a v15 site and a v16 site.
- [ ] On a Frappe Cloud site: check that nginx serves `/.well-known/oauth-protected-resource` and `/.well-known/oauth-authorization-server` (spec risk 3).
- [ ] Tag the release: `git tag v1.0.0` (the tag is local until you push it).

## Assets to make

- [x] Logo (at least 200x200, no text): `docs/assets/logo-512.png` (512x512 PNG, transparent rounded corners) for upload forms.
- [ ] The 90-second demo video (`docs/launch/demo-script.md`).

## Frappe Cloud Marketplace

- [ ] Frappe Cloud publisher profile.
- [ ] "Add to Marketplace" for the app, using `docs/listing/frappe-marketplace.md` (short description is 58 characters).

## Claude Directory

- [ ] Build the reviewer demo site (private bench, `server_script_enabled`, synthetic data) and the reviewer test account described in `docs/listing/claude-directory.md`.
- [ ] Submit at `claude.ai/directory/manage` as a custom connection with the text from `docs/listing/claude-directory.md`; the privacy policy link is PRIVACY.md on GitHub.

## Launch

- [ ] Post `docs/launch/forum-post.md` on discuss.frappe.io.
- [ ] Open the awesome-frappe pull request (`docs/launch/awesome-frappe.md`).

## Decisions recorded (you can overturn)

- Plan arguments are stored in plaintext (audit trail); PRIVACY.md says so.
- DocType metadata is readable by any signed-in user through `get_doctype` and the debug tools, like Frappe's own desk.
- Deferred after release: the onboarding prompt as an MCP prompt, scrubber edge cases, merging the six background-job seed notes.
