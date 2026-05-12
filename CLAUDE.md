# StickerApp Backend — AI Context

> **Studio**: YeKo Studio · **Client**: a print shop in Barcelona that sells custom stickers
> **Stack**: Python 3.11 · Django 4.2 · DRF · PostgreSQL 15 · Docker · Stripe · (Celery only if real async need)
> **Status (EOD 2026-05-12)**: M1 + M2 + M3 shipped — orders / payments / catalog / smart-cut / cut-path SVG / shape field + Oval / shipping_method + IVA pricing / shipping contact fields + admin force-status with shipping email / customer order-received emails / owner new-order email / **reservations with whitelist** (in-store pickup, Order.pickup_at) / **discounts** (Discount model + apply-discount endpoint + pricing pipeline) / product sale_price + weight_grams + category / public catalog respects ?is_active=true. 187 backend tests passing. Live Stripe keys + production SMTP + first deploy remain the operational blockers.

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

## 🧱 Apps structure (current as of 2026-05-12)

```
apps/
├── core/         # BaseModel mixins, permissions, custom managers, common utils
├── users/        # User model (+ can_reserve_orders whitelist flag),
│                 # auth flows, registration (phone_number required),
│                 # email verification, set-password,
│                 # AdminUserViewSet (staff list + PATCH for whitelist toggle)
├── orders/       # Order model (status enum includes 'reserved'; pickup_at,
│                 # discount_code, discount_cents, shipping_carrier/_tracking/_eta,
│                 # shipping_method, shipping_phone/_email), OrderFile (uploads),
│                 # status transitions (place / reserve / mark-paid / shipped /
│                 # admin-set-status / apply-discount), services (pricing pipeline,
│                 # email notifications), simple_history audit
├── payments/     # Stripe integration: PaymentIntent, webhooks, payment records
├── products/     # Catalog products (sale_price_cents, weight_grams, category FK)
│                 # + Category model + ProductRefSerializer (embedded on Order)
└── discounts/    # NEW (2026-05-12). Discount(code unique upper, percent_off 1-100,
                  # is_enabled). DiscountViewSet staff-only CRUD.
                  # Applied to Orders via apps.orders.views.apply_discount.
```

No `notifications/` app on day 1 — Django's `send_mail` straight from
a service function is enough. The three email workflows
(`_send_order_received_to_customer`, `_send_new_order_to_owner`,
`_send_shipping_notification`) live in `apps/orders/services.py`. Add
a dedicated app when there's a real notification surface (HTML
templates, scheduling, multi-channel).

No `analytics/` app on day 1 — admin views + Postgres queries cover it for an SMB.

---

## 🚧 Status

### Pick up here tomorrow (EOD 2026-05-12)

**No open thread — session ended green.** 187 backend tests passing
(0 failures, 0 errors). Everything pushed to `main`. The remaining
operational blockers (real Stripe keys, prod SMTP creds, first deploy)
are configuration tasks, not code.

If picking up next:
- **Cut-path SVG download in admin UI** — file is already generated
  at `transition_to_paid` (apps/orders/cut_path.py). Admin order
  detail screen could surface a download link. ~10 LOC frontend.
- **Smart-cut leaf-bridge tendrils** — the 2026-05-09 issue with
  morphological opening on rembg output may resurface on artwork
  with thin bridges between regions. Fix candidates documented in
  the 2026-05-09 entry below.
- **Real shipping notification email content** — currently plain text.
  HTML template + the shop's logo would polish the impression.

### Done (Session 2026-05-12 — operational polish + reservations + discounts + emails)

Big session shipped end-to-end:

**A. IVA + pricing pipeline overhaul**
- New `IVA_RATE = Decimal("0.21")` constant in services.py. Pricing
  pipeline now: `work × addons × shipping → floor at €20 → discount
  → ×1.21 IVA`. `compute_total_cents` accepts optional
  `discount_percent`; `_compute_breakdown` returns the components
  (`pre_discount_cents`, `discount_cents`, `pre_iva_cents`,
  `total_with_iva_cents`) for callers that need the discount amount
  alongside the total.
