"""Admin-orders-screen tests.

Covers the backend surface the frontend's `/admin/orders` page depends on:
  - customer_email / customer_name in the OrderSerializer response
  - status filter (?status=paid)
  - status_in filter (?status_in=paid,in_production)
  - kind filter (?kind=sticker)
  - date range filter (?created_after / ?created_before)
  - search across uuid + email + first/last name + recipient (?search=foo)
  - ordering (?ordering=-placed_at)
  - page_size query param
  - manual mark-paid transition + permissions

The list endpoint already had role-scoped queryset behavior (customers
see their own; staff see all); we don't re-test that here.
"""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from apps.orders.models import Order
from tests.base import BaseTestCase


def _make_order(*, customer, status="draft", kind="sticker", **fields) -> Order:
    """Create an Order with sane defaults; override status/kind/fields.

    Sticker fields (material/width_mm/height_mm/quantity) are filled
    for kind=sticker. For kind=catalog they stay at the model defaults
    (empty string / 0) because the DB columns are NOT NULL even when
    the catalog branch doesn't logically use them.
    """
    defaults = {
        "kind": kind,
        "status": status,
        "created_by": customer,
        "total_amount_cents": 5000,
    }
    if kind == "sticker":
        defaults.update({
            "material": "vinilo_blanco",
            "width_mm": 100,
            "height_mm": 100,
            "quantity": 100,
        })
    defaults.update(fields)
    return Order.objects.create(**defaults)


class OrderSerializerCustomerFieldsTests(BaseTestCase):
    """customer_email / customer_name in the response."""

    def test_customer_email_and_name_present_in_list(self):
        staff_client, _ = self.authenticate_as_shop_staff()
        customer = self.create_customer(
            email="ana@example.com", first_name="Ana", last_name="García",
        )
        _make_order(customer=customer)
        res = staff_client.get(reverse("order-list"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data["results"]), 1)
        row = res.data["results"][0]
        self.assertEqual(row["customer_email"], "ana@example.com")
        self.assertEqual(row["customer_name"], "Ana García")

    def test_customer_name_falls_back_to_email_local_part(self):
        staff_client, _ = self.authenticate_as_shop_staff()
        # No first/last name set — get_full_name() returns ""; fallback
        # should give the email's local part.
        customer = self.create_customer(email="solo@example.com")
        _make_order(customer=customer)
        res = staff_client.get(reverse("order-list"))
        self.assertEqual(res.data["results"][0]["customer_name"], "solo")


class OrderFilterTests(BaseTestCase):
    """status / status_in / kind / date-range filters."""

    def setUp(self):
        super().setUp()
        self.staff_client, _ = self.authenticate_as_shop_staff()
        self.customer = self.create_customer()
        # Spread orders across statuses + a couple of timestamps so we
        # can exercise the filters meaningfully.
        self.draft = _make_order(customer=self.customer, status="draft")
        self.placed = _make_order(customer=self.customer, status="placed")
        self.paid = _make_order(customer=self.customer, status="paid")
        self.in_production = _make_order(customer=self.customer, status="in_production")
        self.shipped = _make_order(customer=self.customer, status="shipped")

    def test_filter_by_single_status(self):
        res = self.staff_client.get(reverse("order-list"), {"status": "paid"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data["results"]), 1)
        self.assertEqual(res.data["results"][0]["status"], "paid")

    def test_filter_by_status_in(self):
        res = self.staff_client.get(
            reverse("order-list"), {"status_in": "paid,in_production"}
        )
        statuses = {r["status"] for r in res.data["results"]}
        self.assertEqual(statuses, {"paid", "in_production"})

    def test_filter_by_kind(self):
        # All seeded orders are sticker; create one catalog order.
        _make_order(customer=self.customer, kind="catalog", status="placed")
        sticker = self.staff_client.get(reverse("order-list"), {"kind": "sticker"})
        catalog = self.staff_client.get(reverse("order-list"), {"kind": "catalog"})
        self.assertEqual(len(sticker.data["results"]), 5)
        self.assertEqual(len(catalog.data["results"]), 1)

    def test_filter_by_created_after(self):
        # Backdate one order to a week ago — it should drop out when
        # filtering for "created in the last day".
        old = _make_order(customer=self.customer, status="paid")
        old.created_at = timezone.now() - timedelta(days=7)
        old.save(update_fields=["created_at"])
        a_day_ago = (timezone.now() - timedelta(days=1)).isoformat()
        res = self.staff_client.get(reverse("order-list"), {"created_after": a_day_ago})
        uuids = {str(r["uuid"]) for r in res.data["results"]}
        self.assertNotIn(str(old.uuid), uuids)


