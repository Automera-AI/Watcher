# Watcher Control Page — scaffold

Next.js 15 + TypeScript + Tailwind control page, built against `DESIGN-SPEC.md`. This folder holds the
**design-token foundation** (`design-tokens.css`) ahead of the app scaffold; the `create-next-app` step
must run in a Node environment (it isn't run in this planning environment to avoid committing an
unverified lockfile / breaking CI).

## Stack (locked — DECISIONS.md)
- **Next.js 15** (App Router) + **TypeScript**
- **Tailwind CSS** — theme extended from `design-tokens.css`
- **Inter** (EN) + **Cairo** (AR) via `next/font` (self-hosted; no runtime CDN)
- **Lucide React** icons
- **Clerk** auth (D2-a)
- Typed REST client generated from the FastAPI OpenAPI schema

## Scaffold steps (run in a Node env)
```bash
cd apps/control-page
npx create-next-app@latest . --ts --tailwind --app --eslint --use-npm
# then: import design-tokens.css into app/globals.css and map tokens into tailwind.config.ts
npm install lucide-react @clerk/nextjs
```

## Views (build order — DESIGN-SPEC §7; Inbox is the critical path)
1. **Inbox** — triage queue, one-click confirm/edit/route, confidence chip
2. **Sources** — opt-out exclusion list + first-run wizard
3. **Destinations** — recipe picker + webhook URL + field mapping
4. **Rules** — condition → action builder (no DSL)
5. **Admin / Eval** — eval report viewer, accuracy drift per client

## CI note
The `web` job in `.github/workflows/ci.yml` activates automatically once `package.json` exists here
(`npm ci` + lint/typecheck/build); until then it self-skips. Commit `package-lock.json` with the scaffold.
