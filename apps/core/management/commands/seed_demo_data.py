"""Realistic demo data for presentations and development.

Everything goes through the real services (payment settlement, refunds, ledger, points, reviews), so the dashboards,
ledger and reconciliation check behave exactly as they would with live data. Demo accounts all use @demo.parkshare.test.
"""
import io
import random
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import DriverVehicle, OwnerProfile, PointLog, User
from apps.accounts.points import apply_points
from apps.bookings import lifecycle
from apps.bookings.availability import peak_concurrency, within_opening_hours
from apps.bookings.models import Booking, DriverVerdict
from apps.bookings.services import money, split_amount
from apps.core.models import Notification, SiteSettings
from apps.parkings import services as parking_services
from apps.parkings.models import Parking, ParkingPhoto, ParkingVehicleType
from apps.payments import services as pay
from apps.payments.models import OwnerLedger, Payment, Refund
from apps.reviews.models import ParkingReview
from apps.reviews.services import recompute_parking_rating

DOMAIN = "demo.parkshare.test"
PASSWORD = "Demo@12345"
S = Booking.Status

OWNERS = [("owner1", "Anita Mohanty", "Odisha Park Co", "9810000001"),
          ("owner2", "Rakesh Behera", "City Wheels Parking", "9810000002"),
          ("owner3", "Sonali Das", "Coastal Spots", "9810000003")]
DRIVERS = ["Asha Rout", "Biswajit Nayak", "Chandan Sahoo", "Deepa Pradhan", "Elina Patra", "Farhan Khan", "Gita Mishra",
           "Harish Panda", "Ipsita Jena", "Jagdish Sethi", "Kavita Swain", "Lalit Dash", "Meera Nanda", "Nitin Rath"]
# (category, bays, price per slot, slot minutes, overtime block, overtime charge, free minutes)
CAR = ("FOUR_WHEELER", 12, "60", 60, 30, "100", 0)
BIKE = ("TWO_WHEELER", 30, "20", 60, 1, "3", 10)
TRUCK = ("HEAVY", 6, "250", 120, 60, "300", 0)
AUTO = ("THREE_WHEELER", 8, "30", 60, 15, "25", 5)
PARKINGS = [
    dict(owner=0, name="Saheed Nagar Smart Parking", area="Saheed Nagar", city="Bhubaneswar", pin="751007", lat="20.2961", lng="85.8245",
         verified=True, amen=["has_cctv", "has_security_guard", "is_gated", "has_lighting"], cancel=(True, 60, "10"), types=[CAR, BIKE]),
    dict(owner=0, name="Jaydev Vihar Covered Lot", area="Jaydev Vihar", city="Bhubaneswar", pin="751013", lat="20.2987", lng="85.8187",
         verified=True, amen=["is_covered", "has_cctv", "has_ev_charging", "has_washroom"], hours=(7, 23), cancel=(True, 120, "0"),
         types=[("FOUR_WHEELER", 20, "80", 60, 30, "120", 0), ("TWO_WHEELER", 15, "25", 60, 1, "4", 5)]),
    dict(owner=0, name="Airport Road Truck Yard", area="Airport Road", city="Bhubaneswar", pin="751020", lat="20.2444", lng="85.8178",
         verified=False, amen=["has_lighting", "is_gated"], cancel=(False, 60, "0"), types=[TRUCK, ("FOUR_WHEELER", 10, "50", 60, 30, "80", 0)]),
    dict(owner=1, name="Buxi Bazaar Parking", area="Buxi Bazaar", city="Cuttack", pin="753001", lat="20.4625", lng="85.8830",
         verified=True, amen=["has_cctv", "has_lighting"], cancel=(True, 90, "20"), types=[("FOUR_WHEELER", 15, "50", 60, 30, "80", 0), BIKE]),
    dict(owner=1, name="Cuttack Station Park", area="Railway Station", city="Cuttack", pin="753003", lat="20.4687", lng="85.8791",
         verified=False, amen=["has_security_guard", "has_washroom"], cancel=(False, 60, "0"), types=[CAR, BIKE, AUTO]),
    dict(owner=2, name="Puri Beach Parking", area="Swargadwar", city="Puri", pin="752001", lat="19.7983", lng="85.8249",
         verified=True, amen=["has_cctv", "is_gated", "has_washroom", "wheelchair_accessible"], cancel=(True, 180, "10"),
         types=[("FOUR_WHEELER", 40, "70", 60, 30, "100", 0), ("HEAVY", 8, "300", 120, 60, "350", 0)]),
    dict(owner=2, name="Puri Temple View Lot", area="Grand Road", city="Puri", pin="752002", lat="19.8049", lng="85.8180",
         status="PENDING", amen=["has_lighting"], cancel=(False, 60, "0"), types=[CAR, BIKE]),
    dict(owner=1, name="Old Town Courtyard", area="Old Town", city="Cuttack", pin="753002", lat="20.4580", lng="85.8760",
         status="REJECTED", reason="The photos are too dark to see the bays. Please upload clearer pictures.", amen=[],
         cancel=(False, 60, "0"), types=[BIKE]),
]
BRANDS = {"FOUR_WHEELER": ["Tata Nexon", "Maruti Swift", "Hyundai i20", "Mahindra XUV300", "Honda City"],
          "TWO_WHEELER": ["Honda Activa", "TVS Jupiter", "Royal Enfield Classic", "Bajaj Pulsar"],
          "HEAVY": ["Tata 407", "Ashok Leyland Dost", "Eicher Pro"], "THREE_WHEELER": ["Bajaj RE", "Piaggio Ape"]}
