import os
import unittest
from unittest.mock import patch

from app import app


class FitTrackSmokeTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)

    def test_homepage_is_served(self):
        response = app.test_client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"FIT", response.data)

    def test_health_fails_closed_without_database_credentials(self):
        with patch.dict(os.environ, {
            "MYSQL_HOST": "", "MYSQL_USER": "", "MYSQL_PASSWORD": "",
            "MYSQL_DATABASE": "", "MONGO_URI": "", "MONGO_DATABASE": "fittrack"
        }):
            response = app.test_client().get("/api/health")
        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["checks"]["mysql"], "error")
        self.assertEqual(payload["checks"]["mongodb"], "error")


if __name__ == "__main__":
    unittest.main()
