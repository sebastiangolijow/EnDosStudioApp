# StickerApp Backend — AI Context

> **Studio**: YeKo Studio · **Client**: a print shop in Barcelona that sells custom stickers
> **Stack**: Python 3.11 · Django 4.2 · DRF · PostgreSQL 15 · Docker · Stripe · (Celery only if real async need)
> **Status**: greenfield — no code committed yet. CLAUDE.md is the source of truth until the bootstrap skill runs.

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

**Source of truth for the spec**: `/Users/cevichesmac/Downloads/Guía_StickerApp_Version2 (1).md` (move it into this repo as `docs/spec.md` once we scaffold).

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

### Tests
- All tests inherit from `tests/base.BaseTestCase`.
- Factory methods on `BaseTestCase`: `create_admin`, `create_shop_staff`, `create_customer`, `create_order`, `authenticate_as_customer`, etc.
- Run tests via `make test` (always inside Docker — `pytest` directly in venv won't work because deps live in the image).

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

## 🚧 Status (project start)

### Done
- ✅ Folder created
- ✅ CLAUDE.md written
- ⏳ `bootstrap-stickerapp-backend` skill (in progress — see `~/.claude/skills/`)

### Next (after the skill exists)
- Run the bootstrap skill to lay down the Django project skeleton
- Move the spec into `docs/spec.md`
- First real feature: User model + auth flow (mirror LabControl's pattern, adapted for our roles)

### TODO (to keep on the radar)
- Decide email backend for production (SMTP via Gmail / SES / Mailgun?)
- Decide where uploaded files live in production (local volume to start; S3-compatible if/when storage grows)
- Stripe webhook signing secret rotation policy
- Whether the shop owner needs a custom admin UI or Django admin is enough for MVP

---

## 📂 Files / paths to know

- **Spec (source of truth)**: `/Users/cevichesmac/Downloads/Guía_StickerApp_Version2 (1).md` (move into repo at first scaffold)
- **Reference codebase**: `/Users/cevichesmac/Desktop/labcontrol/`
- **YeKo Studio context**: `/Users/cevichesmac/Desktop/yeko_studio/yeko_studio_context.md`
- **Bootstrap skill** (once created): `~/.claude/skills/bootstrap-stickerapp-backend/`

---

*Index file. Edit this when conventions change or new gotchas surface. Keep it short — push detail into linked files / per-app READMEs as the codebase grows.*
