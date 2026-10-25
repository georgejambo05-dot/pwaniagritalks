import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import check_password_hash, generate_password_hash

_TEST_DB_DIR = tempfile.TemporaryDirectory()
_ENV_KEYS = ("DATABASE_URL", "RENDER", "SECRET_KEY", "ADMIN_SETUP_TOKEN")
_ORIGINAL_ENV = {key: os.environ.get(key) for key in _ENV_KEYS}
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_TEST_DB_DIR.name) / 'test.db'}"
os.environ["RENDER"] = "1"
os.environ["SECRET_KEY"] = "test-secret"
os.environ.pop("ADMIN_SETUP_TOKEN", None)

import app as app_module

for _key, _value in _ORIGINAL_ENV.items():
    if _value is None:
        os.environ.pop(_key, None)
    else:
        os.environ[_key] = _value


class DatabaseAndRegistrationTest(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(
            TESTING=True, SECRET_KEY="test-secret", SESSION_COOKIE_SECURE=False
        )
        with app_module.database_connection() as connection:
            connection.execute("DELETE FROM farmer_questions")
            connection.execute("DELETE FROM admin_users")
            connection.execute("DELETE FROM farmers")
        self.client = app_module.app.test_client()
        with self.client.session_transaction() as session:
            session["csrf_token"] = "test-csrf-token"

    def test_database_connection_closes_after_context(self):
        with app_module.database_connection() as connection:
            connection.execute("SELECT 1")
        self.assertTrue(connection.closed)

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

    def test_postgres_urls_use_psycopg_driver(self):
        self.assertEqual(
            app_module.normalize_database_url("postgres://host.example/database"),
            "postgresql+psycopg://host.example/database",
        )
        self.assertEqual(
            app_module.normalize_database_url("postgresql://host.example/database"),
            "postgresql+psycopg://host.example/database",
        )

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
        self.assertEqual(farmer[:4], ("Test Farmer", "0712345678", "Kilifi", "crops"))
        self.assertNotEqual(farmer[4], "test-password")
        self.assertTrue(check_password_hash(farmer[4], "test-password"))

    def test_remote_admin_setup_requires_token_and_hashes_password(self):
        remote = {"REMOTE_ADDR": "203.0.113.12"}
        with patch.dict(os.environ, {"ADMIN_SETUP_TOKEN": "one-time-test-token"}):
            login_response = self.client.get("/login", environ_base=remote)
            self.assertEqual(login_response.status_code, 302)
            self.assertIn("/setup", login_response.headers["Location"])

            setup_response = self.client.get("/setup", environ_base=remote)
            self.assertEqual(setup_response.status_code, 200)
            self.assertIn(b"One-time setup token", setup_response.data)
            with self.client.session_transaction() as session:
                csrf = session["csrf_token"]

            response = self.client.post(
                "/setup",
                environ_base=remote,
                data={
                    "_csrf_token": csrf,
                    "setup_token": "one-time-test-token",
                    "username": "site-admin",
                    "password": "strong-admin-password-123",
                    "confirmation": "strong-admin-password-123",
                },
            )
            self.assertEqual(response.status_code, 302)
            self.assertIn("created=yes", response.headers["Location"])

            with app_module.database_connection() as connection:
                admin = connection.execute(
                    "SELECT username, password_hash FROM admin_users WHERE username = ?",
                    ("site-admin",),
                ).fetchone()
            self.assertIsNotNone(admin)
            self.assertNotEqual(admin[1], "strong-admin-password-123")
            self.assertTrue(check_password_hash(admin[1], "strong-admin-password-123"))

            with self.client.session_transaction() as session:
                csrf = session["csrf_token"]
            login_response = self.client.post(
                "/login",
                environ_base=remote,
                data={
                    "_csrf_token": csrf,
                    "username": "site-admin",
                    "password": "strong-admin-password-123",
                },
            )
            self.assertEqual(login_response.status_code, 302)
            self.assertIn("/farmers", login_response.headers["Location"])

    def test_farmer_and_admin_question_views_use_shared_database(self):
        with app_module.database_connection() as connection:
            connection.execute(
                """INSERT INTO farmers
                   (full_name, phone, location, farming_type, password_hash)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    "Coastal Farmer", "0712345678", "Kilifi", "crops",
                    generate_password_hash("farmer-password"),
                ),
            )
            farmer_id = connection.execute(
                "SELECT id FROM farmers WHERE full_name = ?", ("Coastal Farmer",)
            ).fetchone()[0]

        with self.client.session_transaction() as session:
            session["farmer_id"] = farmer_id
            session["csrf_token"] = "test-csrf-token"
        self.assertEqual(self.client.get("/farmer-portal").status_code, 200)
        self.assertEqual(self.client.get("/farmer-questions").status_code, 200)
        question_response = self.client.post(
            "/farmer-questions",
            data={"_csrf_token": "test-csrf-token", "question_text": "Why are my maize leaves yellow?"},
        )
        self.assertEqual(question_response.status_code, 302)

        with self.client.session_transaction() as session:
            session.clear()
            session["admin_username"] = "site-admin"
            session["csrf_token"] = "test-csrf-token"
        farmers_response = self.client.get("/farmers?q=kilifi")
        self.assertEqual(farmers_response.status_code, 200)
        self.assertIn(b"Coastal Farmer", farmers_response.data)
        export_response = self.client.get("/farmers/export?q=kilifi")
        self.assertEqual(export_response.status_code, 200)
        self.assertIn(b"Coastal Farmer", export_response.data)
        questions_response = self.client.get("/questions")
        self.assertEqual(questions_response.status_code, 200)
        self.assertIn(b"Why are my maize leaves yellow?", questions_response.data)

        with app_module.database_connection() as connection:
            question_id = connection.execute(
                "SELECT id FROM farmer_questions WHERE farmer_id = ?", (farmer_id,)
            ).fetchone()[0]
        reply_response = self.client.post(
            f"/questions/{question_id}/reply",
            data={"_csrf_token": "test-csrf-token", "answer": "Check soil drainage and nutrients."},
        )
        self.assertEqual(reply_response.status_code, 302)

        with self.client.session_transaction() as session:
            session.clear()
            session["farmer_id"] = farmer_id
            session["csrf_token"] = "test-csrf-token"
        farmer_questions_response = self.client.get("/farmer-questions")
        self.assertEqual(farmer_questions_response.status_code, 200)
        self.assertIn(b"Check soil drainage and nutrients.", farmer_questions_response.data)


def tearDownModule():
    with app_module.app.app_context():
        app_module.db.engine.dispose()
    _TEST_DB_DIR.cleanup()


if __name__ == "__main__":
    unittest.main()