- New helpers: `subtotal_cents_of(total)` / `iva_cents_of(total)`
  reverse-derive the breakdown from `total_amount_cents` for the
  OrderSerializer's display fields.
- Catalog products use the same discount-before-IVA logic via
  `_compute_catalog_total_cents` + `_discount_percent_for_order`.
  No €20 floor on catalog (each product has its own price).
- Single source of truth: `_recompute_order_total(order)` is called
  from `place_order`, `reserve_order`, and `apply_discount_to_order`
  so totals can't drift between code paths.

**B. New apps/discounts/ app**
- `Discount(code unique upper, percent_off 1..100, is_enabled,
  history)`. Code is normalized to UPPER inside `save()` so admins
  can type 'summer2026' and customers can submit 'Summer2026' and
  both resolve to 'SUMMER2026'. Disabled codes are kept (don't
  delete) so past orders that used them keep their audit trail.
- `DiscountViewSet` — staff-only CRUD at `/api/v1/discounts/`.
  `IsAdminOrShopStaff` permission.
- Wired into `config/urls.py` and `LOCAL_APPS`.
- 8 tests in `apps/discounts/tests/test_discount_admin_api.py`.

**C. Reservations (Order.status='reserved')**
- New `'reserved'` choice between `'placed'` and `'paid'` in
  STATUS_CHOICES. Lifecycle: draft → placed → reserved → paid →
  in_production → shipped → delivered.
- `Order.pickup_at: datetime?` + `Order.reserved_at: datetime?`.
- `User.can_reserve_orders: bool` whitelist gate.
- `POST /orders/{uuid}/reserve/` — customer-only. Body
  `{pickup_at: ISO 8601}`. Requires `request.user.can_reserve_orders`,
  validates `pickup_at > now()`, runs the same fill-validation
  `place_order` does, computes the total + discount, stamps
  `reserved_at` + `placed_at` (if null) + `pickup_at`.
- `_STATUS_TIMESTAMP_FIELD` extended with `"reserved": "reserved_at"`
  so `admin_set_order_status` stamps it correctly.
- New `apps/users/views.AdminUserViewSet` for `/api/v1/users/`
  (staff list + PATCH). PATCH accepts only `can_reserve_orders`.
- 9 tests covering whitelist gate, past-datetime rejection,
  me-endpoint extension, admin list + PATCH + customer-blocked PATCH.

**D. Admin force-status + shipping email**
- `POST /orders/{uuid}/admin-set-status/` — staff-only. Body
  `{status, shipping_carrier?, shipping_tracking_code?,
  shipping_eta_date?}`. Bypasses the usual transition guards.
  Stamps the matching `*_at` timestamp.
- When `status='shipped'` AND a tracking code is provided, persists
  the carrier/tracking/ETA on the order AND fires
  `_send_shipping_notification` (plain-text email to the customer
  with carrier name + tracking code + ETA).
- `Order` gains `shipping_carrier`, `shipping_tracking_code`,
  `shipping_eta_date`.
- New `GET /orders/shipping-carriers/` — staff-only, returns
  `DISTINCT shipping_carrier` from past orders. Drives the admin
  popup's autosuggest.
- 10 tests in `apps/orders/tests/test_admin_set_status.py`.

**E. Customer + owner order-received emails**
- `_send_order_received_to_customer(order)` fires from
  `transition_to_paid` AND `reserve_order`. Subject branches by
  status (paid vs reserved); reservation body includes
  `pickup_at` + "en efectivo, al retirar".
- `_send_new_order_to_owner(order)` — same trigger paths. Sends to
  the new `settings.SHOP_OWNER_EMAIL` env var (falls back to
  `DEFAULT_FROM_EMAIL`). Body includes recipient name + email,
  total, kind, pickup info for reservations.
- Both synchronous via Django's default email backend. SMTP failures
  are logged but never raised — losing an email can't unwind a
  successful order transition. Top-level `send_mail` import (not
  function-scoped) so tests can `mock.patch("apps.orders.services.send_mail")`.
- 8 tests in `apps/orders/tests/test_order_emails.py`.

