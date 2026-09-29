"""Small, defensive client for the Wolfram|Alpha Short Answers API."""
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_URL = "https://api.wolframalpha.com/v1/result"
MAX_QUERY_LENGTH = 240
MAX_RESULT_BYTES = 4096
MAX_RESULT_LENGTH = 500
APP_ID_PATTERN = re.compile(r"[A-Za-z0-9-]{6,80}")
MATH_QUERY_PATTERN = re.compile(
    r"(?:\d|[+*/^%=]|\b(?:add|addition|algebra|arc|average|calculate|cos|cosine|cube|"
    r"derivative|differentiate|divide|division|equation|factorial|integral|integrate|limit|"
    r"log|logarithm|matrix|minus|multiply|multiplication|percent|percentage|plus|power|root|"
    r"sin|sine|solve|square|subtract|subtraction|tan|tangent|times|vector)\b)",
    re.IGNORECASE,
)
TRIG_PATTERN = re.compile(r"\b(?:arc)?(?:sin|sine|cos|cosine|tan|tangent)\b", re.IGNORECASE)
ANGLE_PATTERN = re.compile(r"\b(?:degrees?|radians?)\b", re.IGNORECASE)


class WolframError(Exception):
    """An API failure with a message and safe HTTP status for the client."""

    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


def configured():
    """Return whether a plausible AppID is available without revealing it."""
    app_id = os.environ.get("WOLFRAM_APP_ID", "").strip()
    return bool(APP_ID_PATTERN.fullmatch(app_id))


def validate_query(value):
    if not isinstance(value, str):
        raise WolframError("Send a spoken calculation.", 400)
    if any(ord(character) < 32 for character in value):
        raise WolframError("The spoken calculation contains invalid characters.", 400)
    query = " ".join(value.split())
    if not query:
        raise WolframError("No calculation was heard.", 400)
    if len(query) > MAX_QUERY_LENGTH:
        raise WolframError("The spoken calculation is too long.", 400)
    if not MATH_QUERY_PATTERN.search(query):
        raise WolframError("Please ask a mathematical calculation.", 400)
    return query


def query_wolfram(value, angle_mode="deg"):
    """Return a short plain-text result for one validated mathematical query."""
    query = validate_query(value)
    if not isinstance(angle_mode, str) or angle_mode not in {"deg", "rad"}:
        raise WolframError("Choose degrees or radians.", 400)

    app_id = os.environ.get("WOLFRAM_APP_ID", "").strip()
    if not APP_ID_PATTERN.fullmatch(app_id):
        raise WolframError("Advanced voice calculations are not configured yet.", 503)

    api_query = query
    if TRIG_PATTERN.search(query) and not ANGLE_PATTERN.search(query):
        api_query += " in " + ("degrees" if angle_mode == "deg" else "radians")

    url = API_URL + "?" + urlencode({
        "appid": app_id,
        "i": api_query,
        "units": "metric",
        "timeout": "5",
    })
    api_request = Request(url, headers={"User-Agent": "VoiceCalc/1.0"})
    try:
        with urlopen(api_request, timeout=8) as response:
            raw_result = response.read(MAX_RESULT_BYTES + 1)
    except HTTPError as exc:
        if exc.code in {400, 501}:
            raise WolframError("I could not understand that calculation.", 400) from exc
        if exc.code in {401, 403}:
            raise WolframError("Advanced voice calculations are not configured correctly.", 503) from exc
        if exc.code == 429:
            raise WolframError("The advanced calculation limit has been reached. Try again later.", 503) from exc
        raise WolframError("The advanced calculation service is unavailable. Try again.", 502) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise WolframError("The advanced calculation service is unavailable. Try again.", 502) from exc

    if len(raw_result) > MAX_RESULT_BYTES:
        raise WolframError("The advanced result was too long to display.", 502)
    result = " ".join(raw_result.decode("utf-8", errors="replace").split())
    if not result or len(result) > MAX_RESULT_LENGTH:
        raise WolframError("The advanced result could not be displayed.", 502)
    return query, result
