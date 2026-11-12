"""A stand-in experience app: a small service that sends each question to the experience adapter.

    EXPERIENCE_SENDER_ID=<your EXPERIENCE_SUBSCRIBER_ID> python3 experience_stub.py
    curl -N localhost:8070/ask -d '{"question": "Will it rain this week?"}'

EXPERIENCE_ADAPTER_URL says where the experience adapter is (http://localhost:9203/dss
when unset) and EXPERIENCE_STUB_PORT where this listens (8070). The answer is an
event stream; send `Accept: application/json` for a single JSON body instead.
"""

import datetime, json, logging, os, sys, urllib.error, urllib.request, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import NamedTuple

log = logging.getLogger("experience-stub")

DEFAULT_ADAPTER_URL = "http://localhost:9203/dss"
DEFAULT_PORT = 8070
DEFAULT_QUESTION = "What is the weather forecast for my farm?"
TURN_TIMEOUT_SECONDS = 200
STREAM = "text/event-stream"
JSON = "application/json"


class Settings(NamedTuple):
    adapter_url: str
    sender_id: str


def settings_from_env(env):
    if not env.get("EXPERIENCE_SENDER_ID"):
        sys.exit("EXPERIENCE_SENDER_ID is not set: use your EXPERIENCE_SUBSCRIBER_ID")
    return Settings(adapter_url=env.get("EXPERIENCE_ADAPTER_URL", DEFAULT_ADAPTER_URL),
                    sender_id=env["EXPERIENCE_SENDER_ID"])


def build_turn(sender_id, question):
    return {
        "context": {
            "id": "api.dss.turn",
            "version": "2.0.0",
            "transactionId": str(uuid.uuid4()),
            "messageId": str(uuid.uuid4()),
            "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sessionId": "experience-stub",
            "senderId": sender_id,
        },
        "message": {
            "input": [{"role": "user", "content": [{"type": "text", "text": question}]}],
            "attributes": {"sourceLanguage": "en", "targetLanguage": "en", "channel": "web"},
        },
    }


def post_turn(base_url, turn, accept):
    request = urllib.request.Request(
        f"{base_url}/v1/turns", data=json.dumps(turn).encode(), method="POST",
        headers={"Content-Type": JSON, "Accept": accept})
    try:
        return urllib.request.urlopen(request, timeout=TURN_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as refusal:
        return refusal


def accept_for(caller_accept):
    return JSON if JSON in (caller_accept or "") else STREAM


def question_from(body):
    asked = body.get("question", DEFAULT_QUESTION)
    if not isinstance(asked, str):
        raise ValueError("question must be text")
    return asked


def make_server(settings, port=DEFAULT_PORT):
    class Handler(BaseHTTPRequestHandler):
        def refuse(self, status, message):
            body = json.dumps({"error": message}).encode()
            self.send_response(status)
            self.send_header("Content-Type", JSON)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def relay(self, answer):
            self.send_response(answer.status)
            self.send_header("Content-Type", answer.headers.get("Content-Type", JSON))
            self.end_headers()
            for line in answer:
                self.wfile.write(line)
                self.wfile.flush()

        def do_POST(self):
            if self.path != "/ask":
                return self.refuse(404, "not found: ask with POST /ask")
            try:
                question = question_from(json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}"))
            except (ValueError, AttributeError) as bad:
                return self.refuse(400, f"send JSON like {{\"question\": \"...\"}}: {bad}")
            turn, accept = build_turn(settings.sender_id, question), accept_for(self.headers.get("Accept"))
            transaction = turn["context"]["transactionId"]
            log.info("turn %s: sending to %s as %s, asking for %s", transaction, settings.adapter_url,
                     settings.sender_id, accept)
            try:
                answer = post_turn(settings.adapter_url, turn, accept)
            except urllib.error.URLError as unreachable:
                log.warning("turn %s: cannot reach %s: %s", transaction, settings.adapter_url, unreachable.reason)
                return self.refuse(502, f"cannot reach {settings.adapter_url}: {unreachable.reason}")
            log.info("turn %s: the experience adapter answered %s", transaction, answer.status)
            self.relay(answer)
            answer.close()

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


def main(env):
    settings = settings_from_env(env)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    server = make_server(settings, int(env.get("EXPERIENCE_STUB_PORT", DEFAULT_PORT)))
    log.info("stand-in experience app on :%d, sending turns to %s as %s",
             server.server_port, settings.adapter_url, settings.sender_id)
    server.serve_forever()


if __name__ == "__main__":
    main(os.environ)