COMMENTS = ["Easy to find and the guard was helpful.", "Clean and well lit. Will book again.", "Entry was quick with the code.",
            "A bit tight for a bigger car but fine.", "Great value for the area.", "Safe place, CCTV everywhere.",
            "Staff were polite. Exit took a few minutes.", "Convenient, right next to where I needed to be."]
REPLIES = ["Thank you for parking with us!", "Glad it worked out. See you next time.", "Thanks for the feedback, we will keep improving."]
HOURS = list(range(6, 23))
HOUR_WEIGHTS = [2, 4, 8, 10, 8, 5, 4, 4, 5, 6, 8, 9, 7, 4, 2, 1, 1]


class Command(BaseCommand):
    help = "Create realistic demo owners, drivers, parkings, bookings, payments, refunds, reviews and payouts."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete existing demo data first, then rebuild it.")
        parser.add_argument("--clear", action="store_true", help="Delete demo data and stop.")
        parser.add_argument("--force", action="store_true", help="Allow running with DEBUG off (use a throwaway database).")
        parser.add_argument("--no-photos", action="store_true", help="Skip generating parking photos.")
        parser.add_argument("--months", type=int, default=3, help="How many months of history to create (default 3).")
        parser.add_argument("--per-parking", type=int, default=40, help="Past bookings per live parking (default 40).")
        parser.add_argument("--seed", type=int, default=7, help="Random seed, for repeatable data.")

    # ------------------------------------------------------------------ entry
    def handle(self, *args, **o):
        if not settings.DEBUG and not o["force"]:
            raise CommandError("Refusing to create demo data while DEBUG is off. Use --force only on a throwaway database.")
        if o["reset"] or o["clear"]:
            self.clear()
            self.stdout.write("Existing demo data removed.")
            if o["clear"]:
                return
        elif User.objects.filter(email__endswith="@" + DOMAIN).exists():
            raise CommandError("Demo data already exists. Run with --reset to rebuild it, or --clear to remove it.")
        self.rng = random.Random(o["seed"])
        self.now = timezone.now()
        self.o = o
        self.counter = 0
        self.occupancy = defaultdict(list)   # vehicle type -> [(start, end)] used to keep the bays within capacity
        self.plate_use = defaultdict(list)
        self.plates = set()
        with transaction.atomic():
            self.build()
        self.report()

    # ------------------------------------------------------------------ helpers
    def n(self):
        self.counter += 1
        return self.counter

    def local(self, day, hour, minute=0):
        return timezone.make_aware(datetime.combine(day, time(hour, minute)), timezone.get_current_timezone())

    def new_plate(self):
        while True:
            letters = "ABCDEFGHJKLMNPRSTUVWXYZ"
            plate = f"OD{self.rng.choice(['02', '05', '07', '33'])}{self.rng.choice(letters)}{self.rng.choice(letters)}{self.rng.randint(1000, 9999)}"
            if plate not in self.plates and not DriverVehicle.objects.filter(plate_number=plate).exists():
                self.plates.add(plate)
                return plate

    def vehicle_for(self, driver, category):
        v = DriverVehicle.objects.filter(user=driver, category=category).first()
        if v is None:
            brand, _, model = self.rng.choice(BRANDS[category]).partition(" ")
            v = DriverVehicle.objects.create(user=driver, category=category, plate_number=self.new_plate(), brand=brand, model=model or brand,
                                             color=self.rng.choice(["White", "Black", "Silver", "Red", "Blue"]))
        return v

    def photo(self, parking, idx):
        from PIL import Image, ImageDraw

        base = [(52, 55, 201), (14, 159, 146), (21, 25, 54), (95, 102, 245)][idx % 4]
        img = Image.new("RGB", (900, 560), base)
        d = ImageDraw.Draw(img)
        for i in range(8):  # painted bay lines
            x = 60 + i * 100
            d.line([(x, 250), (x, 520)], fill=(255, 200, 69), width=5)
        d.line([(60, 250), (860, 250)], fill=(255, 200, 69), width=5)
        d.text((40, 40), parking.name, fill="white")
        d.text((40, 70), f"{parking.area}, {parking.city}", fill=(220, 224, 250))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=82)
        ParkingPhoto.objects.create(parking=parking, image=ContentFile(buf.getvalue(), name=f"demo-{parking.pk}-{idx}.jpg"), is_cover=idx == 0, order=idx)

    # ------------------------------------------------------------------ build
    def build(self):
        cfg = SiteSettings.load()
        cfg.platform_fee_percent, cfg.verification_required = Decimal("5"), True
        cfg.save()
        self.cfg = cfg
        self.admin = User.objects.filter(role=User.Role.SUPERADMIN, is_active=True).first() or User.objects.create_user(
            f"admin@{DOMAIN}", PASSWORD, full_name="Demo Superadmin", role=User.Role.SUPERADMIN, is_staff=True, is_superuser=True)
        days = self.o["months"] * 30
        self.owners = []
        for i, (slug, name, business, phone) in enumerate(OWNERS):
            u = User.objects.create_user(f"{slug}@{DOMAIN}", PASSWORD, full_name=name, role=User.Role.OWNER, phone=phone)
            OwnerProfile.objects.create(user=u, business_name=business, about=f"{business} runs safe, well-lit parking across Odisha.", address="Odisha, India")
            User.objects.filter(pk=u.pk).update(date_joined=self.now - timedelta(days=days + 20 - i * 3))
            self.owners.append(u)
        self.drivers = []
        for i, name in enumerate(DRIVERS):
            u = User.objects.create_user(f"driver{i + 1}@{DOMAIN}", PASSWORD, full_name=name, role=User.Role.DRIVER, phone=f"98200000{i:02d}")
            User.objects.filter(pk=u.pk).update(date_joined=self.now - timedelta(days=int(self.rng.triangular(1, days + 10, days // 2))))
            self.vehicle_for(u, "FOUR_WHEELER")
            self.vehicle_for(u, "TWO_WHEELER")
            self.drivers.append(u)

        self.parkings = []
        for i, spec in enumerate(PARKINGS):
            self.parkings.append(self.make_parking(spec, i, days))
        live = [p for p in self.parkings if p.status == Parking.Status.APPROVED]
        for parking in live:
            self.history(parking, days)
            self.upcoming(parking)
        self.special_cases(live)
        for parking in live:
            recompute_parking_rating(parking.pk)
        self.payouts()
        self.tidy_notifications()

    def make_parking(self, spec, idx, days):
        allow, window, charge = spec["cancel"]
        opening = spec.get("hours")
        p = Parking.objects.create(
            owner=self.owners[spec["owner"]], name=spec["name"], description=f"Secure parking in {spec['area']}, {spec['city']}. "
            "Easy entry with a booking code, staffed gate and clear bay markings.", address_line=f"{10 + idx * 7}, Main Road", area=spec["area"],
            city=spec["city"], state="Odisha", pincode=spec["pin"], google_maps_url=f"https://www.google.com/maps/@{spec['lat']},{spec['lng']},17z",
            latitude=Decimal(spec["lat"]), longitude=Decimal(spec["lng"]), contact_phone="9437000000" if idx % 2 else "",
            is_24x7=opening is None, opening_time=time(opening[0]) if opening else None, closing_time=time(opening[1]) if opening else None,
            allow_cancellation=allow, cancel_window_minutes=window, cancellation_charge_percent=Decimal(charge),
            grace_minutes=15, rules_text="Park inside the lines. Keep the code ready at entry.", status=Parking.Status.DRAFT,
            **{a: True for a in spec["amen"]})
        for cat, bays, price, slot, block, charge_ot, free in spec["types"]:
            ParkingVehicleType.objects.create(parking=p, category=cat, capacity=bays, price_per_slot=price, slot_minutes=slot, min_slots=1,
                                              max_slots=12, overtime_block_minutes=block, overtime_charge=charge_ot, overtime_free_minutes=free)
        if not self.o["no_photos"]:
            for k in range(2 if spec.get("status") != "REJECTED" else 1):
                self.photo(p, k)
        Parking.objects.filter(pk=p.pk).update(created_at=self.now - timedelta(days=days + 10 - idx))
        status = spec.get("status", "APPROVED")
        if status == "PENDING":
            Parking.objects.filter(pk=p.pk).update(status=Parking.Status.PENDING, submitted_at=self.now - timedelta(hours=5))
        elif status == "REJECTED":
            Parking.objects.filter(pk=p.pk).update(status=Parking.Status.PENDING, submitted_at=self.now - timedelta(days=2))
            p.refresh_from_db()
            parking_services.reject(p, self.admin, spec["reason"])
        else:
            Parking.objects.filter(pk=p.pk).update(status=Parking.Status.APPROVED, submitted_at=self.now - timedelta(days=days + 5))
            p.refresh_from_db()
            if spec.get("verified"):
                parking_services.approve(p, self.admin)
        p.refresh_from_db()
        return p

    # ------------------------------------------------------------------ bookings
    def book(self, vt, driver, start, slots, backdate_to=None):
        """Create a booking the same way a real one is made: hold, order, signed payment, confirmation."""
        end = start + timedelta(minutes=vt.slot_minutes * slots)
        if peak_concurrency(self.occupancy[vt.pk], start, end) >= vt.capacity or not within_opening_hours(vt.parking, start, end):
            return None
        vehicle = self.vehicle_for(driver, vt.category)
        if peak_concurrency(self.plate_use[vehicle.plate_number], start, end) >= 1:
            return None
        base = money(vt.total_price(slots))
        fee, owner_amount = split_amount(base, self.cfg.platform_fee_percent)
        parking, n = vt.parking, self.n()
        booking = Booking.objects.create(
            driver=driver, parking=parking, vehicle_type=vt, start_at=start, end_at=end, slots=slots, vehicle_category=vt.category,
            vehicle_number_plate=vehicle.plate_number, vehicle_brand_model=f"{vehicle.brand} {vehicle.model}", price_per_slot=vt.price_per_slot,
            slot_minutes=vt.slot_minutes, overtime_block_minutes=vt.overtime_block_minutes, overtime_charge=vt.overtime_charge,
            overtime_free_minutes=vt.overtime_free_minutes, grace_minutes=parking.grace_minutes, allow_cancellation=parking.allow_cancellation,
            cancel_window_minutes=parking.cancel_window_minutes,
            cancel_charge_percent=parking.cancellation_charge_percent if parking.allow_cancellation else Decimal("0"),
            base_amount=base, platform_fee_percent=self.cfg.platform_fee_percent, platform_fee_amount=fee, owner_amount=owner_amount,
            status=S.PENDING_PAYMENT, payment_hold_expires_at=self.now + timedelta(minutes=10))
        payment = Payment.objects.create(booking=booking, kind=Payment.Kind.BOOKING, razorpay_order_id=f"order_demo_{n}", amount=base,
                                         platform_fee_amount=fee, owner_amount=owner_amount)
        pay.settle_payment(payment.razorpay_order_id, f"pay_demo_{n}", "demo-signature")
        booking.refresh_from_db()
        placed = min(backdate_to or (start - timedelta(hours=self.rng.randint(2, 72))), self.now - timedelta(minutes=1))
        Booking.objects.filter(pk=booking.pk).update(created_at=placed)
        Payment.objects.filter(pk=payment.pk).update(created_at=placed, paid_at=placed)
        OwnerLedger.objects.filter(payment=payment).update(created_at=placed)
        self.occupancy[vt.pk].append((start, end))
        self.plate_use[vehicle.plate_number].append((start, end))
        return booking, payment, placed

    def pick(self, parking):
        types = list(parking.vehicle_types.all())
        weights = [6 if t.category == "FOUR_WHEELER" else 4 if t.category == "TWO_WHEELER" else 1 for t in types]
        return self.rng.choices(types, weights)[0]

    def history(self, parking, days):
        attempts, made = 0, 0
        while made < self.o["per_parking"] and attempts < self.o["per_parking"] * 6:
            attempts += 1
            vt = self.pick(parking)
            day = (self.now - timedelta(days=max(1, int(self.rng.triangular(1, days, 4))))).date()
            start = self.local(day, self.rng.choices(HOURS, HOUR_WEIGHTS)[0], self.rng.choice([0, 30]))
            slots = self.rng.randint(1, 4)
            roll = self.rng.random()
            outcome = ("CANCELLED" if parking.allow_cancellation else "COMPLETED") if roll < 0.10 else "NO_SHOW" if roll < 0.17 else "OVERTIME" if roll < 0.28 else "COMPLETED"
            made_one = self.book(vt, self.rng.choice(self.drivers), start, slots)
            if made_one is None:
                continue
            made += 1
            self.finish(*made_one, outcome)

    def finish(self, booking, payment, placed, outcome):
        rng = self.rng
        driver = booking.driver
        if outcome == "CANCELLED":
            charge = money(payment.amount * booking.cancel_charge_percent / Decimal("100"))
            when = min(booking.start_at - timedelta(hours=rng.randint(3, 40)), self.now - timedelta(minutes=1))
            refund = Refund.objects.create(booking=booking, payment=payment, requested_by=driver, amount=payment.amount - charge, cancellation_charge=charge,
                                           status=Refund.Status.APPROVED, reason=rng.choice(["Plans changed", "Meeting cancelled", "Found a closer spot"]),
                                           razorpay_refund_id=f"rfnd_demo_{self.n()}")
            pay.finalize_refund(refund.pk)
            Refund.objects.filter(pk=refund.pk).update(created_at=when, processed_at=when + timedelta(hours=2))
            OwnerLedger.objects.filter(refund=refund).update(created_at=when + timedelta(hours=2))
            return
        if outcome == "NO_SHOW":
            Booking.objects.filter(pk=booking.pk).update(status=S.NO_SHOW)
            apply_points(driver, PointLog.Reason.NO_SHOW, note=f"Booking #{booking.pk}")
            PointLog.objects.filter(user=driver, note=f"Booking #{booking.pk}").update(created_at=booking.start_at + timedelta(minutes=booking.grace_minutes + 1))
            return
        late = rng.choice([12, 25, 40, 55]) if outcome == "OVERTIME" else 0
        out_at = booking.end_at + timedelta(minutes=late)
        extra, amount = lifecycle.compute_overtime(booking, out_at)
        Booking.objects.filter(pk=booking.pk).update(
            status=S.COMPLETED, checked_in_at=booking.start_at + timedelta(minutes=rng.randint(-5, 10)), checked_out_at=out_at,
            checkout_requested_at=out_at, overtime_minutes=extra, overtime_amount=amount)
        if amount > 0:
            booking.refresh_from_db()
            n = self.n()
            fee, owner_amount = split_amount(amount, booking.platform_fee_percent)
            ot = Payment.objects.create(booking=booking, kind=Payment.Kind.OVERTIME, razorpay_order_id=f"order_demo_{n}", amount=amount,
                                        platform_fee_amount=fee, owner_amount=owner_amount)
            pay.settle_payment(ot.razorpay_order_id, f"pay_demo_{n}", "demo-signature")
            Payment.objects.filter(pk=ot.pk).update(created_at=out_at, paid_at=out_at)
            OwnerLedger.objects.filter(payment=ot).update(created_at=out_at)
            self.occupancy[booking.vehicle_type_id].append((booking.end_at, out_at))
        satisfied = rng.random() < 0.9
        DriverVerdict.objects.create(booking=booking, owner=booking.parking.owner, driver=driver, satisfied=satisfied,
                                     comment="" if satisfied else rng.choice(["Blocked the exit lane", "Left late without telling us"]))
        DriverVerdict.objects.filter(booking=booking).update(created_at=out_at)
        apply_points(driver, PointLog.Reason.SATISFIED if satisfied else PointLog.Reason.UNSATISFIED, note=f"Booking #{booking.pk}")
        PointLog.objects.filter(user=driver, note=f"Booking #{booking.pk}").update(created_at=out_at)
        if rng.random() < 0.55:
            rating = rng.choices([5, 4, 3, 2, 1], [48, 30, 14, 6, 2])[0]
            review = ParkingReview.objects.create(booking=booking, driver=driver, parking=booking.parking, owner=booking.parking.owner, rating=rating,
                                                  comment=rng.choice(COMMENTS) if rating >= 3 else "Not as described. Entry was slow.")
            when = min(out_at + timedelta(hours=rng.randint(1, 30)), self.now - timedelta(minutes=1))
            fields = {"created_at": when}
            if rng.random() < 0.4:
                fields.update(owner_reply=rng.choice(REPLIES), replied_at=min(when + timedelta(hours=3), self.now))
            ParkingReview.objects.filter(pk=review.pk).update(**fields)

    def upcoming(self, parking):
        today = timezone.localdate()
        for _ in range(2):  # confirmed bookings in the next few days
            vt = self.pick(parking)
            start = self.local(today + timedelta(days=self.rng.randint(1, 3)), self.rng.choice([9, 10, 14, 17, 18]))
            self.book(vt, self.rng.choice(self.drivers), start, self.rng.randint(1, 3), backdate_to=self.now - timedelta(hours=self.rng.randint(1, 20)))
        vt = self.pick(parking)  # someone arriving soon, so "Arriving today" has content
        soon = (self.now + timedelta(minutes=self.rng.randint(15, 90))).replace(second=0, microsecond=0)
        self.book(vt, self.rng.choice(self.drivers), soon, 2, backdate_to=self.now - timedelta(hours=3))

    def special_cases(self, live):
        first = next(p for p in live if p.owner == self.owners[0])
        vt = first.vehicle_types.get(category="FOUR_WHEELER")
        start = (self.now - timedelta(hours=2, minutes=30)).replace(second=0, microsecond=0)
        made = self.book(vt, self.drivers[0], start, 2, backdate_to=start - timedelta(hours=5))
        if made:  # parked and past its time: shows under "Overstaying"
            Booking.objects.filter(pk=made[0].pk).update(status=S.CHECKED_IN, checked_in_at=start)
        start = self.local(timezone.localdate() + timedelta(days=2), 11)
        made = self.book(vt, self.drivers[1], start, 2, backdate_to=self.now - timedelta(hours=6))
        if made and first.allow_cancellation:  # a refund waiting for the owner's decision
            booking, payment, _ = made
            charge = money(payment.amount * booking.cancel_charge_percent / Decimal("100"))
            Refund.objects.create(booking=booking, payment=payment, requested_by=booking.driver, amount=payment.amount - charge,
                                  cancellation_charge=charge, reason="Trip postponed")
            Booking.objects.filter(pk=booking.pk).update(status=S.REFUND_REQUESTED)

    def payouts(self):
        for owner in self.owners:
            balance = pay.owner_balance(owner)
            if balance > 0:
                entry = pay.record_payout(owner, money(balance * Decimal("0.6")), f"UPI-DEMO-{self.n():04d}", "Demo payout", self.admin)
                OwnerLedger.objects.filter(pk=entry.pk).update(created_at=self.now - timedelta(days=10))

    def tidy_notifications(self):
        """Demo users start with most alerts read, so the bell shows a handful of recent, unread ones."""
        for user in User.objects.filter(email__endswith="@" + DOMAIN):
            Notification.objects.filter(user=user).update(is_read=True)
            latest = list(Notification.objects.filter(user=user).order_by("-id").values_list("pk", flat=True)[:3])
            Notification.objects.filter(pk__in=latest).update(is_read=False)

    # ------------------------------------------------------------------ reset + report
    def clear(self):
        demo = User.objects.filter(email__endswith="@" + DOMAIN)
        with transaction.atomic():
            Notification.objects.filter(user__in=demo).delete()
            PointLog.objects.filter(user__in=demo).delete()
            OwnerLedger.objects.filter(owner__in=demo).delete()
            Refund.objects.filter(booking__driver__in=demo).delete()
            ParkingReview.objects.filter(driver__in=demo).delete()
            DriverVerdict.objects.filter(driver__in=demo).delete()
            Payment.objects.filter(booking__driver__in=demo).delete()
            Booking.objects.filter(driver__in=demo).delete()
            for photo in ParkingPhoto.objects.filter(parking__owner__in=demo):
                photo.image.delete(save=False)
            ParkingPhoto.objects.filter(parking__owner__in=demo).delete()
            ParkingVehicleType.objects.filter(parking__owner__in=demo).delete()
            Parking.objects.filter(owner__in=demo).delete()
            DriverVehicle.objects.filter(user__in=demo).delete()
            OwnerProfile.objects.filter(user__in=demo).delete()
            demo.delete()

    def report(self):
        out = self.stdout.write
        out(self.style.SUCCESS("Demo data created."))
        out(f"  Parkings: {Parking.objects.filter(owner__in=self.owners).count()}   Bookings: {Booking.objects.filter(driver__in=self.drivers).count()}   "
            f"Payments: {Payment.objects.filter(booking__driver__in=self.drivers).count()}   Reviews: {ParkingReview.objects.filter(driver__in=self.drivers).count()}")
        out(f"  Platform fee set to {self.cfg.platform_fee_percent}%. Verification is required.")
        out("  Log in (password for all demo accounts: " + PASSWORD + "):")
        out(f"    Owner      owner1@{DOMAIN}  (also owner2, owner3)")
        out(f"    Driver     driver1@{DOMAIN}  (driver1 to driver{len(DRIVERS)})")
        if self.admin.email.endswith(DOMAIN):
            out(f"    Superadmin {self.admin.email}")
        else:
            out(f"    Superadmin {self.admin.email}  (your existing account; its password is unchanged)")
