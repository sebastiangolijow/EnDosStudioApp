# StickerApp Backend — AI Context

> **Studio**: YeKo Studio · **Client**: a print shop in Barcelona that sells custom stickers
> **Stack**: Python 3.11 · Django 4.2 · DRF · PostgreSQL 15 · Docker · Stripe · (Celery only if real async need)
> **Status**: M1 (bootstrap) + M2 (orders/payments backend, customer/staff API, Stripe checkout flow, auth gate) + M3 in progress (frontend SPA shipped, cut-path SVG generation, shape field, repricing 2026-05-09 — area×qty×material formula with additive % add-ons + 20€ floor). Live Stripe keys + email SMTP + first deploy are the remaining blockers to a real first transaction.

This file is the index for any AI agent working in this repo. Read it before doing anything. It captures the Yeko Studio mindset, the project spec digest, the conventions we'll follow, and the open questions still to resolve.

---

## 🧠 YeKo Studio mindset (non-negotiable)

YeKo builds **operational systems for SMBs that already make money**. Not prototypes, not theory, not pretty websites. The bar is "did we reduce real operational chaos and increase real revenue?" If a feature doesn't pass that bar, it shouldn't be built.

When working in this repo:

- **Simple > complex.** If you find yourself reaching for microservices, message queues, or abstract patterns, stop and reconsider. The smallest thing that fixes the bottleneck is usually right.
- **Build first, sell after.** This is a real business backend, not a demo. Every endpoint should map to something a customer or shop owner actually does.
- **Execute, don't theorize.** Ship the obvious answer fast and iterate. Long architecture debates are worse than imperfect code that ships.
- **Frontend keeps frontend work.** Image processing (edge detection, mask generation) runs in the browser via OpenCV.js. The backend stores files, not pixels. Don't move work backend just because it's "easier on the server".

The 1-line filter: *"If a solution doesn't improve the business operation, it isn't worth building."*

---

## 🎯 Project spec digest

A web app where customers upload an image, a frontend editor proposes a die-cut outline + lets them mark "relief" zones, then they place an order and pay. **Backend is responsible for ~5 things only:**

1. **Auth + user accounts** — registration, login, profile.
2. **Order management** — create order, attach files, track status (placed → paid → in production → shipped → delivered).
3. **File ingestion** — receive uploaded images + die-cut mask + relief mask from the frontend, persist them.
4. **Stripe integration** — payment intents, webhook handling, order state transitions on payment events.
5. **Admin/management** — Django admin for the shop owner + DRF endpoints for a future custom admin UI.

**Backend is explicitly NOT responsible for:**
- Image processing (OpenCV.js runs in the browser; backend stores whatever the frontend uploads).
- Edge detection or mask generation.
- Vue/frontend logic of any kind.

The spec leaves a "FUTURE" door open for backend OpenCV-Python only if browser processing proves insufficient. Don't open that door until there's real evidence.

**Source of truth for the spec**: `docs/spec.md` (in this repo). The original at `/Users/cevichesmac/Downloads/Guía_StickerApp_Version2 (1).md` is kept as a backup.

---

## 🛠️ Stack — locked

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11 | Studio default |
| Framework | Django 4.2 + DRF | Studio default; auth, admin, ORM, file handling all batteries-included |
| DB | PostgreSQL 15 | Studio default |
| Auth | JWT (simplejwt) + dj-rest-auth + allauth | Same stack as LabControl — proven, allauth handles email verification |
| Payments | **Stripe** | Barcelona-based client, EU-friendly, simpler than gateway-agnostic abstractions |
| Async | None today. Celery + Redis only if a real need appears (e.g. backend OpenCV processing, large export jobs) | YAGNI |
| Infra | Docker + docker-compose, two compose files: `docker-compose.yml` (local) + `docker-compose.prod.yml` (production). **No staging.** | Per spec |
| Tests | pytest + pytest-django | Studio default |
| Lint/Format | black + isort + flake8 | Studio default |

**Multi-tenancy**: NONE. This is a single-tenant app for one print shop. Don't add `lab_client_id`-style scaffolding. If we ever onboard a second shop, that's a separate project.

