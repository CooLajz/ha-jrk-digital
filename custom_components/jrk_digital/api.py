"""Read the JRK citizen portal using its authorization-code flow with PKCE."""
from __future__ import annotations

import base64
import hashlib
from html.parser import HTMLParser
from http.cookiejar import CookieJar
import json
import secrets
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

AUTH = "https://auth.econit.chytreodpady.cz"
API = "https://api-cp.jrkdigital.eu/v1"
CALLBACK = "https://app-cp.jrkdigital.eu/login"
CLIENT_ID = "snadnee_frontend_econit_citizen_portal"


class PortalError(Exception):
    """The portal is unavailable or returned an unexpected response."""


class LoginError(PortalError):
    """The portal did not accept the account login."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class _LoginForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = (attrs.get("action", ""), {})
            self.forms.append(self.current)
        if tag == "input" and self.current and attrs.get("name"):
            self.current[1][attrs["name"]] = (attrs.get("type", "text"), attrs.get("value", ""))

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


class CitizenPortal:
    """Use one client per account, from a worker thread, one operation at a time."""

    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.opener = None
        self.token = None

    def _request(self, url, data=None, headers=None):
        if self.opener is None:
            self.opener = build_opener(HTTPCookieProcessor(CookieJar()), _NoRedirect())
        body = urlencode(data).encode() if data is not None else None
        try:
            return self.opener.open(Request(url, data=body, headers=headers or {}), timeout=25)
        except HTTPError as exc:
            return exc
        except (OSError, URLError):
            raise PortalError("Spojení s JRK se nezdařilo.") from None

    def _follow(self, url, data=None):
        for _ in range(10):
            if not url.startswith(AUTH + "/"):
                raise PortalError("Neočekávaná adresa přihlašovacího serveru.")
            with self._request(url, data) as response:
                if response.status == 200:
                    return url, response.read().decode()
                if response.status not in (301, 302, 303) or not response.headers.get("Location"):
                    raise PortalError(f"Přihlašovací server vrátil HTTP {response.status}.")
                url = urljoin(url, response.headers["Location"])
            if url.split("?")[0] == CALLBACK:
                return url, None
            data = None
        raise PortalError("Příliš mnoho přesměrování při přihlášení.")

    def login(self):
        state = secrets.token_urlsafe()
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        params = dict(client_id=CLIENT_ID, redirect_uri=CALLBACK, response_type="code",
                      scope="openid profile email api_citizen_portal.read offline_access",
                      state=state, nonce=secrets.token_urlsafe(), code_challenge_method="S256",
                      code_challenge=challenge)
        url, html = self._follow(AUTH + "/connect/authorize?" + urlencode(params))
        if html is not None:
            parser = _LoginForm()
            parser.feed(html)
            for action, fields in parser.forms:
                if "Password" in fields and "Username" in fields:
                    break
            else:
                raise PortalError("Přihlašovací formulář JRK se změnil.")
            payload = {name: value for name, (kind, value) in fields.items() if kind == "hidden"}
            payload.update(Username=self.username, Password=self.password, button="login")
            url, html = self._follow(urljoin(url, action or url), payload)
        query = parse_qs(urlsplit(url).query)
        if html is not None or query.get("error"):
            raise LoginError("Přihlášení k účtu JRK se nezdařilo.")
        if query.get("state") != [state] or not query.get("code"):
            raise PortalError("Neplatná odpověď přihlašovacího serveru.")
        with self._request(AUTH + "/connect/token", dict(grant_type="authorization_code",
                           client_id=CLIENT_ID, redirect_uri=CALLBACK, code=query["code"][0],
                           code_verifier=verifier)) as response:
            if response.status != 200:
                raise PortalError(f"Výměna přihlašovacího kódu vrátila HTTP {response.status}.")
            self.token = json.load(response)["access_token"]

    def get(self, path, params=None):
        url = API + path + ("?" + urlencode(params) if params else "")
        for attempt in range(2):
            if not self.token:
                self.login()
            with self._request(url, headers={"Authorization": "Bearer " + self.token, "Accept": "application/json"}) as r:
                if r.status == 200:
                    return json.load(r)
                if r.status == 401 and not attempt:
                    self.token = None
                    continue
                raise PortalError(f"Čtení dat JRK vrátilo HTTP {r.status}.")

    def subjects(self):
        result = self.get("/user/user-info/managed-subjects")
        if not result["items"]:
            own = self.get("/user/user-info").get("subject")
            return {str(own["id"]): own.get("name") or str(own["id"])} if own else {}
        return {str(item["id"]): item["name"] for item in result["items"]}
