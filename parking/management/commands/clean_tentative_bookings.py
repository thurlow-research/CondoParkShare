"""
parking management command: clean_tentative_bookings

Marks expired tentative bookings as cancelled_admin.

Run at :00 each hour (alongside notify_bookings --event starts,completions):

    0 * * * *  python manage.py clean_tentative_bookings

See TECHNICAL-DESIGN.md §6 "Expired tentative hold cleanup".
"""

from django.core.management.base import BaseCommand
from django.utils.timezone import now

from parking.models import Booking


class Command(BaseCommand):
    help = "Cancel tentative bookings whose 5-minute hold has expired."

    def handle(self, *args, **options):
        updated = Booking.objects.filter(
            status="tentative",
            tentative_expires_at__lt=now(),
        ).update(status="cancelled_admin")

        if options["verbosity"] >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f"clean_tentative_bookings: {updated} expired hold(s) cancelled."
                )
            )