---

## 📚 Reference codebase: LabControl

`/Users/cevichesmac/Desktop/labcontrol/` — Yeko's other Django backend. **Read it for patterns. Do NOT import from it. Do NOT depend on it.**

What's worth copying (adapted, not blindly):

- `apps/core/models.py` — `BaseModel`, `UUIDModel`, `TimeStampedModel` mixin pattern. Use the same.
- `apps/core/permissions.py` — `IsAdminOrLabStaff`, `IsPatientOwner`-style permission classes. We'll have `IsAdminOrShopStaff`, `IsCustomerOwner` here.
- `apps/users/models.py` — custom User with email as `USERNAME_FIELD` + role field. We'll have roles `admin`, `shop_staff`, `customer` here (no doctors, no lab_staff).
- `tests/base.py` — `BaseTestCase` with factory methods (`create_admin`, `create_customer`, `create_order`, etc.). Same pattern.
- `apps/users/auth_views.py` + `apps/users/views.py:SetPasswordView` — email verification, password setup that creates the allauth `EmailAddress` row (otherwise login silently fails).
- `Makefile` targets — `make up/down/test/migrate/shell/format/lint`.
- Settings split — `config/settings/{base,dev,prod,test}.py`.

What's specific to LabControl and NOT to copy:

- `lab_client_id` multi-tenancy — single-tenant here.
- Patient/doctor/study models — different domain.
- LabWin Firebird sync, FTP PDF fetch, Firebird container — not relevant.
- Healthcheck/deployment runbooks — different infra (will write our own when we deploy).

---

## ⚠️ Conventions to follow (will be enforced once code lands)

### UUID primary keys
All models use UUID PKs (matches LabControl). **Always `.pk`, never `.id`.**

```python
user.pk                       # ✓
str(obj.pk)                   # ✓ in test assertions
Count("pk", filter=Q(...))    # ✓ in aggregations
user.id                       # ✗ raises AttributeError
```

### Permissions
Role check: `user.role in ['admin', 'shop_staff']`. Permission classes live in `apps/core/permissions.py`.

Roles for this project:
- `admin` — Yeko + shop owner full access
- `shop_staff` — shop employees managing orders
- `customer` — end users who upload images and place orders

### Service layer vs views
**Business logic lives in `apps/<app>/services.py`, not in views or serializers.** Views call services, services do work, return DTOs/dicts/objects. This keeps views thin and tests easy. Don't put 100-line transactions inside a ViewSet method.

### Files
Uploaded images, masks, results files: `models.FileField` / `models.ImageField`, stored under `media/` (Docker volume in prod, bind mount locally). One model field per file slot — don't try to overload one field with multiple files.

### Celery
Don't add Celery + Redis for "future scalability". Add it the moment you have a concrete async need (a webhook handler that takes >2s, an export job, backend image processing). Adding it earlier is overengineering; the prompt says so.

