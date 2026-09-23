"""SwapCheck local verifier-style tests (PRD §10)."""
from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path

# Isolate env before importing app
_TMP = tempfile.mkdtemp(prefix="swapcheck-test-")
os.environ["DATABASE_PATH"] = str(Path(_TMP) / "test.db")
os.environ["UPLOAD_DIR"] = str(Path(_TMP) / "uploads")
os.environ["SECRET_KEY"] = "test-secret"
os.environ["OWNER_PASSWORD"] = "testpass"
os.environ["TECH_PASSWORD"] = "techpass"
os.environ["BUSINESS_NAME"] = "Harbor HVAC"
os.environ["PUBLIC_BASE_URL"] = "http://127.0.0.1:8080"
os.environ["MARKETING_URL"] = ""
os.environ["MAX_UPLOAD_MB"] = "5"
for k in (
    "SMTP_HOST",
    "FROM_EMAIL",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM_NUMBER",
    "OFFICE_EMAIL",
    "OFFICE_PHONE",
):
    os.environ.pop(k, None)

import app as app_module  # noqa: E402
import helpers as H  # noqa: E402

SAMPLE = Path(__file__).resolve().parent / "sample-matrix.csv"

# Minimal 1x1 PNG
PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


class SwapCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        path = Path(os.environ["DATABASE_PATH"])
        if path.exists():
            path.unlink()
        upload = Path(os.environ["UPLOAD_DIR"])
        upload.mkdir(parents=True, exist_ok=True)
        for f in upload.glob("*"):
            f.unlink()
        H.init_schema(H.connect_db(str(path)))
        app_module.app.config["TESTING"] = True
        app_module.app.config["MAX_CONTENT_LENGTH"] = H.max_upload_bytes()
        self.client = app_module.app.test_client()
        self.app = app_module.app

    def _owner_login(self):
        return self.client.post(
            "/login",
            data={"password": "testpass"},
            follow_redirects=False,
        )

    def _tech_login(self):
        return self.client.post(
            "/tech/login",
            data={"password": "techpass"},
            follow_redirects=False,
        )

    def test_health_public(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertFalse(data["smtp_configured"])
        self.assertFalse(data["twilio_configured"])

    def test_owner_gates_home(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers.get("Location", ""))
        self._owner_login()
        r2 = self.client.get("/")
        self.assertEqual(r2.status_code, 200)

    def test_tech_gates_create(self):
        r = self.client.get("/requests/new")
        self.assertEqual(r.status_code, 302)
        loc = r.headers.get("Location", "")
        self.assertTrue("/tech/login" in loc or "/login" in loc)
        self._tech_login()
        r2 = self.client.get("/requests/new")
        self.assertEqual(r2.status_code, 200)

    def test_matrix_import_create_decide_export(self):
        self._owner_login()
        with SAMPLE.open("rb") as fh:
            r = self.client.post(
                "/matrix",
                data={"action": "import", "file": (fh, "sample-matrix.csv")},
                content_type="multipart/form-data",
                follow_redirects=True,
            )
        self.assertEqual(r.status_code, 200)
        with self.app.app_context():
            rows = H.list_matrix()
            self.assertGreaterEqual(len(rows), 5)

        # Create tech person
        r = self.client.post(
            "/people",
            data={"action": "create", "name": "Alex Tech", "role": "tech"},
            follow_redirects=True,
        )
        self.assertEqual(r.status_code, 200)
        with self.app.app_context():
            techs = H.list_people(role="tech")
            self.assertEqual(len(techs), 1)
            tech_id = techs[0]["id"]

        # Tech session: create hit + miss swaps with photo+spec, same job_ref
        tech = app_module.app.test_client()
        tech.post("/tech/login", data={"password": "techpass"})

        job = "JOB-1042"
        # Matrix HIT: CAP-45/5 → CAP-45/5-ALT
        r_hit = tech.post(
            "/requests/new",
            data={
                "job_ref": job,
                "tech_person_id": str(tech_id),
                "from_sku": "CAP-45/5",
                "from_name": "45/5 µF dual run capacitor",
                "to_sku": "CAP-45/5-ALT",
                "to_name": "45/5 µF dual run (alt brand)",
                "reason": "Damaged on truck",
                "spec_text": "45/5 µF 370V dual",
                "photo": (io.BytesIO(PNG_1X1), "plate.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(r_hit.status_code, 200)
        body_hit = r_hit.get_data(as_text=True)
        self.assertIn("pre-approved", body_hit)

        # Matrix MISS: random SKUs
        r_miss = tech.post(
            "/requests/new",
            data={
                "job_ref": job,
                "tech_person_id": str(tech_id),
                "from_sku": "UNKNOWN-1",
                "from_name": "Odd part",
                "to_sku": "UNKNOWN-2",
                "to_name": "Other part",
                "reason": "Not on truck",
                "spec_text": "amp unknown",
                "photo": (io.BytesIO(PNG_1X1), "other.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(r_miss.status_code, 200)
        body_miss = r_miss.get_data(as_text=True)
        self.assertIn("needs office", body_miss)

        with self.app.app_context():
            reqs = H.list_job_requests(job)
            self.assertEqual(len(reqs), 2)
            hit_req = next(r for r in reqs if r["matrix_hit"])
            miss_req = next(r for r in reqs if not r["matrix_hit"])
            self.assertTrue(hit_req["photo_path"])
            self.assertTrue(hit_req["spec_text"])
            hit_id = hit_req["id"]
            miss_id = miss_req["id"]
            hit_token = hit_req["decide_token"]

        # Approve via dashboard (owner)
        owner = app_module.app.test_client()
        owner.post("/login", data={"password": "testpass"})
        r_ap = owner.post(
            f"/requests/{hit_id}/approve",
            data={"decision_note": "OK same µF"},
            follow_redirects=True,
        )
        self.assertEqual(r_ap.status_code, 200)

        # Deny via magic /d/{token}
        magic = app_module.app.test_client()
        r_d = magic.get(f"/d/{hit_token}")  # already approved — locked page still 200
        self.assertEqual(r_d.status_code, 200)

        with self.app.app_context():
            miss = H.get_request(miss_id)
            miss_token = miss["decide_token"]

        r_deny = magic.post(
            f"/d/{miss_token}",
            data={"action": "denied", "decision_note": "Wrong amp"},
            follow_redirects=True,
        )
        self.assertEqual(r_deny.status_code, 200)
        self.assertIn("denied", r_deny.get_data(as_text=True).lower())

        with self.app.app_context():
            self.assertEqual(H.get_request(hit_id)["status"], "approved")
            self.assertEqual(H.get_request(miss_id)["status"], "denied")
            events_hit = H.list_events(hit_id)
            kinds = [e["kind"] for e in events_hit]
            self.assertIn("created", kinds)
            self.assertIn("matrix_hit", kinds)
            self.assertIn("approved", kinds)
            trail = H.list_job_trail(job)
            self.assertGreaterEqual(len(trail), 4)

        # Job trail page
        r_job = owner.get(f"/jobs/{job}")
        self.assertEqual(r_job.status_code, 200)
        job_body = r_job.get_data(as_text=True)
        self.assertIn("approved", job_body)
        self.assertIn("denied", job_body)

        # CSV export
        csv_r = owner.get("/export/requests.csv")
        self.assertEqual(csv_r.status_code, 200)
        csv_text = csv_r.get_data(as_text=True)
        self.assertIn("job_ref", csv_text)
        self.assertIn(job, csv_text)
        self.assertIn("approved", csv_text)
        self.assertIn("denied", csv_text)
        self.assertIn("matrix_hit", csv_text)

        csv_e = owner.get("/export/events.csv")
        self.assertEqual(csv_e.status_code, 200)
        self.assertIn("created", csv_e.get_data(as_text=True))

        # Magic decide works without owner cookie (fresh client already used)
        # Photo not world-open
        anon = app_module.app.test_client()
        r_photo = anon.get(f"/requests/{hit_id}/photo")
        self.assertIn(r_photo.status_code, (302, 403))

        # No PartPing / ParKit routes
        self.assertEqual(anon.get("/p/fake").status_code, 404)


if __name__ == "__main__":
    unittest.main()
