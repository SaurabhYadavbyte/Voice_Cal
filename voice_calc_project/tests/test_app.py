import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-only")

from app import app, evaluate, init_db  # noqa: E402


class CalculatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        app.config.update(TESTING=True, DATABASE=Path(self.temp.name) / "test.sqlite3")
        init_db()
        self.client = app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def token(self):
        with self.client.session_transaction() as session:
            return session["csrf_token"]

    def register(self, username="alice"):
        self.client.get("/register")
        return self.client.post(
            "/register",
            data={"username": username, "password": "correct horse battery", "csrf_token": self.token()},
        )

    def login(self, username="alice"):
        self.client.get("/login")
        response = self.client.post(
            "/login",
            data={"username": username, "password": "correct horse battery", "csrf_token": self.token()},
        )
        if response.status_code == 302:
            self.client.get("/calculator")
        return response

    def calculate(self, expression):
        return self.client.post(
            "/calculate",
            json={"expression": expression},
            headers={"X-CSRF-Token": self.token()},
        )

    def test_arithmetic_and_rejected_code(self):
        self.assertEqual(evaluate("2 + 3 * (4 - 1)"), "11")
        self.assertEqual(evaluate("0.1 + 0.2"), "0.3")
        for expression in ["1/0", "2**1000", "__import__('os')", "abc1+2", "9" * 121]:
            with self.subTest(expression=expression):
                with self.assertRaises(ValueError):
                    evaluate(expression)

    def test_account_calculation_and_history(self):
        self.assertEqual(self.register().status_code, 302)
        self.assertIn(b"Username already exists", self.register().data)
        self.assertEqual(self.login().status_code, 302)
        response = self.calculate("5 / 2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["result"], "2.5")
        self.assertIn(b"5 / 2", self.client.get("/history").data)
        self.assertEqual(self.calculate("1/0").status_code, 400)
        self.assertEqual(
            self.client.post("/clear_history", data={"csrf_token": self.token()}).status_code, 302
        )
        self.assertNotIn(b"5 / 2", self.client.get("/history").data)

    def test_auth_and_csrf(self):
        self.assertEqual(self.client.get("/calculator").status_code, 302)
        self.assertEqual(self.client.post("/register", data={"username": "bad"}).status_code, 400)
        self.register()
        self.login()
        self.assertEqual(self.client.post("/calculate", json={"expression": "1+1"}).status_code, 400)
        self.assertEqual(
            self.client.post("/logout", data={"csrf_token": self.token()}).status_code, 302
        )
        self.assertEqual(self.client.get("/history").status_code, 302)

    def test_history_is_private(self):
        self.register()
        self.login()
        self.calculate("17+4")
        self.client.post("/logout", data={"csrf_token": self.token()})
        self.register("bob")
        self.login("bob")
        self.assertNotIn(b"17+4", self.client.get("/history").data)


if __name__ == "__main__":
    unittest.main()