### Tests (backend)
- All tests inherit from `tests/base.BaseTestCase`.
- Factory methods on `BaseTestCase`: `create_admin`, `create_shop_staff`, `create_customer`, `create_order`, `authenticate_as_customer`, etc.
- Run tests via `make test` (always inside Docker — `pytest` directly in venv won't work because deps live in the image).

### Frontend (separate repo: `endosstudio_frontend`)
- **Stack**: Vue 3 + Vite + TypeScript + Tailwind + Pinia + Vue Router + Axios + OpenCV.js + Stripe.js.
- **Test runner**: **Playwright** (E2E, real browser). Don't propose Cypress/Vitest/jsdom for UI tests — use Playwright. Unit tests for pure utility functions can use Vitest, but the surface that matters (editor, checkout, auth) is exercised through Playwright specs hitting the real dev server.
- **Reference UX for auto-crop**: the user has a reference site demonstrating the exact die-cut auto-crop flow they want to replicate. Ask them for the URL when frontend canvas/OpenCV work starts — don't design that component from first principles.
- **API contract is load-bearing**: this backend uses UUIDs (field name `uuid`, not `id`), money in `total_amount_cents` (integer), status as snake_case strings (`"in_production"`, not camelCase). Frontend must mirror exactly. Payment flow expects the frontend to POST `multipart/form-data` for file uploads under `OrderFile.kind ∈ {original, die_cut_mask}`.

---

## 📋 What "done" looks like for this backend

Eventually: a backend that supports the **full real-world flow** end-to-end:

1. Customer signs up → email verification → password set → can log in
2. Customer creates a draft order, uploads original image + die-cut mask + relief mask → backend stores all three files associated with the order
3. Customer goes to checkout → backend creates Stripe PaymentIntent → returns client_secret → frontend confirms payment via Stripe.js
4. Stripe webhook hits backend → backend marks order as `paid` → fires whatever side effects (email confirmation, etc.)
5. Shop staff sees paid orders in admin → moves them through `in_production` → `shipped` → `delivered` states
6. Customer can see their order history + statuses

That's the MVP. Anything beyond it (rebates, discount codes, multi-shop, admin UI in Vue) is post-MVP.

---

## 🧱 Apps structure (planned, will be created by the bootstrap skill)

```
apps/
├── core/         # BaseModel mixins, permissions, custom managers, common utils
├── users/        # User model, auth flows, registration, email verification
├── orders/       # Order model, OrderFile (uploads), order status transitions, services
└── payments/     # Stripe integration: PaymentIntent, webhooks, payment records
```

No `notifications/` app on day 1 — Django's `send_mail` straight from a service is enough. Add an app when there's a real notification surface (templates, scheduling, multi-channel).

No `analytics/` app on day 1 — admin views + Postgres queries cover it for an SMB.

---

## 🚧 Status

### Done (Milestone 1 — bootstrap + Docker local dev)
- Django 4.2 + DRF + Postgres 15 skeleton scaffolded; 4 apps (`core`, `users`, `orders`, `payments`); Docker compose; Makefile.
- Custom User installed (UUID PK, email `USERNAME_FIELD`, role admin/shop_staff/customer); migrated.
- Spec moved to `docs/spec.md`; `SESSION_START.md` archived.

### Done (Milestone 2 — orders backend + payment plumbing + auth gate)
- **Models**: `Order` (full lifecycle + simple_history audit), `OrderFile` (`unique_together(order, kind)`), `PaymentIntent` (PROTECT FK, raw_event JSON). All inherit `apps.core.models.BaseModel`. Migrations applied.
- **Pricing**: real shop formula wired (see "Repricing 2026-05-09" below for the current version). Constants in `apps/orders/services.py`, bounds (min size, step, quantity) in `apps/orders/models.py` so both layers reference the same source.
- **Service layer** (`apps/orders/services.py`): `compute_total_cents`, six lifecycle transitions (`place_order`, `transition_to_paid`, `transition_to_in_production`, `transition_to_shipped`, `mark_delivered`, `cancel_order`) with permission/status guards, `select_for_update()` row locks, `simple_history` actor attribution. `InvalidTransition` and `InvalidPricingInput` exceptions translate to 409/400 in views.
- **Stripe webhook router** (`apps/payments/views.py:StripeWebhookView`): dispatches on `event["type"]`, idempotent on replays, looks up order via `metadata.order_uuid` with fallback to `Order.stripe_payment_intent_id`. `record_payment_intent_event` upserts the local mirror.
- **Customer/staff API**: `OrderViewSet` (role-scoped queryset, draft-only PATCH guard), per-transition `@action`s (`/place`, `/checkout`, `/cancel`, `/deliver`, `/start-production`, `/ship`), `OrderFileViewSet` for multipart uploads, `PriceQuoteView`. URL surface live at `/api/v1/orders/`.
- **Stripe checkout flow**: `POST /api/v1/orders/{uuid}/checkout/` creates a Stripe `PaymentIntent`, denormalizes the PI id onto the order, returns `client_secret` for Stripe.js. Mocked in tests; live integration awaits real Stripe keys.
- **Django admin**: `Order` (with `OrderFile` inline, status/material/lifecycle fieldsets), `OrderFile` standalone, `PaymentIntent` read-only mirror.
- **Auth roundtrip gate** (`tests/test_auth_roundtrip.py`): full register → set-password → login → /me/ flow, including the explicit `EmailAddress` row check. **Passes** — the auth foundation is solid.
- **Tests**: 40 passing, 92% coverage. Run with `make test`.

### Done (Session 2026-05-03 — cut-path generation + shape field)

Sibling frontend session shipped the editor's Forma step + materials
overhaul + tight-clip halo. Backend matched it with:

- **`Order.shape`** field. Choices: `contorneado` (default — preserves
  the existing flow), `cuadrado`, `circulo`, `redondeadas`. Migrated;
  exposed on both `OrderSerializer` (read) and `OrderUpdateSerializer`
  (PATCH). Mirrored on `HistoricalOrder` via simple_history.
- **`OrderFile.kind = "cut_path"`** new slot. The shop's cutter file.
- **`apps/orders/cut_path.py`** — generates a cutter-friendly SVG per
  order at `transition_to_paid()` time (after the row lock releases;
  failure logs but doesn't unwind the paid transition):
  - `contorneado` → trace `die_cut_mask` PNG alpha contour with a
    Pillow-only Moore-neighbor walker, emit `<path d="...">`. Falls
    back to a rectangle if the customer skipped Auto cut.
  - `cuadrado` → `<rect>`.
  - `circulo` → `<ellipse>`.
  - `redondeadas` → `<rect rx=10%×min(W,H)>`.
  - SVG conventions: viewBox in mm, `stroke="red" stroke-width="0.1"
    fill="none"` (the de-facto cutter-software convention for "cut
    here"; no OpenCV-Python dep — the trace is ~50 LOC of Pillow).
- **Tests**: 7 new in `apps/orders/tests/test_cut_path.py`. Full suite
  **55/55** passing, **92%** coverage.

Frozen detail of this session: `docs/archive/SESSION_2026_05_03_cut_path.md`.

### Done (Session 2026-05-09 — repricing)

Client locked the real production pricing formula. The previous
`material_base + (W+H)·1€ + qty·1€ + flat add-ons` model is gone;
replaced with area-based pricing × quantity × material rate, with
additive percent add-ons and a 20€ floor:

```
area_factor      = ((W+15)/1000) × ((H+15)/1000)        # m², bleed-inclusive
subtotal_eur     = area_factor × qty × material_price
addon_multiplier = 1 + 0.35·relief + 0.35·tinta_blanca
                     + 0.20·barniz_brillo + 0.20·barniz_opaco
total_eur        = max(subtotal_eur × addon_multiplier, 20.00)
```

Material prices unchanged (45/50/55/60 € — same `MATERIAL_PRICE_CENTS`
table, renamed from `MATERIAL_BASE_CENTS` for clarity). New per-material
"price" is now the rate that plugs into the area formula.

Model changes (`migration 0005_repricing_addons`):
- Removed: `with_design_service`, `with_varnish` (and historical mirrors).
- Added: `with_tinta_blanca`, `with_barniz_brillo`, `with_barniz_opaco`.

Compute path uses `Decimal` end-to-end (no float drift) and
`ROUND_HALF_UP` at the cents boundary. Floor applies AFTER add-ons —
e.g. a 4€ subtotal with relief still floors to 20€, not (4+15)×1.35.

Gold-standard scenarios baked into tests:
- `vinilo_blanco 10×10cm q=100` (no add-ons) → **5951 cents (59.51€)**
  — replaces the old `holografico 5×5cm q=50 → 110€` baseline. Picked
  because it sits comfortably above the floor.
- `holografico 5×5cm q=50` → **2000 cents (20.00€)** — floor case.
- `vinilo_blanco 10×10cm q=100 +relief +brillo` → **9224 cents (92.24€)**
  — exercises additive multiplier (1 + 0.35 + 0.20 = 1.55).

UI note (frontend): the two varnish booleans (`brillo`, `opaco`) are
mutually exclusive in `OrderConfigView` via radio-group UX (none /
brillo / opaco). The model layer doesn't enforce mutual exclusion;
that's a frontend-only constraint. Picking both via a direct API call
would charge +40% — not a security issue, just a UX one.

Tests: 57 passing (was 55), 92% coverage. Frontend Playwright: 38/38.

### Bootstrap deviations from the skill (still in force)
- `django-allauth` pinned to **`>=65.0,<66.0`** (the modern `ACCOUNT_LOGIN_METHODS` / `ACCOUNT_SIGNUP_FIELDS` API only landed in 65.x).
- `dj-rest-auth` bumped to **`>=7.0,<8.0`** (allauth 65.x compat).
- `REST_AUTH["TOKEN_MODEL"] = None` added (dj-rest-auth 7.x defaults to legacy Token; we use JWT only).
- `whitenoise` moved from `prod.txt` to `base.txt` (settings reference its middleware unconditionally; dev tests need it loadable).

### Next (Milestone 3 — first real transaction)
The backend + frontend are feature-complete for the MVP loop. The
remaining blockers are operational:

1. **Stripe account + test keys**. Drop `STRIPE_PUBLISHABLE_KEY`,
   `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` into `.env`. End-to-end
   test with
   `stripe listen --forward-to localhost:8000/api/v1/payments/webhooks/stripe/`.
   Until then, `POST /api/v1/orders/{uuid}/checkout/` returns 502 and
   the frontend mocks the Stripe layer in dev.
2. **Email backend for verification + password reset**. SMTP env vars
   already wired (`EMAIL_HOST`, `EMAIL_HOST_USER`, ...) but no real
   provider configured. Pick Gmail SMTP / SES / Mailgun and ship the
   credentials.
3. **First deploy**. `docker-compose.prod.yml` is wired; needs a
   hosting choice, domain, TLS, and the SMTP creds from (2).

### TODO (longer horizon)
- Decide email backend for production (Gmail SMTP / SES / Mailgun?)
- Decide where uploaded files live in production (local volume → S3-compatible when storage grows)
- Stripe webhook signing secret rotation policy
- Whether shop owner needs a custom admin UI or Django admin is enough for MVP
- Add `/api/v1/health/` endpoint (the prod compose healthcheck references it)
- `STATICFILES_STORAGE` is deprecated in Django 5+; switch to `STORAGES` setting before that bump
- Drawn-relief PNG mask feature (currently scoped out — `with_relief: bool` + free-text note only). Add `relief_mask` to `OrderFile.KIND_CHOICES` when it lands.
- **Cutter format**: SVG (universal) ships today. If the shop uses
  Roland CutStudio / GCC GreatCut and wants the proprietary format
  directly, add a per-format exporter alongside `build_cut_svg()` in
  `apps/orders/cut_path.py`.
- **Admin "regenerate cut path" action**: `apps/orders/admin.py` could
  expose `generate_cut_path_file(order)` as an admin action so the
  shop owner can re-run it from the order detail page if the customer
  changed their mask after payment.

---

## 🧭 Decision log

Open questions where there's a working recommendation but no locked
choice yet. Update each entry to **Decided (YYYY-MM-DD): X** when the
call is made; until then it's an open question with the tradeoffs on
record so we don't relitigate from scratch next time.

> Format: each entry is **Status / Recommendation / Tradeoffs / Trigger
> to decide**. The recommendation reflects current thinking, not a
> commitment.

### Email provider for prod (`EMAIL_BACKEND` + SMTP creds)

- **Status**: open. Backend uses SMTP env vars (`EMAIL_HOST`,
  `EMAIL_HOST_USER`, …) but no real provider configured. `RegisterView`
  + password reset both depend on this; can't ship customers without it.
- **Recommendation**: **Gmail SMTP** for M3. Cheapest path to
  "customers can self-register". `App password` on a yeko@gmail or
  shop@gmail account. Move to SES or Mailgun once volume goes past
  ~500 emails/month or deliverability complaints arrive.
- **Tradeoffs**:
  - *Gmail*: zero cost, 5-min setup, deliverability is fine for low
    volume. Daily send cap (~500/day) is the ceiling; bounces are
    invisible (no webhook).
  - *SES*: cheap (~$0.10 / 1k emails), proper bounce/complaint
    webhooks, AWS account overhead.
  - *Mailgun*: best DX, ~$35/mo for 5k emails, vendor we'd be locked
    to. Faster setup than SES.
- **Trigger to decide**: before first deploy.

### Hosting target (backend + frontend)

- **Status**: open. `docker-compose.prod.yml` is wired but unused.
- **Recommendation**: **backend on a small VPS** (Hetzner CX22 / DO
  $6 droplet) + **frontend on Vercel or Netlify**. The backend is
  Docker-native and we already have `docker-compose.prod.yml`; a VPS
  matches that without a Heroku/Render abstraction tax. Frontend is
  static (Vite build); managed hosts give automatic HTTPS + previews.
- **Tradeoffs**:
  - *VPS for backend*: cheapest, full control, requires nginx/TLS
    setup once. Bus factor: an admin who knows Linux.
  - *Render/Railway/Fly for backend*: skip nginx setup, more $/mo,
    one less moving piece.
  - *Vercel for frontend*: free tier covers us, GitHub auto-deploy,
    excellent preview URLs. Locks us to their build settings.
  - *Self-host frontend behind backend's nginx*: one origin, no CORS
    headaches, but loses preview deploys + needs the backend's nginx
    to know how to serve a SPA fallback.
- **Trigger to decide**: when the deploy task starts. Domain
  registration can happen in parallel.

### Stripe account owner (yours vs. shop's)

- **Status**: open. Backend has all the Stripe wiring (PaymentIntent
  creation + webhook receiver) but no real keys. Whoever owns the
  Stripe account owns the funds.
- **Recommendation**: **the shop owner registers the Stripe account**;
  YeKo never touches the keys directly. We get test keys via shared
  password manager / 1Password share / similar for dev.
- **Tradeoffs**:
  - *Shop's account*: clean separation of money. Shop owns the
    customer relationship. They handle disputes / 1099 / VAT.
  - *YeKo's account, payouts to shop*: faster start, but YeKo on the
    hook for chargebacks + tax. Don't do this.
- **Trigger to decide**: before live keys are needed (i.e., before
  the first real customer transaction). Test keys can use either
  account in the meantime.

### Stripe webhook signing secret rotation policy

- **Status**: open. Webhook handler verifies signatures via
  `STRIPE_WEBHOOK_SECRET` from env. No rotation policy written down.
- **Recommendation**: rotate annually OR on any suspicion of
  compromise. Stripe lets us register multiple endpoints with
  separate secrets, so rotation is non-disruptive: register the new
  secret as a second endpoint, deploy with the new secret, retire
  the old endpoint.
- **Trigger to decide**: optional now. Document properly when we
  have a real shop running for >6 months (low priority — this is a
  single-tenant app for one print shop).

### Where uploaded files live in production

- **Status**: open. Currently `models.FileField` writes to local
  `media/` (Docker volume in prod). Fine until storage grows.
- **Recommendation**: **stay local through M3**, migrate to
  S3-compatible (Hetzner Object Storage / DO Spaces / Backblaze B2
  + django-storages) when total media exceeds ~10 GB or when
  deploying to multi-instance.
- **Trigger to decide**: when monthly orders pass ~50/month or
  total media >5 GB, whichever first.

---

## 📂 Files / paths to know

- **Spec (source of truth)**: `docs/spec.md` (in this repo; original at `/Users/cevichesmac/Downloads/Guía_StickerApp_Version2 (1).md` kept as backup)
- **Reference codebase**: `/Users/cevichesmac/Desktop/labcontrol/`
- **YeKo Studio context**: `/Users/cevichesmac/Desktop/yeko_studio/yeko_studio_context.md`
- **Bootstrap skill** (already executed; do not re-run): `~/.claude/skills/bootstrap-stickerapp-backend/`
- **Past-session briefings (archive)**: `docs/archive/SESSION_START.md` (M1), `docs/archive/NEXT_SESSION_M2.md` (M2), `docs/archive/SESSION_2026_05_03_cut_path.md` (M3 cut-path + shape). Read for historical context only — current state lives in this file.

---

*Index file. Edit this when conventions change or new gotchas surface. Keep it short — push detail into linked files / per-app READMEs as the codebase grows.*