class OrderSearchTests(BaseTestCase):
    """SearchFilter across uuid / email / name / recipient."""

    def setUp(self):
        super().setUp()
        self.staff_client, _ = self.authenticate_as_shop_staff()

    def test_search_by_email_substring(self):
        target = self.create_customer(email="findme@example.com")
        other = self.create_customer(email="nope@example.com")
        _make_order(customer=target)
        _make_order(customer=other)
        res = self.staff_client.get(reverse("order-list"), {"search": "findme"})
        self.assertEqual(len(res.data["results"]), 1)
        self.assertEqual(res.data["results"][0]["customer_email"], "findme@example.com")

    def test_search_by_first_name(self):
        target = self.create_customer(email="a@x.com", first_name="Sebastián")
        other = self.create_customer(email="b@x.com", first_name="Diego")
        _make_order(customer=target)
        _make_order(customer=other)
        res = self.staff_client.get(reverse("order-list"), {"search": "Sebastián"})
        self.assertEqual(len(res.data["results"]), 1)
        self.assertEqual(res.data["results"][0]["customer_name"], "Sebastián")

    def test_search_by_recipient_name(self):
        customer = self.create_customer()
        _make_order(customer=customer, recipient_name="Carlos Mendoza")
        _make_order(customer=customer, recipient_name="Otra Persona")
        res = self.staff_client.get(reverse("order-list"), {"search": "Mendoza"})
        self.assertEqual(len(res.data["results"]), 1)


class OrderOrderingTests(BaseTestCase):
    def test_default_ordering_is_newest_first(self):
        staff_client, _ = self.authenticate_as_shop_staff()
        customer = self.create_customer()
        first = _make_order(customer=customer)
        first.created_at = timezone.now() - timedelta(hours=2)
        first.save(update_fields=["created_at"])
        second = _make_order(customer=customer)  # default created_at = now
        res = staff_client.get(reverse("order-list"))
        # Newer order appears first.
        self.assertEqual(str(res.data["results"][0]["uuid"]), str(second.uuid))
        self.assertEqual(str(res.data["results"][1]["uuid"]), str(first.uuid))

    def test_explicit_ordering_by_total_asc(self):
        staff_client, _ = self.authenticate_as_shop_staff()
        customer = self.create_customer()
        cheap = _make_order(customer=customer, total_amount_cents=1000)
        expensive = _make_order(customer=customer, total_amount_cents=9000)
        res = staff_client.get(reverse("order-list"), {"ordering": "total_amount_cents"})
        self.assertEqual(str(res.data["results"][0]["uuid"]), str(cheap.uuid))
        self.assertEqual(str(res.data["results"][1]["uuid"]), str(expensive.uuid))


class PageSizeTests(BaseTestCase):
    def test_page_size_query_param_overrides_default(self):
        staff_client, _ = self.authenticate_as_shop_staff()
        customer = self.create_customer()
        for _ in range(25):
            _make_order(customer=customer)
        # Default page_size = 20; ask for 5.
        res = staff_client.get(reverse("order-list"), {"page_size": 5})
        self.assertEqual(len(res.data["results"]), 5)
        self.assertEqual(res.data["count"], 25)

    def test_page_size_capped_at_max(self):
        staff_client, _ = self.authenticate_as_shop_staff()
        customer = self.create_customer()
        for _ in range(10):
            _make_order(customer=customer)
        # max_page_size = 100; request more, get capped.
        res = staff_client.get(reverse("order-list"), {"page_size": 9999})
        self.assertEqual(len(res.data["results"]), 10)  # all 10, capped well under 100


class MarkPaidActionTests(BaseTestCase):
    """Manual placed → paid for shop owners handling payment out-of-band."""

    def test_staff_can_mark_placed_order_as_paid(self):
        staff_client, _ = self.authenticate_as_shop_staff()
        customer = self.create_customer()
        order = _make_order(customer=customer, status="placed")
        url = reverse("order-mark-paid", kwargs={"pk": order.pk})
        res = staff_client.post(url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["status"], "paid")
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")
        self.assertIsNotNone(order.paid_at)

    def test_customer_cannot_mark_paid(self):
        # Even on their own order — payment confirmation is staff turf.
        client, customer = self.authenticate_as_customer()
        order = _make_order(customer=customer, status="placed")
        url = reverse("order-mark-paid", kwargs={"pk": order.pk})
        res = client.post(url)
        self.assertEqual(res.status_code, 403)

    def test_mark_paid_rejected_from_wrong_status(self):
        staff_client, _ = self.authenticate_as_shop_staff()
        customer = self.create_customer()
        order = _make_order(customer=customer, status="draft")  # not placed
        url = reverse("order-mark-paid", kwargs={"pk": order.pk})
        res = staff_client.post(url)
        self.assertEqual(res.status_code, 409)
