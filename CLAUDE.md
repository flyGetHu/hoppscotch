# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Development (starts all dev servers concurrently)
pnpm dev

# Build all packages for production
pnpm generate

# Lint, typecheck, and test across all packages
pnpm lint          # eslint with --fix
pnpm typecheck     # TypeScript noEmit
pnpm test          # runs vitest/jest per package
pnpm pre-commit    # lint + typecheck (used by husky hook)

# Backend-specific (run from packages/hoppscotch-backend)
pnpm start:dev     # NestJS watch mode
pnpm test:e2e      # end-to-end tests
pnpm generate-gql-sdl  # export GraphQL schema to gql-gen/backend-schema.gql

# GraphQL code generation (regenerates typed queries from backend schema)
pnpm gen-gql

# Single test in backend
pnpm --filter hoppscotch-backend exec jest -- --testPathPattern="auth"

# Single test in common
pnpm --filter @hoppscotch/common exec vitest -- --run path/to/test.ts
```

## Architecture

**Monorepo** managed by pnpm workspaces (`packages/**`). No Turborepo/Nx — uses `pnpm -r` for orchestration. Each package defines `do-dev`, `do-build-prod`, `do-lint`, `do-typecheck`, `do-test` scripts that root commands delegate to.

### Packages

| Package | Role |
|---------|------|
| `hoppscotch-backend` | NestJS 11 GraphQL API (port 3170). PostgreSQL via Prisma 7. Cookie-based JWT auth. |
| `hoppscotch-common` | Shared Vue 3 frontend library (`@hoppscotch/common`). Components, composables, platform logic. |
| `hoppscotch-selfhost-web` | Self-hosted web app (port 3000). Vue 3 SPA. |
| `hoppscotch-sh-admin` | Admin dashboard (port 3100). Vue 3 SPA. |
| `hoppscotch-data` | Shared types, validations (io-ts, zod). No runtime deps. |
| `hoppscotch-kernel` | Cross-platform runtime abstraction (Tauri API layer). |
| `hoppscotch-js-sandbox` | JS sandbox for pre-request/test scripts (isolated-vm). |
| `hoppscotch-cli` | CLI tool (`hopp`) for running tests in CI. Node >= 22. |
| `hoppscotch-desktop` | Tauri v2 desktop app wrapping selfhost-web. |

### Backend (hoppscotch-backend)

- **GraphQL**: Code-first with `@nestjs/graphql`, schema auto-generated from decorators
- **REST**: Versioned REST controllers under `/v1/` (auth, health, infra-token endpoints)
- **Database**: PostgreSQL 15 + Prisma 7 with `@prisma/adapter-pg` driver adapter
- **Auth**: Passport.js strategies (Google, GitHub, Microsoft, Feishu, Email, JWT). Access + refresh tokens in httpOnly cookies.
- **Config**: `InfraConfig` system — key-value store in DB with optional encryption. Loaded at startup via `loadInfraConfiguration()`. Sensitive values (CLIENT_SECRET, JWT_SECRET) are encrypted with `DATA_ENCRYPTION_KEY`.
- **Module pattern**: Each feature is a NestJS module (`auth/`, `user/`, `team/`, `infra-config/`, etc.) with service, resolver, and optional controller/guard/strategy.

### Frontend (hoppscotch-common + selfhost-web + sh-admin)

- **Vue 3** Composition API with `<script setup>`
- **GraphQL client**: urql with code generation (`graphql-codegen`)
- **State**: RxJS + Dioc (DI) + Pinia
- **UI**: Tailwind CSS + `@hoppscotch/ui` component library
- **Routing**: File-based via `vite-plugin-pages` + `vite-plugin-vue-layouts`
- **Platform layer**: `packages/hoppscotch-selfhost-web/src/platform/` provides platform-specific implementations (web vs desktop) for auth, HTTP, IO operations

## Key Patterns

### GraphQL Code Generation Flow
1. Backend emits SDL to `gql-gen/backend-schema.gql` (via `generate-gql-sdl` or `GENERATE_GQL_SCHEMA=true`)
2. Frontend packages run `graphql-codegen` in `postinstall` to generate typed TypeScript from that SDL
3. Root `pnpm gen-gql` orchestrates the full pipeline

### Adding a New OAuth Provider
Requires changes across these files (all required, no exceptions):
1. `backend/src/types/InfraConfig.ts` — enum values for config keys
2. `backend/src/auth/helper.ts` — AuthProvider enum
3. `backend/src/infra-config/helper.ts` — provider keys + default configs + derived env
4. `backend/src/auth/strategies/` — new Strategy file
5. `backend/src/auth/guards/` — new Guard file
6. `backend/src/auth/auth.controller.ts` — two routes (initiate + callback)
7. `backend/src/auth/auth.module.ts` — conditional registration
8. `backend/src/infra-config/infra-config.service.ts` — isServiceConfigured + validateEnvValues
9. `backend/src/infra-config/dto/onboarding.dto.ts` — Request + Response DTOs
10. Frontend: `common/src/components/firebase/Login.vue`, platform auth files, locales
11. Admin: `sh-admin/src/helpers/configs.ts`, `OAuthProviderConfigurations.vue`

### InfraConfig System
Runtime configuration stored in `InfraConfig` DB table. Each entry has `name`, `value`, `isEncrypted`. Encrypted values use AES with `DATA_ENCRYPTION_KEY`. The `buildDerivedEnv()` function auto-heals callback URLs based on `VITE_BACKEND_API_URL`.

### Cookie Auth
Auth tokens are set as httpOnly cookies by the backend. The `authCookieHandler()` function sets `access_token` and `refresh_token` cookies, then redirects. The callback URL hostname must match `VITE_BACKEND_API_URL` hostname exactly (e.g., `localhost` vs `127.0.0.1` matters).

## Style

- **Formatting**: Prettier — no semicolons, double quotes, trailing commas (ES5), 80 char width, 2-space indent
- **Commits**: Conventional Commits (enforced by commitlint + husky)
- **Backend tsconfig**: lenient (`strict: false`)
- **Frontend tsconfig**: strict (`strict: true`)
- **Functional patterns**: `fp-ts` for Either/Option in backend; `io-ts`/`zod` for validation in shared data
