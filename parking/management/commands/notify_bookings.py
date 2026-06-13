"""
parking management command: notify_bookings

Sends booking lifecycle notifications and updates booking statuses.

Run via cron:

    0  * * * *  python manage.py notify_bookings --event starts,completions,tentative_cleanup
    30 * * * *  python manage.py notify_bookings --event warning_30
    45 * * * *  python manage.py notify_bookings --event warning_15

See TECHNICAL-DESIGN.md §7 "Management command: notify_bookings".
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils.timezone import now

from notifications.dispatch import notify
from parking.models import Booking


class Command(BaseCommand):
    help = "Send booking notifications and update booking statuses."

    def add_arguments(self, parser):
        parser.add_argument(
            "--event",
            type=str,
            help="Comma-separated list of events to process: "
            "starts, completions, tentative_cleanup, warning_30, warning_15",
        )

    def handle(self, *args, **options):
        events = [
            e.strip() for e in (options.get("event") or "").split(",") if e.strip()
        ]
        now_dt = now()

        # ------------------------------------------------------------------
        # tentative_cleanup — also runs alongside starts/completions at :00
        # ------------------------------------------------------------------
        if (
            "tentative_cleanup" in events
            or "starts" in events
            or "completions" in events
        ):
            expired = Booking.objects.filter(
                status="tentative",
                tentative_expires_at__lt=now_dt,
            ).update(status="cancelled_admin")
            if options["verbosity"] >= 2:
                self.stdout.write(
                    f"tentative_cleanup: {expired} expired hold(s) cancelled."
                )

        # ------------------------------------------------------------------
        # starts — confirmed bookings whose start falls in (now-1h, now]
        # ------------------------------------------------------------------
        if "starts" in events:
            started = list(
                Booking.objects.filter(
                    time_range__startswith__gt=now_dt - timedelta(hours=1),
                    time_range__startswith__lte=now_dt,
                    status="confirmed",
                ).select_related("spot__owner", "borrower")
            )
            pks = [b.pk for b in started]
            if pks:
                Booking.objects.filter(pk__in=pks).update(status="active")
                for b in started:
                    notify("booking_starts", b)
            if options["verbosity"] >= 2:
                self.stdout.write(f"starts: {len(pks)} booking(s) activated.")

        # ------------------------------------------------------------------
        # completions — active bookings whose end falls in (now-1h, now]
        # ------------------------------------------------------------------
        if "completions" in events:
            completed = list(
                Booking.objects.filter(
                    time_range__endswith__gt=now_dt - timedelta(hours=1),
                    time_range__endswith__lte=now_dt,
                    status="active",
                ).select_related("spot__owner", "borrower")
            )
            pks = [b.pk for b in completed]
            if pks:
                Booking.objects.filter(pk__in=pks).update(status="completed")
                for b in completed:
                    owner = b.spot.owner
                    if owner:
                        owner.last_booking_at = b.time_range.upper
                        owner.save(update_fields=["last_booking_at"])
                    notify("booking_completed", b)
            if options["verbosity"] >= 2:
                self.stdout.write(f"completions: {len(pks)} booking(s) completed.")

        # ------------------------------------------------------------------
        # warning_30 — active bookings ending in ~30 minutes (±5 min window)
        # ------------------------------------------------------------------
        if "warning_30" in events:
            target = now_dt + timedelta(minutes=30)
            warnings = list(
                Booking.objects.filter(
                    time_range__endswith__gt=target - timedelta(minutes=5),
                    time_range__endswith__lte=target + timedelta(minutes=5),
                    status="active",
                ).select_related("spot__owner", "borrower")
            )
            for b in warnings:
                notify("warning_30", b)
            if options["verbosity"] >= 2:
                self.stdout.write(f"warning_30: {len(warnings)} warning(s) sent.")

        # ------------------------------------------------------------------
        # warning_15 — active bookings ending in ~15 minutes (±5 min window)
        # ------------------------------------------------------------------
        if "warning_15" in events:
            target = now_dt + timedelta(minutes=15)
            warnings = list(
                Booking.objects.filter(
                    time_range__endswith__gt=target - timedelta(minutes=5),
                    time_range__endswith__lte=target + timedelta(minutes=5),
                    status="active",
                ).select_related("spot__owner", "borrower")
            )
            for b in warnings:
                notify("warning_15", b)
            if options["verbosity"] >= 2:
                self.stdout.write(f"warning_15: {len(warnings)} warning(s) sent.")

        if options["verbosity"] >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f'notify_bookings: processed events={events or "(none)"}.'
                )
            )
