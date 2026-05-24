import hashlib
import hmac
import json
import logging
import os
import subprocess

from flask import Flask, abort, request

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"].encode()
REPO_DIR = os.path.expanduser("~/apps/leaf-by-leaf")
SERVICE = "leaf-by-leaf"


def verify_signature(payload: bytes, sig_header: str) -> bool:
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header)


def git_pull() -> str:
    result = subprocess.run(
        ["git", "pull"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    logging.info("git pull: %s", result.stdout.strip())
    if result.returncode != 0:
        logging.error("git pull failed: %s", result.stderr.strip())
    return result.stdout


def restart_service() -> None:
    logging.info("Restarting %s", SERVICE)
    subprocess.run(["sudo", "systemctl", "restart", SERVICE], check=True)


@app.route("/webhook", methods=["POST"])
def webhook():
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not verify_signature(request.data, sig):
        abort(403)

    event = request.headers.get("X-GitHub-Event", "")
    if event != "push":
        return "ok", 200

    payload = request.get_json()
    if not payload:
        abort(400)

    branch = payload.get("ref", "")
    if branch != "refs/heads/master":
        return "ignored non-master push", 200

    git_pull()
    restart_service()

    logging.info("Deployed %s", SERVICE)
    return json.dumps({"deployed": SERVICE}), 200


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=9001)