**F. Shipping method + contact fields**
- `Order.shipping_method ∈ {normal, express, flash}`, with `+0%/+20%/+60%`
  surcharges that stack into the existing `addon_multiplier`.
- `Order.shipping_phone` (required at place_order — model layer
  `default=""` so the change is non-disruptive, but place_order's
  fill-validation rejects empty values), `Order.shipping_email`
  (optional).
- `RegisterSerializer.phone_number = serializers.CharField(...)` —
  required on new signups. Existing User rows with blank phones
  remain valid (model-level field stays blankable for migration safety).

**G. Product enrichment**
- `Product.sale_price_cents` (nullable) — when set, supersedes
  `price_cents` via `effective_price_cents` property. Catalog UI
  shows strikethrough + discounted price.
- `Product.weight_grams` (nullable) — captured now so a future
  weight-aware shipping rate is a data change, not a migration.
- `Product.category = FK(Category, SET_NULL)` — new
  `apps/products/Category` model. ProductWriteSerializer accepts
  `category` as free text; dedupes by slug via `update_or_create`.
- Public catalog `?is_active=true` filter now honored EVEN for staff
  visitors (Frontend always passes it from `/catalogo`; admin
  product list at `/admin/products` skips it to see everything).
- `ProductRefSerializer` (the embed on `Order.product_detail`)
  carries `sale_price_cents` + `effective_price_cents` so the
  checkout summary renders the strikethrough without a second fetch.

**H. Order shape: 'oval'**
- Fifth Shape choice between `redondeadas` and... well, last in the
  list. Frontend renders it at a fixed 2:1 horizontal aspect
  (deliberately distinct from `circulo` which fits the image's
  aspect). Backend just accepts the value; no special handling.
- Frontend: drag-to-move support for ALL geometric shapes
  (cuadrado / circulo / oval / redondeadas) via image-natural-pixel
  offset. Backend stores the resulting polygon in OrderFile if/when
  the customer hits Continuar; the offset itself is editor-session
  state and not persisted.

**I. Tests**
- 187 passing total, up from 137 at session start (+50 new tests
  across: shipping_pricing (gold standard repriced for IVA), catalog
  lifecycle, admin_set_status, reserve, admin_users_api, order_emails,
  apply_discount, discount_admin_api).
- The gold-standard scenarios in the regression suite:
  - `vinilo_blanco 10×10 q=100`: pre-IVA 5951 cents → ×1.21 = **7201 cents** (was 5951).
  - With `+relief +brillo`: pre-IVA 9224 → ×1.21 = **11161 cents** (was 9224).
  - With WELCOME10 (10% off, no add-ons): pre-discount 5951 → discount 595 → pre-IVA 5356 → ×1.21 = **6481 cents**.
  - Catalog €15 product × 1, no add-ons → ×1.21 = **1815 cents**.

### Local dev caveat: rembg in the running container is ephemeral

### Local dev caveat: rembg in the running container is ephemeral

**The Dockerfile change baking `isnet-general-use.onnx` into `/app/.u2net/`
is correct, but the local image rebuild failed today** with a Docker
Hub Cloudflare R2 timeout fetching `python:3.11-slim` metadata. The
running `web` container was rescued by `docker compose exec -T web pip
install "rembg[isnet,cpu]>=2.0,<3.0"` directly — that gets the dev
loop working, but **those packages disappear on `docker compose down &&
up`** (only the source bind-mount survives, not pip-installed packages).

To recover the rembg install if the container is recycled before the
real rebuild succeeds:

```sh
docker compose exec -T web pip install "rembg[isnet,cpu]>=2.0,<3.0"
docker compose exec -T web python -c \
  "from rembg import new_session; new_session('isnet-general-use')"
```

