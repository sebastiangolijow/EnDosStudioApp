# Session Start Briefing — StickerApp Backend

> **For the AI agent picking this project up.** Read this file first. It captures the work done in the previous session (project setup) and tells you exactly where to start.
>
> If you have time/budget for only one file: read this one, then `CLAUDE.md`, then start working.

---

## 1. What this project is

A Django backend for a Barcelona-based print shop ("gráfica") that sells custom stickers online. Customers upload images, a frontend editor (Vue + OpenCV.js, **separate codebase, not your concern**) generates die-cut and relief masks, then customers place orders and pay via Stripe.

This backend is responsible for **5 things only**:

1. Auth + user accounts (registration, login, profile)
2. Order management (create, attach files, track status)
3. File ingestion (store images + masks the frontend uploads)
4. Stripe integration (payment intents, webhooks, status transitions)
5. Admin/management surface (Django admin + DRF endpoints for a future Vue admin UI)

Built by **YeKo Studio**. Mindset: simple > complex, build first sell after, frontend keeps frontend work.

---

## 2. State of the repo right now

The repo currently contains **only** these files:

```
endossutdio_backend/
├── CLAUDE.md           ← read this NEXT, after this briefing
├── README.md           ← 5-line public description
└── SESSION_START.md    ← you are here
```

There is **no Django project yet**. No `manage.py`, no `apps/`, no `config/`. The previous session intentionally stopped before scaffolding so the actual creation could happen with full attention.

**Your first action** is to invoke the `bootstrap-stickerapp-backend` skill. That skill will lay down the entire skeleton (Django project, 4 apps, Docker, Makefile, tests, etc.) — see §4 below.

---

## 3. What's already been decided (don't re-litigate)

These are locked answers from the previous session. If you find yourself wanting to revisit any of them, **flag it to the user explicitly** rather than silently changing course — they were debated and chosen for a reason.

| Decision | Value | Why |
|---|---|---|
| Backend language | Python 3.11 | YeKo Studio default |
| Framework | Django 4.2 + DRF | Matches LabControl reference, batteries-included |
| Database | PostgreSQL 15 | Studio default |
| Payment gateway | **Stripe** (not abstracted, not gateway-agnostic) | Barcelona-based client, EU-friendly |
| Multi-tenancy | **NONE** | Single print shop, single tenant. Do NOT add `lab_client_id`-style scaffolding |
| Async / queue | **No Celery, no Redis on day 1** | YAGNI. Image processing happens in the browser. Add Celery only when there's a concrete async need |
| Auth stack | JWT (simplejwt) + dj-rest-auth + django-allauth | Same as LabControl |
| User model | Custom, UUID PK, email as `USERNAME_FIELD`, role: `admin` / `shop_staff` / `customer` | NOT Django's default |
| Primary keys | UUID across all models. Always `.pk`, never `.id` | `.id` raises `AttributeError` on UUID-PK models |
| Business logic | Lives in `apps/<app>/services.py`, NOT in views or serializers | Service-layer pattern — keeps views thin |
| Image processing in backend | NONE on day 1. Backend stores files only | Frontend does OpenCV.js. Backend OpenCV-Python is a "FUTURE" item per spec, only if browser proves insufficient |
| Frontend | Out of scope for this repo | Vue app is a separate codebase |
| Apps to create | `core`, `users`, `orders`, `payments` (4 apps) | No `notifications`, no `analytics` apps yet |
| Infra | Docker + docker-compose. Two compose files: local + production. **No staging environment** | Per project spec |
| Language defaults | Spanish (`LANGUAGE_CODE = "es"`, `TIME_ZONE = "Europe/Madrid"`) | Barcelona client |

---

## 4. Your immediate next action

**Invoke the bootstrap skill.** It's already created and tested-by-design.

```
/bootstrap-stickerapp-backend
```

(Or just say "bootstrap the stickerapp backend" / "scaffold the project" — the skill description is permissive enough to trigger on natural language.)

The skill is at `~/.claude/skills/bootstrap-stickerapp-backend/`. Its structure:

```
bootstrap-stickerapp-backend/
├── SKILL.md                              ← 207 lines: orchestrator, phases, confirmation gate
└── references/
    ├── structure.md                       ← directory tree
    ├── django-config.md                   ← manage.py, settings split, urls.py
    ├── users-app.md                       ← custom User, auth flow, allauth EmailAddress trap
    ├── core-app.md                        ← BaseModel mixins, permissions
    ├── orders-payments-skeletons.md       ← empty-but-correct app skeletons
    ├── docker-and-tooling.md              ← Docker, Make, lint, requirements
    └── tests-and-templates.md             ← test base + email templates
```

### What the skill does (so you know what to expect)

