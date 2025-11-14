# frontend-service/main.py
import os
import socket
import sys
import json
import logging
import traceback
from flask import Flask, request, jsonify, g, send_from_directory
from google.cloud import pubsub_v1, secretmanager
import pg8000.dbapi  # PostgreSQL database driver
from dotenv import load_dotenv
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import time
from werkzeug.exceptions import HTTPException, NotFound

load_dotenv()

app = Flask(__name__)

# ---- Logging to stdout (Cloud Run picks this up) ----
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
)
# Make Flask logger use root handlers (stdout)
app.logger.handlers = logging.getLogger().handlers
app.logger.setLevel(logging.INFO)
# Show werkzeug access logs too
logging.getLogger("werkzeug").setLevel(logging.INFO)

app.logger.info("Service starting up")

def test_db_ip_connectivity():
    app.logger.info(f"Attempting raw TCP connection to {DB_HOST}:{DB_PORT}...")
    try:
        # Create a TCP/IP socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5) # Set a timeout for the connection attempt (5 seconds)

        # Connect to the server
        start_time = time.time()
        sock.connect((DB_HOST, DB_PORT))
        end_time = time.time()

        app.logger.info(f"Successfully connected to {DB_HOST}:{DB_PORT} in {end_time - start_time:.2f} seconds!")
    except socket.timeout:
        app.logger.info(f"Connection to {DB_HOST}:{DB_PORT} timed out after 5 seconds.")
        app.logger.info("This indicates a network routing issue or the host/port is not reachable.")
        return False
    except ConnectionRefusedError:
        app.logger.info(f"Connection to {DB_HOST}:{DB_PORT} was actively refused.")
        app.logger.info("This indicates a firewall blocking the connection on the target, or the service is not listening.")
        return False
    except Exception as e:
        app.logger.info(f"An unexpected error occurred during TCP connection: {e}")
        return False
    finally:
        sock.close()
    return True

# ---- Config flags ----
DEBUG_ERRORS = os.getenv("DEBUG_ERRORS", "false").lower() == "true"

# ---- Correlation / request context ----
@app.before_request
def inject_request_context():
    # Use Cloud Run / GFE trace header if present, else fallback
    trace_hdr = request.headers.get("X-Cloud-Trace-Context", "")
    # Format: TRACE_ID/SPAN_ID;o=TRACE_TRUE
    req_id = trace_hdr.split("/", 1)[0] or request.headers.get("X-Request-Id") or os.urandom(8).hex()
    g.request_id = req_id

@app.after_request
def add_response_headers(resp):
    # echo back request id for clients
    if hasattr(g, "request_id"):
        resp.headers["X-Request-Id"] = g.request_id
    return resp

# Global error handler (catches anything not caught above)
from werkzeug.exceptions import HTTPException, NotFound

@app.errorhandler(Exception)
def handle_uncaught(e):
    # Let HTTP exceptions pass through with appropriate status (avoid noisy ERROR logs)
    if isinstance(e, HTTPException):
        # Optional: special-case /metrics to be quiet
        if isinstance(e, NotFound) and request.path == "/metrics":
            return jsonify({"error": "not found"}), 404
        # For other HTTP errors, return JSON without logging as exception
        status = e.code or 500
        return jsonify(_err_payload(e, status)), status

    # Non-HTTP exceptions: log full traceback
    _log_exception(
        "Uncaught exception",
        request_id=getattr(g, "request_id", None),
        path=request.path
    )
    return jsonify(_err_payload(e, 500)), 500


def _err_payload(e: Exception, status: int):
    payload = {
        "error": str(e),
        "type": f"{e.__class__.__module__}.{e.__class__.__name__}",
        "status": status,
        "request_id": getattr(g, "request_id", None),
    }
    if DEBUG_ERRORS:
        payload["traceback"] = traceback.format_exc()
    return payload

def _log_exception(msg: str, **ctx):
    # Full traceback to logs, plus structured context
    ctx_str = json.dumps(ctx, default=str)
    app.logger.exception("%s | context=%s", msg, ctx_str)

# --- Environment Variables ---
PROJECT_ID = os.environ.get("PROJECT_ID")
DB_HOST = os.environ.get("DB_HOST")
DB_USER = os.environ.get("DB_USER")
DB_NAME = os.environ.get("DB_NAME")
DB_PORT = os.environ.get("DB_PORT")
DB_SECRET_ID = os.environ.get("DB_SECRET_ID")

if not all([PROJECT_ID, DB_HOST, DB_USER, DB_NAME, DB_SECRET_ID]):
    raise RuntimeError("Missing one or more required environment variables for Frontend Service.")

# --- Google Cloud Clients ---
# publisher = pubsub_v1.PublisherClient()
# topic_path = publisher.topic_path(PROJECT_ID, PUBSUB_TOPIC_ID)
secret_client = secretmanager.SecretManagerServiceClient()

# --- Database Connection (Helper Functions) ---
def get_db_password():
    """Retrieves the database password from Secret Manager."""
    try:
        secret_version_path = secret_client.secret_version_path(PROJECT_ID, DB_SECRET_ID, "latest")
        response = secret_client.access_secret_version(request={"name": secret_version_path})
        pw = response.payload.data.decode("UTF-8")
        return pw
    except Exception as e:
        _log_exception("get_db_password failed", request_id=getattr(g, "request_id", None))
        return "None"

