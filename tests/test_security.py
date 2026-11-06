from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts_core.audit import audit, verify_chain
from accounts_core.models import AuditLog, LoginHistory
from accounts_core.rbac import ROLE_PERMS, can
from core.templatetags.sacco import mask_nin, mask_phone

from .base import SaccoTestCase, mk_user


def login_client(user):
    c = Client(HTTP_HOST="localhost")
    c.force_login(user)
    if user.profile.needs_2fa:
        d = TOTPDevice.objects.create(user=user, name="t", confirmed=True)
        s = c.session
        s["otp_device_id"] = d.persistent_id
        s.save()
    return c


class RolePermissions(SaccoTestCase):
    def test_permission_matrix(self):
        self.assertTrue(can(self.admin, "users.manage"))
        self.assertFalse(can(self.cashier, "users.manage"))
        self.assertFalse(can(self.cashier, "accounting.journal"))
        self.assertTrue(can(self.accountant, "accounting.journal"))
        self.assertFalse(can(self.auditor, "savings.transact"))
        self.assertFalse(can(self.officer, "approvals.decide"))
        self.assertTrue(can(self.manager, "approvals.decide"))
        self.assertTrue(can(self.committee, "loans.committee"))
        self.assertFalse(can(self.officer, "loans.committee"))

    def test_only_privileged_roles_see_full_pii(self):
        self.assertFalse(can(self.cashier, "members.view_pii"))
        self.assertFalse(can(self.auditor, "members.view_pii"))
        self.assertTrue(can(self.admin, "members.view_pii"))

    def test_views_enforce_permissions(self):
        c = login_client(self.cashier)
        self.assertEqual(c.get("/users/").status_code, 403)
        self.assertEqual(c.get("/maintenance/").status_code, 403)
        self.assertEqual(c.get("/accounting/journals/new/").status_code, 403)
        self.assertEqual(c.get("/cashier/").status_code, 200)

    def test_anonymous_is_redirected_to_login(self):
        r = Client(HTTP_HOST="localhost").get("/members/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r["Location"])

    def test_admin_needs_2fa_before_anything_else(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.admin)
        r = c.get("/members/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("2fa", r["Location"])

    def test_cashier_not_forced_into_2fa(self):
        self.assertFalse(self.cashier.profile.needs_2fa)


class Masking(SaccoTestCase):
    def test_phone_and_nin_masked(self):
        self.assertEqual(mask_phone("0772123445"), "07** **** 45")
        self.assertNotIn("CM12345678ABCD", mask_nin("CM12345678ABCD"))
        self.assertEqual(mask_phone(""), "")

    def test_cashier_page_hides_full_phone(self):
        c = login_client(self.cashier)
        html = c.get(f"/members/{self.member.pk}/").content.decode()
        self.assertNotIn(self.member.phone, html)


class AuditLogIntegrity(SaccoTestCase):
    def test_existing_row_cannot_be_modified_or_deleted(self):
        audit("t.one", "security", detail="x", user=self.admin)
        row = AuditLog.objects.order_by("-id").first()
        row.detail = "tampered"
        with self.assertRaises(Exception):
            row.save()
        with self.assertRaises(Exception):
            row.delete()
        with self.assertRaises(Exception):
            AuditLog.objects.filter(pk=row.pk).update(detail="x")

    def test_chain_detects_tampering_with_raw_write(self):
        for i in range(3):
            audit(f"t.{i}", "security", detail=str(i), user=self.admin)
        ok, checked, problems = verify_chain()
        self.assertTrue(ok, problems)
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute("UPDATE accounts_core_auditlog SET detail = %s WHERE id = (SELECT MAX(id) - 1 FROM accounts_core_auditlog)", ["edited"])
        ok, _, problems = verify_chain()
        self.assertFalse(ok)
        self.assertTrue(problems)


class LockoutAndSessions(SaccoTestCase):
    def setUp(self):
        cache.clear()

    def test_account_locks_after_five_failures(self):
        c = Client(HTTP_HOST="localhost")
        for _ in range(5):
            c.post("/login/", {"username": self.cashier.username, "password": "wrong-password"})
        self.cashier.profile.refresh_from_db()
        self.assertTrue(self.cashier.profile.is_locked)
        r = c.post("/login/", {"username": self.cashier.username, "password": "Test@12345!x"})
        self.assertNotEqual(r.status_code, 302)                 # correct password still refused while locked
        self.assertGreaterEqual(LoginHistory.objects.filter(success=False).count(), 5)

    def test_successful_login_resets_counter_and_is_logged(self):
        c = Client(HTTP_HOST="localhost")
        c.post("/login/", {"username": self.cashier.username, "password": "bad"})
        r = c.post("/login/", {"username": self.cashier.username, "password": "Test@12345!x"})
        self.assertEqual(r.status_code, 302)
        self.cashier.profile.refresh_from_db()
        self.assertEqual(self.cashier.profile.failed_attempts, 0)
        self.assertTrue(LoginHistory.objects.filter(success=True).exists())

    def test_idle_session_is_ended(self):
        c = login_client(self.cashier)
        s = c.session
        s["last_activity"] = 1          # long ago
        s.save()
        r = c.get("/members/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r["Location"])

    def test_security_headers_present(self):
        r = login_client(self.cashier).get("/cashier/")
        self.assertIn("Content-Security-Policy", r)
        self.assertIn("no-store", r["Cache-Control"])


from django.core import mail
from django.test import override_settings
from django.utils import timezone


@override_settings(EMAIL_VERIFICATION=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailVerification(SaccoTestCase):
    def client_for(self, user):
        c = Client(HTTP_HOST="localhost")
        c.force_login(user)
        return c

    def code_from_outbox(self):
        import re
        return re.search(r"\n\s+(\d{6})\n", mail.outbox[-1].body).group(1)

    def test_unverified_user_is_sent_to_verify_page_and_code_is_emailed(self):
        c = self.client_for(self.cashier)
        r = c.get("/members/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/account/verify-email/", r["Location"])
        r = c.get("/account/verify-email/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.cashier.email])
        self.assertRegex(mail.outbox[0].subject, r"^\d{6} is your")
        self.assertNotContains(r, self.cashier.email)          # address is masked on the page

    def test_correct_code_unlocks_the_system(self):
        c = self.client_for(self.cashier)
        c.get("/account/verify-email/")
        r = c.post("/account/verify-email/", {"action": "verify", "code": self.code_from_outbox()})
        self.assertEqual(r.status_code, 302)
        self.cashier.profile.refresh_from_db()
        self.assertTrue(self.cashier.profile.email_verified)
        self.assertEqual(c.get("/cashier/").status_code, 200)

    def test_wrong_code_rejected_and_five_tries_burn_it(self):
        c = self.client_for(self.cashier)
        c.get("/account/verify-email/")
        good = self.code_from_outbox()
        bad = "000000" if good != "000000" else "111111"
        for _ in range(5):
            r = c.post("/account/verify-email/", {"action": "verify", "code": bad})
            self.assertEqual(r.status_code, 200)
        r = c.post("/account/verify-email/", {"action": "verify", "code": good})
        self.assertEqual(r.status_code, 200)                    # code was burned, even the right one fails now
        self.cashier.profile.refresh_from_db()
        self.assertFalse(self.cashier.profile.email_verified)

    def test_expired_code_rejected(self):
        c = self.client_for(self.cashier)
        c.get("/account/verify-email/")
        p = self.cashier.profile
        p.refresh_from_db()
        from datetime import timedelta
        p.email_code_expires = timezone.now() - timedelta(minutes=1)
        p.save()
        r = c.post("/account/verify-email/", {"action": "verify", "code": self.code_from_outbox()})
        self.assertEqual(r.status_code, 200)
        self.cashier.profile.refresh_from_db()
        self.assertFalse(self.cashier.profile.email_verified)

    def test_resend_is_throttled_and_refresh_does_not_spam(self):
        c = self.client_for(self.cashier)
        c.get("/account/verify-email/")
        c.get("/account/verify-email/")
        self.assertEqual(len(mail.outbox), 1)
        c.post("/account/verify-email/", {"action": "resend"})
        self.assertEqual(len(mail.outbox), 1)                   # inside the 60 second window

    def test_code_is_not_stored_in_clear(self):
        c = self.client_for(self.cashier)
        c.get("/account/verify-email/")
        self.cashier.profile.refresh_from_db()
        self.assertNotIn(self.code_from_outbox(), self.cashier.profile.email_code_hash)

    def test_changing_email_requires_new_verification(self):
        p = self.cashier.profile
        p.email_verified = True
        p.save()
        self.admin.profile.email_verified = True
        self.admin.profile.save()
        c = login_client(self.admin)
        c.post(f"/users/{p.pk}/edit/", {"first_name": "A", "last_name": "B", "email": "new@example.com", "phone": "0772000000", "role": "cashier", "is_active": "on"})
        p.refresh_from_db()
        self.assertFalse(p.email_verified)

    def test_disabled_when_no_mail_server(self):
        with override_settings(EMAIL_VERIFICATION=False):
            self.assertEqual(self.client_for(self.cashier).get("/cashier/").status_code, 200)

    def test_user_without_email_is_asked_for_one(self):
        self.cashier.email = ""
        self.cashier.save()
        c = self.client_for(self.cashier)
        r = c.get("/account/verify-email/")
        self.assertContains(r, "Add your email address")
        c.post("/account/verify-email/", {"action": "set_email", "email": "mine@example.com"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["mine@example.com"])