- **Phase 1 — read context, build plan, gate.** Reads `CLAUDE.md`, sanity-checks the target dir is empty, then prints the full plan (every directory, every file, every dependency, every env var) and waits for an explicit `yes` before writing anything. If you say anything other than `yes`, it aborts cleanly.
- **Phase 2 — write files.** Reads each reference file in order and writes the corresponding files. Uses the `Write` tool, not heredoc-via-bash, so the diff is reviewable.
- **Phase 3 — verify.** Inside Docker (there's no host venv): `python manage.py check`, `makemigrations`, `migrate`, `pytest --collect-only`. Each must succeed.
- **Phase 4 — final report.** Prints what was created and the immediate next step (which is "design the Order model").

### Confirmation gate is non-negotiable

Don't skip the `yes` prompt to "save time". Re-running the skill on a populated repo would clobber real work. The skill checks for `manage.py` existing and aborts if so, but the gate is the user's last chance to catch anything wrong with the plan.

### After the skill completes

You should have:
- A Django project that boots (`manage.py check` passes)
- Initial migrations applied (User table exists, with UUID PK)
- An empty test suite that collects clean
- A working `make test` / `make up` / `make down` workflow

---

## 5. What comes AFTER the bootstrap (do not do these in the bootstrap)

Once the skeleton exists, the project's first real feature work is **designing the Order domain models**. The bootstrap deliberately ships `apps/orders/models.py` empty.

The order of operations for the next phase (**after** bootstrap, with user input):

1. **Move the spec into the repo.** The full project spec lives at `/Users/cevichesmac/Downloads/Guía_StickerApp_Version2 (1).md`. After bootstrap, move it to `docs/spec.md` so future sessions don't depend on the Downloads folder.

2. **Design `Order` + `OrderFile` models.** Read the spec carefully. The Order has a status lifecycle (`draft → placed → paid → in_production → shipped → delivered → cancelled`); `OrderFile` represents each uploaded file (original image, die-cut mask, relief mask) and is linked to the Order via FK. Design with the user, don't guess.

3. **First feature: customer registration → password setup → login round-trip.** Write an integration test that exercises the full flow:
   - `POST /api/v1/auth/register/` (creates inactive customer)
   - Get the verification token from the user object
   - `POST /api/v1/users/set-password/` with `{email, token, password}` (activates + creates allauth EmailAddress)
   - `POST /api/v1/auth/login/` (issues JWT)
   - `GET /api/v1/users/me/` with the token (returns 200 + user profile)
   - This test is the load-bearing one. If it passes, the auth foundation is solid.

4. **Wire Stripe PaymentIntent creation + webhook handling.** The bootstrap ships `apps/payments/services/stripe_service.py` with `create_payment_intent()` and `construct_webhook_event()` methods. The next step is the order checkout flow: `POST /api/v1/orders/{id}/checkout/` returns a `client_secret`; Stripe webhook arrives at `/api/v1/payments/webhooks/stripe/` and transitions the order to `paid`.

5. **Then admin views.** Django admin first (free), DRF endpoints for a custom Vue admin UI later.

Don't sprint ahead. Each step builds on the previous.

---

## 6. Pitfalls that have already been paid for (don't re-discover them)

These are gotchas from the LabControl project (the reference codebase). Future agents on this project benefit from inheriting the lessons.

### The django-allauth `EmailAddress` row trap

django-allauth authenticates against `allauth.account.models.EmailAddress`, **not** against `User.email`. A user with a correct password and `User.email` set but no matching `EmailAddress` row **silently fails to log in** with a generic "no user found" error. There's no warning, no traceback — just a broken login.

The bootstrap's `User.verify_email()` method creates the `EmailAddress` row alongside flipping `is_verified=True`, and the `SetPasswordView` calls it. **Any future flow that creates or activates a user must follow this pattern**, not just set `User.email` and `User.password`.

The `tests/base.py` `create_user()` factory also creates the `EmailAddress` row by default — so tests don't accidentally write code that "passes tests" but breaks in production.

### `.pk` not `.id`

Custom User uses UUID PK named `uuid`, not `id`. Code that does `user.id` raises `AttributeError`. Same for every domain model (they inherit `UUIDModel`). Use `.pk` everywhere; in tests, `str(obj.pk)` for assertions. `Count("pk", filter=Q(...))` in aggregations.

### `SIMPLE_JWT["USER_ID_FIELD"] = "uuid"`

If left at default `"id"`, JWT issuance silently fails because there's no `id` attribute on `User`. The bootstrap sets this correctly in `config/settings/base.py`. Don't change it.

### Don't rsync `.env.production`

Not relevant until first deploy, but worth noting now: when a deploy script eventually exists, it must NOT include `.env.production` in any rsync source list. Overwriting the server's env with a local template version was the single biggest deploy footgun on LabControl.

### `restart` vs `up -d --force-recreate`

For env var changes, use `stop` + `rm -f` + `up -d`. `restart` does not reload env vars. Same for compose-level config changes (healthchecks, volumes) — use `up -d --force-recreate`, not `restart`. (This is documented in CLAUDE.md too — once a deploy script exists, it should enforce this.)

---

## 7. Reference codebase: LabControl

`/Users/cevichesmac/Desktop/labcontrol/` — YeKo's other Django backend. **Read for patterns. Do NOT import from. Do NOT depend on.**

When unsure how to structure something, check how LabControl did it:

- `apps/core/models.py` — BaseModel mixins (UUID, timestamps, created_by). Same pattern here.
- `apps/users/models.py` — custom User with email + role. Same shape, different role values, no medical-domain fields.
- `apps/users/views.py` `SetPasswordView` — the pattern that handles the allauth trap correctly.
- `tests/base.py` — `BaseTestCase` with factory methods. Same pattern (adapted).
- `Makefile` — make targets for Docker workflows.
- `config/settings/{base,dev,prod,test}.py` — settings split.

Things in LabControl that DON'T apply here (don't copy them):

