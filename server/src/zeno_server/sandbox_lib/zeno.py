"""ZENO's helper for code in a Bit's sandbox: use the user's apps, and reach the user.

From Python:
    import zeno
    zeno.apps()                                   # the app tools you can call, with their parameters
    zeno.tool("gmail-find-email", q="from:ann")   # call one; its result (parsed when it's JSON)
    r = zeno.api("gmail", "GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages?q=from:ann")
    r.status, r.ok, r.json(), r.text, r.content   # an app's own API, signed in as the user
    zeno.notify("Ann just emailed you about the contract.")   # ZENO tells the user right away

From the shell:
    zeno apps
    zeno tool gmail-find-email '{"q": "from:ann"}'
    zeno api gmail GET 'https://gmail.googleapis.com/gmail/v1/users/me/messages?q=from:ann'
    zeno api gmail GET URL --out message.json
    zeno api slack POST https://slack.com/api/chat.postMessage --data '{"channel": "C1", "text": "hi"}'
    zeno notify "Ann just emailed you."

Only the standard library is used, so this works in any Python 3 sandbox.
"""

import base64
import json as _json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("ZENO_URL", "").rstrip("/")
TOKEN = os.environ.get("ZENO_TOKEN", "")


class ZenoError(Exception):
    pass


def _call(path, payload=None, timeout=120):
    if not URL or not TOKEN:
        raise ZenoError("ZENO's gateway isn't available in this sandbox.")
    data = _json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{URL}/api/bit-gateway/{path}",
        data=data,
        method="POST" if data is not None else "GET",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return _json.loads(response.read())
    except urllib.error.HTTPError as e:
        try:
            detail = _json.loads(e.read()).get("detail")
        except Exception:
            detail = None
        raise ZenoError(detail or f"ZENO's gateway answered {e.code}") from None
    except urllib.error.URLError as e:
        raise ZenoError(f"Can't reach ZENO's gateway: {e.reason}") from None


def apps():
    """The user's app tools you can call: [{name, description, parameters}], plus the apps whose
    API you can call with api()."""
    return _call("apps")


def tool(name, arguments=None, **kwargs):
    """Call one of the user's app tools by name, e.g. tool("gmail-find-email", q="from:ann")."""
    result = _call("tool", {"name": name, "arguments": {**(arguments or {}), **kwargs}}, timeout=180)
    try:
        return _json.loads(result["result"])
    except (TypeError, ValueError):
        return result["result"]


class Response:
    def __init__(self, data):
        self.status = data["status"]
        self.headers = data.get("headers") or {}
        self.content = base64.b64decode(data.get("body_base64") or "")

    @property
    def ok(self):
        return 200 <= self.status < 300

    @property
    def text(self):
        return self.content.decode("utf-8", "replace")

    def json(self):
        return _json.loads(self.content)

    def __repr__(self):
        return f"<Response {self.status}, {len(self.content)} bytes>"


def api(app, method, url, json=None, *, data=None, headers=None):
    """Call an app's own API as the user, e.g. api("gmail", "GET", "https://gmail.googleapis.com/...").

    app is the app's id as apps() lists it. Pass a JSON body as json=, or raw bytes or text as data=.
    """
    headers = dict(headers or {})
    body = None
    if json is not None:
        body = _json.dumps(json).encode()
        headers.setdefault("Content-Type", "application/json")
    elif data is not None:
        body = data.encode() if isinstance(data, str) else data
    payload = {"app": app, "method": method.upper(), "url": url, "headers": headers}
    if body is not None:
        payload["body_base64"] = base64.b64encode(body).decode()
    return Response(_call("api", payload, timeout=90))


def notify(text):
    """Have ZENO tell the user something now (they'll hear it, or see it later)."""
    return _call("notify", {"text": str(text)})


def _main(argv):
    usage = (
        "usage: zeno apps | zeno tool NAME [JSON] | "
        "zeno api APP METHOD URL [--data JSON] [--out FILE] | zeno notify TEXT"
    )
    if not argv:
        print(usage, file=sys.stderr)
        return 2
    command, args = argv[0], argv[1:]
    try:
        if command == "apps":
            print(_json.dumps(apps(), indent=2))
        elif command == "tool" and args:
            arguments = _json.loads(args[1]) if len(args) > 1 else {}
            result = tool(args[0], arguments)
            print(result if isinstance(result, str) else _json.dumps(result, indent=2))
        elif command == "api" and len(args) >= 3:
            app, method, url = args[:3]
            rest = args[3:]
            body = out = None
            while rest:
                flag = rest.pop(0)
                if flag == "--data" and rest:
                    body = rest.pop(0)
                elif flag == "--out" and rest:
                    out = rest.pop(0)
                else:
                    print(usage, file=sys.stderr)
                    return 2
            headers = {"Content-Type": "application/json"} if body else None
            response = api(app, method, url, data=body, headers=headers)
            if out:
                with open(out, "wb") as f:
                    f.write(response.content)
                print(f"HTTP {response.status}: saved {len(response.content)} bytes to {out}")
            else:
                print(response.text)
                if not response.ok:
                    print(f"HTTP {response.status}", file=sys.stderr)
            return 0 if response.ok else 1
        elif command == "notify" and args:
            notify(" ".join(args))
            print("Sent.")
        else:
            print(usage, file=sys.stderr)
            return 2
    except ZenoError as e:
        print(f"zeno: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
