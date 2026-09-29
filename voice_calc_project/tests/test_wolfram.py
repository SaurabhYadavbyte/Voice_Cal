import os
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from wolfram import WolframError, query_wolfram, validate_query


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.body


class WolframClientTests(unittest.TestCase):
    def test_query_is_encoded_and_degree_mode_is_explicit(self):
        captured = {}

        def fake_open(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return FakeResponse(b"0.5")

        with patch.dict(os.environ, {"WOLFRAM_APP_ID": "TESTAPP-123456"}), patch(
            "wolfram.urlopen", side_effect=fake_open
        ):
            query, result = query_wolfram("sine of thirty", "deg")

        params = parse_qs(urlparse(captured["url"]).query)
        self.assertEqual(query, "sine of thirty")
        self.assertEqual(result, "0.5")
        self.assertEqual(params["i"], ["sine of thirty in degrees"])
        self.assertEqual(params["appid"], ["TESTAPP-123456"])
        self.assertEqual(captured["timeout"], 8)

    def test_rejects_non_math_and_unconfigured_requests(self):
        with self.assertRaises(WolframError) as context:
            validate_query("who is the president")
        self.assertEqual(context.exception.status_code, 400)

        with patch.dict(os.environ, {"WOLFRAM_APP_ID": ""}):
            with self.assertRaises(WolframError) as context:
                query_wolfram("integrate x squared", "deg")
        self.assertEqual(context.exception.status_code, 503)

    def test_provider_errors_have_safe_messages(self):
        error = HTTPError("https://api.wolframalpha.com/v1/result", 501, "Not implemented", {}, None)
        with patch.dict(os.environ, {"WOLFRAM_APP_ID": "TESTAPP-123456"}), patch(
            "wolfram.urlopen", side_effect=error
        ):
            with self.assertRaises(WolframError) as context:
                query_wolfram("solve x squared equals four", "deg")
        self.assertEqual(context.exception.status_code, 400)
        self.assertNotIn("TESTAPP", str(context.exception))


if __name__ == "__main__":
    unittest.main()
