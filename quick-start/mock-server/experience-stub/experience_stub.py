"""A stand-in experience app: sends one turn through the experience adapter and prints the answer.

    EXPERIENCE_SENDER_ID=<your EXPERIENCE_SUBSCRIBER_ID> python3 experience_stub.py [--stream] "question"

EXPERIENCE_ADAPTER_URL says where the experience adapter is. It is
http://localhost:9203/dss when unset.
"""

import argparse, datetime, json, os, sys, urllib.error, urllib.request, uuid

DEFAULT_ADAPTER_URL = "http://localhost:9203/dss"
DEFAULT_QUESTION = "What is the weather forecast for my farm?"
TURN_TIMEOUT_SECONDS = 200


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
        headers={"Content-Type": "application/json", "Accept": accept})
    try:
        return urllib.request.urlopen(request, timeout=TURN_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as refusal:
        return refusal


def show(response, out):
    print(f"HTTP {response.status}", file=out)
    for line in response:
        print(line.decode().rstrip("\n"), file=out, flush=True)


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Send one turn through the experience adapter.")
    parser.add_argument("--stream", action="store_true", help="ask for an event stream, not one JSON body")
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    return parser.parse_args(argv)


def main(argv, env, out):
    args = parse_args(argv)
    sender_id = env.get("EXPERIENCE_SENDER_ID")
    if not sender_id:
        sys.exit("EXPERIENCE_SENDER_ID is not set: use your EXPERIENCE_SUBSCRIBER_ID")
    accept = "text/event-stream" if args.stream else "application/json"
    url = env.get("EXPERIENCE_ADAPTER_URL", DEFAULT_ADAPTER_URL)
    try:
        response = post_turn(url, build_turn(sender_id, args.question), accept)
    except urllib.error.URLError as unreachable:
        sys.exit(f"cannot reach {url}: {unreachable.reason}")
    show(response, out)
    response.close()
    return 0 if response.status == 200 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.environ, sys.stdout))
