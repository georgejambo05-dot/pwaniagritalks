import sqlite3
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import check_password_hash

import app as app_module


class FarmerRegistrationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = app_module.DATABASE
        self.original_secret_key = app_module.app.secret_key
        self.original_testing = app_module.app.config.get("TESTING")

        app_module.DATABASE = Path(self.temp_dir.name) / "farmers.db"
        app_module.create_database()
        app_module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = app_module.app.test_client()
        with self.client.session_transaction() as session:
            session["csrf_token"] = "test-csrf-token"

    def tearDown(self):
        app_module.DATABASE = self.original_database
        app_module.app.config.update(
            TESTING=self.original_testing,
            SECRET_KEY=self.original_secret_key,
        )
        self.temp_dir.cleanup()

    def test_database_connection_closes_after_context(self):
        with app_module.database_connection() as connection:
            connection.execute("SELECT 1")

        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_database_connection_rolls_back_on_error(self):
        with self.assertRaises(RuntimeError):
            with app_module.database_connection() as connection:
                connection.execute(
                    "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
                    ("temporary-admin", "hash"),
                )
                raise RuntimeError("force rollback")

        with app_module.database_connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM admin_users WHERE username = ?",
                ("temporary-admin",),
            ).fetchone()[0]

        self.assertEqual(count, 0)

    def test_registration_persists_farmer_with_hashed_password(self):
        response = self.client.post(
            "/",
            data={
                "_csrf_token": "test-csrf-token",
                "full_name": "Test Farmer",
                "phone": "0712345678",
                "location": "Kilifi",
                "farming_type": "crops",
                "password": "test-password",
                "confirmation": "test-password",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("registered=yes", response.headers["Location"])

        with app_module.database_connection() as connection:
            farmer = connection.execute(
                """SELECT full_name, phone, location, farming_type, password_hash
                   FROM farmers"""
            ).fetchone()

        self.assertIsNotNone(farmer)
        self.assertEqual(
            farmer[:4], ("Test Farmer", "0712345678", "Kilifi", "crops")
        )
        self.assertNotEqual(farmer[4], "test-password")
        self.assertTrue(check_password_hash(farmer[4], "test-password"))


if __name__ == "__main__":
    unittest.main()
