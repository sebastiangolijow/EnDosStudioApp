"""
Order domain models.

Intentionally empty at bootstrap. The next session designs:
  - Order (status: draft → placed → paid → in_production → shipped → delivered → cancelled)
  - OrderFile (FK to Order, FK to file slot type, FileField, mime, size)
  - The relationship between Order, the original image, the die-cut mask, and the relief mask

Don't add models here yet. Read the spec, talk through the lifecycle, then design.
"""