The model file lands in `/app/.u2net/isnet-general-use.onnx` (~170 MB).
The `.u2net/` dir at the repo root is the host-side mirror (created by
the bake step's `cp -r /root/.u2net/. /app/.u2net/`); add to
`.gitignore` if not already.

The proper rebuild via `docker compose build web` will work whenever
the network cooperates. Run `docker compose build --no-cache web` if
you need to force-rebuild.

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

### Done (Session 2026-05-09 — catalog + Order.kind, M3a)

The shop sells more than custom stickers — llaveros and similar fixed
SKUs. Rather than refactor the whole `Order` table to support mixed
carts (sticker + product in one order, that's M3b), M3a ships catalog
products as their own orders with a discriminator field.

**New app: `apps.products`** — `Product(BaseModel)` with `name`, `slug`
(auto-generated, unique), `description`, `price_cents`, `stock_quantity`,
`image` (ImageField), `is_active`, simple_history mirror. Public list +
retrieve via `/api/v1/products/` (no auth needed, drives signups via the
buy flow); staff CRUD gated by `IsAdminOrShopStaff`.

**Order.kind discriminator** (`apps/orders/models.py`):
- `"sticker"` (default — all M2 behavior unchanged)
- `"catalog"` (new — single Product + product_quantity; sticker spec
  fields are null/zero)
- `clean()` enforces the XOR (sticker orders cannot carry a product;
  catalog orders cannot set sticker spec fields)

Migration `0006_order_kind_product` adds three columns to `Order`:
`kind`, `product` (PROTECT FK to products.Product), `product_quantity`.
All defaults are backfill-safe (existing orders default to
`kind="sticker"` and continue to work without changes).

**Service-layer branching** in `apps/orders/services.py`:
- `compute_total_cents` → branch on order.kind. Catalog =
  `product.price_cents × product_quantity`. Sticker = unchanged.
- `place_order` → `_validate_sticker_required` vs
  `_validate_catalog_required`. Catalog requires product set, qty ≥ 1,
  `product.is_active`, and `stock_quantity >= product_quantity` (initial
  check). Shipping required for both kinds.
- `transition_to_paid` → for catalog orders, locks the Product row with
  `select_for_update()` and decrements `stock_quantity`. Cut-path SVG
  generation only runs for sticker kind. If stock dropped under the
  paid order at this point (race), the oversell is logged but allowed —
  the SMB-correct tradeoff (shop reconciles).

**Checkout stock re-check** (`OrderViewSet.checkout` action): catalog
orders re-fetch `product.stock_quantity` before creating the Stripe
PaymentIntent and return HTTP 409 with `{"detail":
"insufficient_stock"}` if short. Cleaner than refunding a charged card.

**Cancel-after-paid intentionally NOT implemented for catalog**: the M2
contract is "no self-service refund after paid; admin handles via
Stripe dashboard." Same rule for catalog; if/when the shop refunds, a
future `charge.refunded` webhook handler or admin "Re-credit stock"
action will restore inventory.

**Order serializer + admin updated**: nested `product_detail`
(ProductRefSerializer subset: name, slug, image, price_cents) is
included in `OrderSerializer` so the frontend renders the catalog
summary without a second API call. `OrderAdmin.get_fieldsets()` returns
the "Sticker spec" or "Catalog item" fieldset based on `obj.kind`. New
`POST /api/v1/orders/` accepts `{kind: "catalog", product: <uuid>,
product_quantity: <n>}` for catalog draft creation.

**Tests**: 17 new (`test_product_api.py` + `test_product_admin_api.py` +
`test_models.py` for the kind XOR + `test_catalog_lifecycle.py` for
place/paid/checkout). Sticker regression suite stays untouched and
green. **93 passing total, 93% coverage.**

**Frontend mirror**: `OrderKind`, `Product`, `ProductRef` types added.
New views `/catalogo`, `/catalogo/:slug`, `/admin/products`,
`/admin/products/new`, `/admin/products/:slug/edit`. CheckoutView /
ConfirmationView / DashboardView / OrderHistoryCard branch on
`order.kind` for kind-aware rendering.

**Deferred to M3b**: `OrderItem` table; mixed cart; refund-driven stock
re-credit; product variants; image gallery; stock reservation on
placement; categories/search/filters; discount codes.

### Done (Session 2026-05-09 — smart-cut / rembg AI background removal)

The editor's classical OpenCV.js auto-cut handles ~80% of customer
images, but fails on artwork colors that overlap with the background
(gorilla face/fur on teal), busy backgrounds, and isolated multi-piece
designs. Added `rembg` (isnet-general-use ONNX, ~170 MB) as an opt-in
upgrade button in the editor — the existing OpenCV.js auto-cut stays
as the fast default.

Architecture:
- `apps.orders.services_smart_cut.smart_cut_for_order(order)` —
  module-cached rembg session, sync-blocking (~2-4 s CPU per 1024 px).
  Returns `{kind, points, artwork_points, area_px}`. Reuses the
  Moore-tracer from `apps.orders.cut_path._walk_alpha_contour` (refactored
  out of the SVG-generation code path) so the silhouette extraction is
  shared between cutter-file generation and smart-cut.
- `POST /api/v1/orders/{uuid}/smart-cut/` — DRF `@action` on
  `OrderViewSet`. Allowed on any status (read-only, doesn't mutate the
  order). 400 on missing `original` file, 503 on rembg load failure,
  200+`kind=ok` or 200+`kind=no-contour-found` on success. Ownership
  enforced via `get_queryset` (customers see only their own orders).
- No bleed-margin offset on the backend — frontend already owns
  `offsetPolygonOutward` + `marginMm` slider + `pxPerMm` derivation in
  `useAutoCropWorker.ts`. Smart cut returns the tight artwork outline;
  customers who want margin re-run classical Auto cut.

Dependencies + Docker:
- `rembg[isnet,cpu]>=2.0,<3.0` in `requirements/base.txt`. The `[cpu]`
  extra is required (rembg 2.0.7+ split onnxruntime out of the base
  package; without it `new_session()` raises "No onnxruntime backend
  found").
- Dockerfile bakes the 170 MB ONNX into `/app/.u2net/` at build time so
  prod cold-starts don't block 5-10 s downloading from GitHub Releases.
  `U2NET_HOME` env var redirects the lookup so the non-root `app` user
  can read the model file. Image bloats by ~170 MB; controlled, no
  rate-limit risk.

Tests: 9 new in `apps/orders/tests/test_smart_cut.py` (5 endpoint, 4
service-level). All mock `apps.orders.services_smart_cut.remove` so the
test suite stays sub-second and doesn't need the model file present.
**102/102 passing total, 93% coverage.**

Frontend mirror: new `'smart-cut'` button in `EditorToolbar.vue`,
`onSmartCut` handler in `EditorView.vue` calling
`ordersService.smartCut(uuid)`. Disabled when no `original` file
uploaded, when shape isn't `contorneado`, or while the classical
auto-cut is running. Reuses the existing `editor-processing` banner.

#### Polishing work later in the same session (uncommitted as of EOD)

After initial smoke testing the customer found:
1. The cut polygon needed to lock out classical Auto cut while smart-cut
   was active (overwriting was a foot-gun).
2. The margin slider needed to re-inflate the smart-cut polygon locally
   (no server round-trip per slider drag).
3. The bleed margin needed the source image's "feel" — for the gorilla
   on teal, customer expects teal vinyl extending outward, not random
   truncated artwork bits.

Implemented (uncommitted):
- **`cleaned_image_data_url`** added to the smart-cut response — the
  rembg RGBA encoded as a base64 PNG inline data URL. Frontend swaps
  it in as the canvas's base layer when smart-cut is active so margin
  expansion shows transparent ring (or material halo) in the bleed
  area instead of truncated source-image artwork.
- **Morphological opening on the rembg alpha** before contour tracing
  (`PIL.ImageFilter.MinFilter(13)` then `MaxFilter(13)`). Drops thin
  appendages — single-pixel-wide bridges between the main silhouette
  and decorative bits like leaves/sparkles/feathers — and tiny
  disconnected islands. Without this, those thin bridges become huge
  curving "tendrils" when the frontend offsets the polygon outward by
  the bleed margin (the boundary walks IN to the body, OUT along a
  leaf bridge to the tip, BACK along the same bridge → offset becomes
  a long curving outward horn perpendicular to the bridge). The
  opened alpha is also composed back into the cleaned RGBA so the
  visible image and the cut polygon match — appendages dropped from
  both consistently.

#### Known issue carried into tomorrow

**Margin slider on smart-cut still produces visual breakage at
high values on complex artwork.** Gorilla illustration at margin
30 mm shows the expected silhouette + tendril artifacts despite:
- Pre-smoothing the polygon with margin-scaled passes (1 pass / 8 px
  of offset, max 50) before `offsetPolygonOutward`.
- Morphological opening at the source mask.

The morph-opening fix should help with ANY rembg output that has
thin attachments to the main body, but the customer reported the
behavior persists. Likely causes to investigate next:

1. **Kernel size of 13 px isn't enough** for the gorilla's specific
   leaf-bridge widths. Bumping to 21 or 31 may help but risks
   eroding wider-but-legitimate fur tufts.
2. **Multiple disconnected components** still surviving the open. The
   contour walker takes the FIRST inside pixel scanning row-major;
   it might be picking the wrong island (a stray decorative bit
   instead of the main body) when the open partially separates them.
   Fix: pick the LARGEST connected component instead of the first.
3. **The frontend `offsetPolygonOutward` self-intersects** on
   complex inputs even after pre-smoothing. May need a real polygon-
   offset library (Clipper2 / Martinez); pure normal-bisector offset
   is mathematically incorrect for non-convex polygons.

For the next session: try (2) first — it's a small, targeted change
to `_walk_alpha_contour` (or a sibling helper) and is the cheapest
diagnostic. If artifacts persist, fall back to (3) — that's a real
dependency add but is the proper fix.

Deferred to M3b:
- Async job pattern (Celery + Redis) once volume passes ~100 calls/day.
- Caching by file-bytes hash (5x speedup on re-clicks).
- Multi-piece detection (today we keep the largest contour only).
- Backend bleed-margin offset (mirror `offsetPolygonOutward` in Python).
- "Source-bg color × material texture" preview in the bleed area
  (sample the source image's edge bg color, render the chosen
  material's texture in the bleed at near-100% opacity tinted by
  that color — matches the printed-sticker look).

### Done (Session 2026-05-10 — smart-cut perf rewrite + bugfix)

#### A. Smart-cut margin handling moved to the backend

The previous JS-side `offsetPolygonOutward` was mathematically wrong on
non-convex polygons (per-vertex normal-bisector offset self-intersects
on sharp concavities). Replaced with a backend dilation pipeline.

`apps/orders/services_smart_cut.py` rewrite:
- New `margin_mm` parameter (clamped to `MIN_MARGIN_MM=5`).
- Replaced PIL `MaxFilter` with `scipy.ndimage.binary_dilation` —
  same Minkowski-sum semantics, ~1000× faster on big kernels.
- Mask processing (morph-open, dilate, contour walk) runs on a
  downsampled 512-px-long-edge copy. Polygon coords scale back to
  natural pixels before serializing. Final RGBA compose stays full-res.
- Cleaned RGBA preserves ORIGINAL source RGB pixels in the bleed
  ring, gated by the dilated alpha mask. Customer asked for
  "background extending outward" feel — this delivers it.
- Gaussian-smooth pass on both artwork and cut masks before the
  contour walker, controlled by new `smoothness` param (1-10,
  default 5). Fills narrow concavities a vinyl plotter can't follow.
- Per-step timing logs left in service for regression detection.

`apps/orders/views.py`:
- Endpoint accepts `margin_mm` and `smoothness` (body or query
  params), with 400 on non-int input.

`apps/orders/apps.py` (NEW logic):
- `OrdersConfig.ready()` warms the rembg ONNX session in a
  background thread at Django boot, so the first customer no longer
  eats the 25-40 s cold-start. Skipped under management commands so
  `manage.py test` / `migrate` stay fast. Override via env var
  `DJANGO_SKIP_REMBG_WARMUP=1`.

Real timings on the gorilla (832×924 PNG, warm session):
- Before: ~10-15s warm + 33s cold-start
- After: **~2.3s warm**, ~3-5s on the second-warm-call after a
  slider drag

`apps/orders/tests/test_smart_cut.py`:
- 11 tests passing (was 9). New: `test_cut_polygon_is_larger_than_artwork_polygon`
  (regression for "no margin" bug); `test_margin_below_floor_is_clamped_to_5mm`.
- Existing tests updated for the new contract — `points` (cut polygon
  with bleed) is now strictly larger than `artwork_points` (tight
  silhouette).

#### B. PATCH response shape bug fix

`OrderViewSet.partial_update` was returning `OrderUpdateSerializer.data`
— write-only fields (material, shape, dimensions, shipping) without
`uuid`, `status`, `total_amount_cents`, `files`, etc. The frontend
stored that as `order.value` and subsequent reads of `order.value.uuid`
got undefined → `POST /orders/undefined/...` → 404 → "Falló el
recorte inteligente" toast.

Fix: override `partial_update` to use the write serializer for input
validation but return `OrderSerializer.data` for the response. Mirrors
the same workaround `ProductViewSet` already uses (per CLAUDE.md
"Backend response shape note").

All 62 existing order tests still pass.

#### C. Frontend companion changes (in `endosstudio_frontend`, not this repo)

For context — the frontend session log is in
`endosstudio_frontend/CLAUDE.md`, but the relevant backend-touching
parts:
- Smart-cut margin slider re-calls the backend debounced 600ms
  (passing `margin_mm`).
- Smoothness slider also re-calls the backend in smart-cut mode
  (passing `smoothness`).
- Per-call axios timeout bumped to 90s to absorb cold-starts.
- New WebGL holographic FX layer (`useHolographicFX.ts`) — server
  contract unchanged; this is purely frontend rendering.
- AI macro reference textures (`docs/material-textures-prompts.md`,
  `src/assets/textures/*_macro.png`) — server contract unchanged;
  textures live entirely on the frontend bundle.

### Bootstrap deviations from the skill (still in force)
- `django-allauth` pinned to **`>=65.0,<66.0`** (the modern `ACCOUNT_LOGIN_METHODS` / `ACCOUNT_SIGNUP_FIELDS` API only landed in 65.x).
- `dj-rest-auth` bumped to **`>=7.0,<8.0`** (allauth 65.x compat).
- `REST_AUTH["TOKEN_MODEL"] = None` added (dj-rest-auth 7.x defaults to legacy Token; we use JWT only).
- `whitenoise` moved from `prod.txt` to `base.txt` (settings reference its middleware unconditionally; dev tests need it loadable).

### Next (Milestone 3 — first real transaction)
The backend + frontend are feature-complete for the MVP loop AND
for the reservation / discount / multi-email workflows. Remaining
blockers are operational:

1. **Stripe account + test keys**. Drop `STRIPE_PUBLISHABLE_KEY`,
   `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` into `.env`. End-to-end
   test with
   `stripe listen --forward-to localhost:8000/api/v1/payments/webhooks/stripe/`.
   Until then, `POST /api/v1/orders/{uuid}/checkout/` returns 502 and
   the frontend mocks the Stripe layer in dev. Note that the
   **reservation path bypasses Stripe entirely** — that's already
   working end-to-end for whitelisted customers.
2. **Email backend for production**. Five email surfaces depend on
   it: verification, password reset, customer order-received,
   owner new-order notification, customer shipping tracking. SMTP
   env vars wired (`EMAIL_HOST`, `EMAIL_HOST_USER`, …) +
   `SHOP_OWNER_EMAIL` (added 2026-05-12) but no real provider
   configured. Pick Gmail SMTP / SES / Mailgun and ship the
   credentials. Until then, the console backend prints emails to
   the Django logs in dev — you can verify content end-to-end.
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
  `EMAIL_HOST_USER`, …) but no real provider configured.
  `RegisterView` + password reset + the three new email workflows
  (customer order-received, owner new-order, customer shipping
  tracking — all shipped 2026-05-12) depend on this. Console
  backend is the dev default; emails print to logs.
- **`SHOP_OWNER_EMAIL` env var** (added 2026-05-12) — owner
  notifications go here. Falls back to `DEFAULT_FROM_EMAIL` when
  blank. Both `.env` and `.env.example` carry placeholder values.
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