def get_db_connection():
    """Establishes and returns a PostgreSQL database connection."""
    db_password = get_db_password()
    app.logger.info("Opening DB connection to %s | req=%s", DB_HOST, getattr(g, "request_id", None))
    try:
        return pg8000.dbapi.connect(
            host=DB_HOST,
            user=DB_USER,
            password=db_password,
            database=DB_NAME,
            timeout=10,
            port=DB_PORT
        )
    except Exception as e:
        _log_exception(f"get_db_connection failed {DB_HOST}:{DB_PORT} {DB_USER} {DB_NAME}", request_id=getattr(g, "request_id", None))
        raise e


def submit_job(admin:bool = False):
    app.logger.info("Received /submit | req=%s", g.request_id)
    test_db_ip_connectivity()

    def is_worker_busy(conn) -> bool:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT is_busy FROM worker_state WHERE id = 'singleton'")
            result = cursor.fetchone()
            return result and result[0]
        finally:
            cursor.close()

    if is_worker_busy(get_db_connection()):
        app.logger.warning("Worker is busy | req=%s", g.request_id)
        return jsonify({"error": "Worker is busy", "request_id": g.request_id}), 503

    client_payload = request.get_json(silent=True)
    if client_payload is None:
        app.logger.warning("Invalid JSON or missing Content-Type | req=%s", g.request_id)
        return jsonify({"error": "Request body must be valid JSON with Content-Type: application/json", "request_id": g.request_id}), 400
    if not isinstance(client_payload, dict):
        app.logger.warning("JSON body not an object | req=%s", g.request_id)
        return jsonify({"error": "JSON body must be an object", "request_id": g.request_id}), 400
    if not admin:
        client_payload["model"] ="gemini-2.5-flash-lite"
    job_id = f"job_{os.urandom(8).hex()}"
    app.logger.info("Creating job_id=%s | req=%s", job_id, g.request_id)

    message_for_pubsub = {
        "job_id": job_id,
        "client_payload": client_payload
    }
    message_data = json.dumps(message_for_pubsub).encode("utf-8")

    if len(message_data) > 9_500_000:
        app.logger.warning("Payload too large: %s bytes | job=%s req=%s", len(message_data), job_id, g.request_id)
        return jsonify({"error": "Payload too large for Pub/Sub message (max 10MB)", "request_id": g.request_id}), 413

    conn = None
    try:
        app.logger.info("Creating Connection")
        conn = get_db_connection()
        app.logger.info("Getting Cursor")
        cursor = conn.cursor()
        app.logger.info("Executing")
        cursor.execute(
            "INSERT INTO jobs (job_id, status, client_request_data) VALUES (%s, %s, %s)",
            (job_id, "PENDING", json.dumps(client_payload))
        )
        conn.commit()
        app.logger.info("Job %s inserted into DB as PENDING | req=%s", job_id, g.request_id)

    except Exception as e:
        _log_exception("Error during job submission", job_id=job_id, request_id=g.request_id)
        if conn:
            try:
                conn.rollback()
            except Exception as rb_e:
                _log_exception("Rollback failed", job_id=job_id, request_id=g.request_id)
        return jsonify(_err_payload(e, 500)), 500
    finally:
        if conn:
            try:
                conn.close()
                app.logger.info("DB connection closed | job=%s req=%s", job_id, g.request_id)
            except Exception as close_e:
                _log_exception("DB close failed", job_id=job_id, request_id=g.request_id)

    return jsonify({
        "job_id": job_id,
        "message": "Job submitted successfully. Check status via /status/{job_id}",
        "request_id": g.request_id
    }), 202


test_db_ip_connectivity()

# --- Flask App Routes ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
@app.route("/", methods=["GET"])
def root_page():
    # serves ./index.html with proper headers
    return send_from_directory(
        directory=BASE_DIR,
        path="index.html",
        mimetype="text/html; charset=utf-8",
        max_age=0  # disable caching while iterating; bump later if needed
    )
@app.route("/health", methods=["GET"])
def health():
    test_db_ip_connectivity()
    app.logger.info("Health check | req=%s", g.request_id)
    return jsonify({"status": "ok", "request_id": g.request_id}), 200

@app.route("/submit", methods=["POST"])
def route_a():
    return submit_job(admin=False)

@app.route("/status/<job_id>", methods=["GET"])
def get_job_status(job_id):
    app.logger.info("Received /status | job=%s req=%s", job_id, g.request_id)
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status, result_url, error_message, created_at, updated_at FROM jobs WHERE job_id = %s",
            (job_id,)
        )
        result = cursor.fetchone()

        if result:
            status, result_url, error_message, created_at, updated_at = result
            app.logger.info("Status for %s: %s | req=%s", job_id, status, g.request_id)
            return jsonify({
                "job_id": job_id,
                "status": status,
                "result_url": result_url,
                "error_message": error_message,
                "created_at": created_at.isoformat() if created_at else None,
                "updated_at": updated_at.isoformat() if updated_at else None,
                "request_id": g.request_id
            }), 200
        else:
            app.logger.warning("Job not found: %s | req=%s", job_id, g.request_id)
            return jsonify({"error": "Job not found", "request_id": g.request_id}), 404
    except Exception as e:
        _log_exception("Error fetching status", job_id=job_id, request_id=g.request_id)
        return jsonify(_err_payload(e, 500)), 500
    finally:
        if conn:
            try:
                conn.close()
                app.logger.info("DB connection closed for status | job=%s req=%s", job_id, g.request_id)
            except Exception as close_e:
                _log_exception("DB close failed (status)", job_id=job_id, request_id=g.request_id)

if __name__ == "__main__":
    # Dev-only: Cloud Run uses gunicorn via CMD
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
