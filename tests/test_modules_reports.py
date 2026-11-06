from django.test import Client

from maintenance.models import Module, ModuleRole
from maintenance import services as msvc

from .base import SaccoTestCase
from .test_security import login_client


class ModuleActivation(SaccoTestCase):
    def make(self):
        m = Module.objects.create(key="zakat", name="Zakat Fund", icon="volunteer_activism", category="extensions", builtin=False, enabled=False)
        ModuleRole.objects.create(module=m, role="manager")
        return m

    def test_module_hidden_until_all_gates_passed_and_enabled(self):
        m = self.make()
        self.assertFalse(m.can_activate)
        c = login_client(self.admin)
        c.post(f"/maintenance/modules/{m.pk}/enable/")
        m.refresh_from_db()
        self.assertFalse(m.enabled)
        for gate in ("dev_tested", "security_reviewed", "migrated", "approve"):
            c.post(f"/maintenance/modules/{m.pk}/{gate}/")
        c.post(f"/maintenance/modules/{m.pk}/enable/")
        m.refresh_from_db()
        self.assertTrue(m.enabled)

    def test_sidebar_shows_module_only_to_assigned_role(self):
        m = self.make()
        m.dev_tested = m.security_reviewed = m.migrated = True
        m.approved_by, m.enabled, m.status = self.admin, True, "active"
        m.save()
        mgr = login_client(self.manager).get("/").content.decode()
        cash = login_client(self.cashier).get("/").content.decode()
        self.assertIn("Zakat Fund", mgr)
        self.assertNotIn("Zakat Fund", cash)

    def test_disabled_module_not_in_sidebar(self):
        self.make()
        self.assertNotIn("Zakat Fund", login_client(self.manager).get("/").content.decode())


class ReportExports(SaccoTestCase):
    def test_every_report_renders_and_exports(self):
        from reports.registry import REPORTS
        c = login_client(self.admin)
        for key in REPORTS:
            r = c.get(f"/reports/{key}/")
            self.assertEqual(r.status_code, 200, key)
        for fmt, ctype in (("csv", "text/csv"), ("xlsx", "spreadsheetml"), ("pdf", "application/pdf")):
            r = c.get("/reports/trial_balance/", {"export": fmt})
            self.assertEqual(r.status_code, 200, fmt)
            self.assertIn(ctype, r["Content-Type"])
            self.assertGreater(len(r.content), 20)
        self.assertTrue(c.get("/reports/trial_balance/", {"export": "pdf"}).content.startswith(b"%PDF"))
        self.assertTrue(c.get("/reports/trial_balance/", {"export": "xlsx"}).content.startswith(b"PK"))

    def test_user_without_export_permission_is_refused_export(self):
        c = login_client(self.cashier)
        r = c.get("/reports/trial_balance/", {"export": "csv"})
        self.assertNotEqual(r.get("Content-Type", "").split(";")[0], "text/csv")


class UiAcceptance(SaccoTestCase):
    def test_product_without_rate_shows_badge(self):
        self.product(rate=None, code="NR")
        html = login_client(self.admin).get("/loans/products/").content.decode()
        self.assertIn("Rate not set", html)

    def test_duplicate_member_rejected_through_the_form(self):
        c = login_client(self.cashier)
        data = {"full_name": "Another Person", "gender": "F", "phone": "0772000009", "nin": "CF11111111AAAA", "next_of_kin_name": "K",
                "next_of_kin_phone": "0772000010", "consent_given": "on", "member_no": self.member.member_no}
        before = type(self.member).objects.count()
        c.post("/members/new/", data)
        c.post("/members/new/", {**data, "nin": "CM12345678ABCD", "full_name": "Duplicate NIN"})
        self.assertLessEqual(type(self.member).objects.count(), before + 1)       # the duplicate NIN is never saved
        self.assertFalse(type(self.member).objects.filter(full_name="Duplicate NIN").exists())
