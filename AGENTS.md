# bluefin-bling — Agent & Copilot Instructions

> **You are part of an agentic operating system, built by agentic workflows.**
>
> **Humans approve design, security, and merge. Everything else is automated, self-healing, and non-blocking.**

**bluefin-bling** is the monorepo for desktop enhancements, GNOME Shell extensions, and UI integrations for [Project Bluefin](https://projectbluefin.io).

Home repo: [projectbluefin/bluefin-bling](https://github.com/projectbluefin/bluefin-bling)  
Parent factory: [projectbluefin/common](https://github.com/projectbluefin/common)

---

## Agent Fast Path

```
1. docs/SKILL.md                    # skill router — find the right skill before acting
2. docs/skills/<skill>.md           # load authoritative skill docs
3. Use Context7 for any library     # resolve-library-id → query-docs, every time
4. Validate changes before commit   # node --check, glib-compile-schemas, json validation
```

**Doc-only changes** (`docs/`, `AGENTS.md`, `README.md`) → push directly to `main`.
```bash
git diff --cached --name-only   # confirm only docs/*, AGENTS.md, or README.md
```
**Everything else** → feature branch + PR targeting `main`.

---

## Repo Layout

```
bluefin-bling/
├── extensions/
│   ├── power-status-color/    # Quick Settings power button status styling
│   │   ├── metadata.json
│   │   ├── extension.js
│   │   └── stylesheet.css
│   └── syncthing-toggle/      # Sync Folder peer sharing quick settings toggle
│       ├── metadata.json
│       ├── extension.js
│       ├── toggle.js
│       ├── prefs.js
│       ├── icons/
│       └── schemas/
├── docs/
│   ├── SKILL.md               # Task → skill router
│   └── skills/                # Authoritative operational knowledge
├── README.md
└── AGENTS.md
```

**Rule:** Every extension lives under its own folder inside `extensions/` with its own `metadata.json`. Monorepo root is reserved for docs, CI, and tooling.

---

## Context7 — Mandatory for All Platform & Library Work

Use `context7-resolve-library-id` + `context7-query-docs` **before writing any code** that touches:
- GNOME Shell extension APIs (`/git_gitlab_gnome_org/gnome_gnome-shell`)
- GJS / GNOME JavaScript APIs (`/websites/gjs-docs_gnome`)
- GNOME HIG and developer documentation (`/websites/developer_gnome`)
- bootc interfaces and schemas (`/bootc-dev/bootc`)
- Updating any skill file that covers a platform API

Training data for GNOME Shell and bootc APIs is frequently outdated. Context7 provides current authoritative docs. This rule is unconditional.

Frontmatter convention:
```yaml
metadata:
  context7-sources:
    - /git_gitlab_gnome_org/gnome_gnome-shell
    - /websites/developer_gnome
    - /bootc-dev/bootc
```

---

## Self-Improvement Loop (The Two-Output Rule)

Every agent session produces two outputs:

1. **The work** — the PR, fix, or feature
2. **The learning** — what the next agent needs to know

```
work on task
  └─ discover pattern / workaround / convention
       └─ write it to the relevant docs/skills/ file
            └─ commit in the same PR (never a follow-up)
                 └─ next agent starts smarter → loop
```

### What is Banned

- **No changelog files.** Delete `IMPROVEMENTS.md`, `CHANGELOG.md`, `SESSION.md` if found.
- **No session notes committed to the repo.** (`NOTES.md`, `PLAN.md`, `TODO.md` live only in session folders).
- **No "append here" docs.** Route learnings directly into `docs/skills/`.

### Before Marking Work Complete

- [ ] Did I discover any workaround, non-obvious pattern, or convention?
- [ ] Is there a skill file for the area I worked in?
- [ ] If yes — did I update it?
- [ ] If no — did I create one in `docs/skills/`?
- [ ] Skill file committed in **this same PR** (not a follow-up)

See [`docs/skills/skill-improvement.md`](docs/skills/skill-improvement.md).

---

## Human Decision Gates

Stop and ask at these four gates. Never guess past them.

| Gate | Stop when |
|---|---|
| **Design** | New extension architecture, major UI overhaul, changing alert behavior |
| **Security** | System service permissions, polkit actions, elevated execution |
| **Breakage** | Incompatible schema migrations or dropping GNOME version compatibility |
| **Merge** | PR ready for final review — always requires human `lgtm` |

---

## Build and Validation

```bash
# Validate JS syntax across extensions
node --check extensions/power-status-color/extension.js
node --check extensions/syncthing-toggle/extension.js
node --check extensions/syncthing-toggle/toggle.js
node --check extensions/syncthing-toggle/prefs.js

# Validate JSON metadata
python3 -m json.tool extensions/power-status-color/metadata.json > /dev/null
python3 -m json.tool extensions/syncthing-toggle/metadata.json > /dev/null

# Compile and validate GSettings schemas
glib-compile-schemas --strict extensions/syncthing-toggle/schemas/
```

---

## PR and Commit Conventions

### Commit Format — Conventional Commits

```
feat(power-status): add bootc staged update detection
fix(syncthing): handle missing user service gracefully
docs(skills): document GNOME 45+ cancellable subprocess cleanup
```

Types: `feat` `fix` `docs` `ci` `refactor` `chore` `build` `perf` `test`

### AI Attribution

Every AI-authored commit must include the Co-authored-by trailer:

```
Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

### Git Hook Note
Pushes to `projectbluefin/*` may trigger ghost hooks. When pushing via CLI:
```bash
git push --no-verify
```
