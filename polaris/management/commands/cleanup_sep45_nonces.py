from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from polaris.models import WebAuthNonce


class Command(BaseCommand):
    help = "Delete expired SEP-45 nonces (expires_at < now() - 1 day)."

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=1)
        deleted, _ = WebAuthNonce.objects.filter(expires_at__lt=cutoff).delete()
        self.stdout.write(f"deleted {deleted} expired nonces")
