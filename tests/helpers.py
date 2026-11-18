"""Test doubles shared across test modules."""

import json

from bnpl_metrics.errors import InputError


def make_pdf(path, text):
    """A minimal single-page PDF whose text pypdf can read."""
    stream = f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (
        len(objects) + 1,
        xref,
    )
    path.write_bytes(out)
    return path


class Response:
    def __init__(self, payload):
        self._payload = payload
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self):
        return self._payload


class FakeHttp:
    """Serves canned responses by URL; unknown URLs fail like a real request would."""

    def __init__(self, routes):
        self.routes = routes
        self.requested = []

    def get(self, url, **kwargs):
        self.requested.append(url)
        if url not in self.routes:
            raise InputError(f"unexpected url {url}")
        return Response(self.routes[url])


class ScriptedLLM:
    """Always replies with the same JSON."""

    def __init__(self, **reply):
        self.reply = reply
        self.questions = []

    def complete(self, system, user, *, temperature=0.0):
        self.questions.append(user)
        return json.dumps(self.reply)

    def embed(self, texts):
        raise RuntimeError("no embeddings")


class RoutedLLM:
    """Replies according to a phrase found in the question line."""

    def __init__(self, routes):
        self.routes = routes
        self.questions = []

    def complete(self, system, user, *, temperature=0.0):
        question = user.split("\n", 1)[0]
        self.questions.append(question)
        for phrase, reply in self.routes:
            if phrase in question:
                return json.dumps(reply)
        return json.dumps({"value": None})

    def embed(self, texts):
        raise RuntimeError("no embeddings")
