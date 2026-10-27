from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.test import Client, TestCase

from .sap import SapError, get_sap_rows


class AuditViewTests(TestCase):
    @patch("audit.views.get_sap_rows")
    def test_recovers_active_audit_after_session_cookie_changes(self, get_sap_rows):
        get_sap_rows.return_value = [
            {
                "material": "MAT-1",
                "grid_value": "GRID-1",
                "stock_category": "CNA999999",
                "user": "USER1",
                "quantity": 2,
                "stock_ids": ["200269247493"],
            }
        ]
        self.client.post(
            "/",
            {"action": "start", "from_date": "2026-10-13", "to_date": "2026-10-13"},
        )
        self.client.post("/", {"action": "scan", "stock_id": "00200269247493"})

        response = Client().post("/", {"action": "scan", "stock_id": "00200269247493"})

        self.assertContains(response, "ACEPTADO — 7493 — 2/2")

    @patch("audit.views.get_sap_rows")
    def test_loads_sap_rows_and_registers_scan(self, get_sap_rows):
        get_sap_rows.return_value = [
            {
                "material": "MAT-1",
                "grid_value": "GRID-1",
                "stock_category": "CNA999999",
                "user": "LOPEZHJC",
                "quantity": 2,
                "stock_ids": ["200269247493"],
            },
            {
                "material": "MAT-1",
                "grid_value": "GRID-1",
                "stock_category": "CNA999999",
                "user": "LOPEZHJC",
                "quantity": 1,
                "stock_ids": ["200269247493"],
            },
        ]

        response = self.client.post(
            "/",
            {"action": "start", "from_date": "2026-09-23", "to_date": "2026-09-24"},
        )
        self.assertContains(response, "MAT-1")
        self.assertContains(response, "PENDIENTE")
        self.assertContains(response, "3 unidades")
        self.assertContains(response, "keepScannerFocused")
        self.assertContains(response, "queue = queue.then")

        response = self.client.post("/", {"action": "scan", "stock_id": "ABC!"})
        self.assertContains(response, "RECHAZADO — El Stock ID sólo puede contener números")
        self.assertContains(response, "scan-error")

        response = self.client.post(
            "/",
            {"action": "scan", "stock_id": "00200269247493"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.json()["message"], "ACEPTADO — 7493 — 1/3")
        self.assertEqual(response.json()["flash"], "success")
        self.assertEqual(response.json()["rows"][0]["status"], "PARCIAL")

        self.client.post("/", {"action": "scan", "stock_id": "00200269247493"})
        response = self.client.post("/", {"action": "scan", "stock_id": "00200269247493"})
        self.assertContains(response, "3/3")
        self.assertContains(response, "COMPLETO")

        response = self.client.post("/", {"action": "scan", "stock_id": "00200269247493"})
        self.assertContains(response, "AMARILLO — 7493 — EXCESO")
        self.assertContains(response, "No se sumó")
        self.assertContains(response, "scan-warning")
        self.assertEqual(self.client.session["audit_rows"][0]["scanned_qty"], 3)

        response = self.client.post("/", {"action": "reset"})
        self.assertContains(response, "Auditoría reiniciada")
        self.assertNotIn("audit_rows", self.client.session)

    @patch("audit.sap.subprocess.run")
    def test_parses_ltap_and_mean_output(self, run):
        run.return_value = SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=(
                "LTAP\tMAT-1\tGRID-1\tCA999999\tUSER1\t2\n"
                "MEAN\tMAT-1\tGRID-1\tCA\t200269247493\n"
                "MEAN\tMAT-1\tGRID-1\tUSA\t200456\n"
            ),
        )

        rows = get_sap_rows(date(2026, 9, 24), date(2026, 9, 24))

        self.assertEqual(rows[0]["quantity"], 2)
        self.assertEqual(rows[0]["stock_ids"], ["00200269247493"])

    @patch("audit.views.get_sap_rows")
    def test_blocks_ambiguous_stock_ids(self, get_sap_rows):
        get_sap_rows.return_value = [
            {
                "material": "MAT-1",
                "grid_value": "GRID-1",
                "stock_category": "CNA999999",
                "user": "USER1",
                "quantity": 1,
                "stock_ids": ["200123", "200456"],
            }
        ]

        response = self.client.post(
            "/",
            {"action": "start", "from_date": "2026-09-24", "to_date": "2026-09-24"},
        )
        self.assertContains(response, "STOCK ID AMBIGUO")

        response = self.client.post("/", {"action": "scan", "stock_id": "200123"})
        self.assertContains(response, "bloqueado")
        self.assertContains(response, "scan-error")

    @patch("audit.sap.subprocess.run")
    def test_treats_sap_script_stderr_as_an_error(self, run):
        run.return_value = SimpleNamespace(returncode=0, stderr="SAP error", stdout="")

        with self.assertRaises(SapError):
            get_sap_rows(date(2026, 9, 24), date(2026, 9, 24))
