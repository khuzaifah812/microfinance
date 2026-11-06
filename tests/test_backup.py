import io
import json
import tempfile
from decimal import Decimal
from pathlib import Path

from cryptography.fernet import Fernet
from django.core.management import call_command
from django.test import override_settings

from accounts_core.audit import verify_chain
from maintenance import services

from .base import SaccoTestCase


class BackupRoundTrip(SaccoTestCase):
    def test_backup_is_encrypted_verified_and_audit_chain_survives_json(self):
        from savings import services as sv
        sv.deposit(self.savings, Decimal(10000), self.cashier, "cash")
        with tempfile.TemporaryDirectory() as d, override_settings(BACKUP_DIR=Path(d), BACKUP_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            rec = services.create_backup(self.admin)
            self.assertTrue(rec.verified_ok)
            self.assertTrue(rec.encrypted)
            raw_file = (Path(d) / rec.filename).read_bytes()
            self.assertNotIn(b"Test Member", raw_file)            # ciphertext, not readable data
            data = json.loads(services.read_backup_json(rec))
        # replay the exported audit rows through JSON the way a restore does and verify the chain still holds
        from django.core import serializers
        from accounts_core.models import AuditLog
        text = serializers.serialize("json", AuditLog.objects.order_by("pk"))
        objs = [o.object for o in serializers.deserialize("json", text)]
        self.assertEqual(len(objs), AuditLog.objects.count())
        ok, _, problems = verify_chain()
        self.assertTrue(ok, problems)
        for o, orig in zip(objs, AuditLog.objects.order_by("pk")):
            from accounts_core.audit import _row
            self.assertEqual(_row(o), _row(orig))

    def test_tampered_backup_file_fails_verification(self):
        with tempfile.TemporaryDirectory() as d, override_settings(BACKUP_DIR=Path(d), BACKUP_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            rec = services.create_backup(self.admin)
            p = Path(d) / rec.filename
            b = bytearray(p.read_bytes())
            b[len(b) // 2] ^= 0xFF
            p.write_bytes(bytes(b))
            ok, err = services.verify_backup(rec, self.admin)
            self.assertFalse(ok)