- `lab_client_id` and multi-tenancy — single-tenant here.
- LabWin Firebird sync, FTP PDF fetch, `apps/labwin_sync/` — entirely different domain.
- `apps/notifications/` and `apps/analytics/` apps — explicitly deferred for StickerApp.
- Healthcheck/deploy scripts — different infra; we'll write our own when first deploy happens.

LabControl is at 475 tests passing as of late April 2026. Stable. Worth reading when stuck.

---

## 8. The YeKo Studio mindset (load this into your operating frame)

The studio's principles, distilled. Apply them in every architectural decision:

- **Build operational systems for SMBs that already make money.** The bar is "did we reduce real operational chaos and increase real revenue?"
- **Simple > complex.** If you're reaching for microservices, message queues, or abstract patterns, stop. The smallest thing that fixes the bottleneck is usually right.
- **Build first, sell after.** Real backend, not a demo. Every endpoint should map to something a customer or shop owner actually does.
- **Execute, don't theorize.** Ship the obvious answer fast and iterate.
- **Frontend keeps frontend work.** The Vue app does OpenCV. Don't move work backend just because it's "easier on the server".
- **No overengineering.** Don't add Celery for "future scalability". Don't add multi-tenancy "in case we onboard more shops". Don't pre-design abstract base classes you might need.

The 1-line filter: *"If a solution doesn't improve the business operation, it isn't worth building."*

Full studio context: `/Users/cevichesmac/Desktop/yeko_studio/yeko_studio_context.md`.

---

## 9. Recommended reading order for the next session

If the next session has full token budget:

1. This file (`SESSION_START.md`)
2. `CLAUDE.md` in the same folder
3. The bootstrap skill's `SKILL.md` at `~/.claude/skills/bootstrap-stickerapp-backend/SKILL.md`

That's enough to start. Once the skill runs, it'll pull the relevant references on-demand.

If budget is tight:

1. This file only
2. Then invoke the skill — its own SKILL.md will tell you what to read next

---

## 10. What "success" looks like for the next session

You'll know the next session went well if, by the end of it:

- [ ] The bootstrap skill ran end-to-end without errors
- [ ] `manage.py check` passes
- [ ] Initial migrations applied cleanly
- [ ] `make test` runs the smoke tests and reports green
- [ ] `docs/spec.md` exists (moved from Downloads)
- [ ] CLAUDE.md updated to mark "bootstrap done" in its status section
- [ ] You've sketched the Order + OrderFile model designs with the user (not implemented yet — just designs)
- [ ] First migration commit on the project's git repo (if you initialize git in this session)

---

## 11. If something goes wrong

- **The bootstrap skill aborts during the gate** — that's by design. Read what it printed, decide whether to proceed, type `yes` if so.
- **Verification (Phase 3) fails** — stop. Don't fix things mid-flight. Show the user what broke and ask. Common causes: Docker not running, port 5432 already in use, `.env` file missing.
- **You find a contradiction between this file and CLAUDE.md** — CLAUDE.md wins. CLAUDE.md is the project's living doc; this file is a one-time briefing for session-2 startup.
- **The user wants to deviate from a locked decision in §3** — don't silently comply. Surface the trade-off ("changing X means we lose Y; are you sure?") and let them confirm.

---

*Created at end of project-setup session. After the next session bootstraps the Django project, this file becomes historical — feel free to delete it or move it to `docs/archive/SESSION_START.md` once CLAUDE.md is updated to reflect post-bootstrap state.*
