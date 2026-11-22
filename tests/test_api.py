"""Request limits and failure classification, without a live portal."""
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from http.client import IncompleteRead
from io import BytesIO
import unittest
from unittest.mock import Mock, patch

from custom_components.jrk_digital.api import CitizenPortal, LoginError, PortalError, PortalHTTPError
from custom_components.jrk_digital.data import pages


def response(status, body=b"{}", **headers):
    result = BytesIO(body)
    result.status = status
    result.headers = headers
    return result


class TestClient(unittest.TestCase):
    def setUp(self):
        self.client = CitizenPortal("example", "unused")
        self.client.token = "cached"

    def test_only_one_relogin_for_expired_access_token(self):
        with (patch.object(self.client, "_request", side_effect=[response(401), response(401)]) as request,
              patch.object(self.client, "login", side_effect=lambda: setattr(self.client, "token", "fresh")) as login):
            with self.assertRaises(LoginError):
                self.client.get("/example")
            login.assert_called_once()
            self.assertEqual(request.call_count, 2)

    def test_explicit_errors_are_not_retried(self):
        deadline = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=2)
        for status in (403, 404, 429, 500, 502, 503):
            with (self.subTest(status=status), patch.object(self.client, "_request", return_value=response(
                    status, **{"Retry-After": format_datetime(deadline, usegmt=True)})) as request):
                with self.assertRaises(PortalHTTPError) as caught:
                    self.client.get("/example")
                self.assertEqual(caught.exception.retry_at, deadline)
                request.assert_called_once()
        for header in ("", "later", "-5"):
            self.assertIsNone(PortalHTTPError(response(503, **{"Retry-After": header})).retry_at)

    def test_invalid_and_truncated_body(self):
        for payload in (b"{", b"[]", b"<html>"):
            with patch.object(self.client, "_request", return_value=response(200, payload)):
                with self.assertRaises(PortalError):
                    self.client.get("/example")
        broken = response(200)
        broken.read = Mock(side_effect=IncompleteRead(b"", 10))
        with patch.object(self.client, "_request", return_value=broken), self.assertRaises(PortalError):
            self.client.get("/example")

    def test_invalid_pagination_stops_early(self):
        for count, total, items in [(100000, 1, [1]), (10, 10, []), (0, 4, [1])]:
            api = Mock()
            api.get.return_value = {"pageCount": count, "total": total, "items": items}
            with self.assertRaises(PortalError):
                pages(api, "/example", {})
            api.get.assert_called_once()
