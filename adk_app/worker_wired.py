#!/usr/bin/env python3
import os
import sys
import time
import json
import logging
import signal
import zipfile
import io
from datetime import timedelta, datetime
from dotenv import load_dotenv
load_dotenv()

import pg8000.dbapi
from google.cloud import storage, secretmanager
from google.api_core.exceptions import NotFound as GcpNotFound, DeadlineExceeded
from google.oauth2 import service_account
from google.auth import default as google_auth_default
from classes.pipeline.bk_governor_wired import main as pipeline_main

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(message)s',
)
log = logging.getLogger("worker")

class JobWorker:
    """
    Class-based refactor of the original script.
    Encapsulates config, clients, state, and the main loop.
    """
    def __init__(self) -> None:
        # ---------- Config via env ----------
        self.PROJECT_ID          = os.environ["GOOGLE_CLOUD_PROJECT"]
        self.BUCKET_NAME         = os.environ.get("GCS_BUCKET_NAME", "llm-app-results-bucket")

        self.DB_HOST             = os.environ["DB_HOST"]
        self.DB_PORT             = int(os.environ.get("DB_PORT", "5432"))
        self.DB_NAME             = os.environ["DB_NAME"]
        self.DB_USER             = os.environ["DB_USER"]
        self.DB_PASSWORD         = os.environ.get("DB_PASSWORD")
        self.DB_SECRET_ID        = os.environ.get("DB_SECRET_ID")

        self.POLL_SLEEP_SECONDS  = float(os.environ.get("POLL_SLEEP_SECONDS", "0.5"))
        self.PROCESS_SIM_STEPS   = int(os.environ.get("PROCESS_SIM_STEPS", "6"))
        self.STEP_SLEEP_SECONDS  = float(os.environ.get("STEP_SLEEP_SECONDS", "10.0"))

        # ---------- State ----------
        self.busy = False
        self.shutdown = False

        # ---------- Credentials & clients ----------
        self.bucket_creds = self._build_creds()
        try:
            sa_email = getattr(self.bucket_creds, "service_account_email", None)
            log.info("******************************\nUsing credentials for service account: %s \n******************************", sa_email or "(unknown)")
        except Exception:
            log.info("******************************\nUNABLE TO RETRIEVE CREDENTIALS\n******************************")
            raise

        self.storage_client = storage.Client(credentials=self.bucket_creds)
        self.secret_client = (
            secretmanager.SecretManagerServiceClient(credentials=self.bucket_creds)
            if self.DB_PASSWORD is None and self.DB_SECRET_ID else None
        )

        # ---------- Signals ----------
        signal.signal(signal.SIGINT, self._graceful_shutdown)
        signal.signal(signal.SIGTERM, self._graceful_shutdown)

    # ---------- Credentials ----------
    def _build_creds(self):
        key_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        scopes = ["https://www.googleapis.com/auth/cloud-platform"]
        if key_path and os.path.exists(key_path):
            return service_account.Credentials.from_service_account_file(key_path, scopes=scopes)
        creds, _ = google_auth_default(scopes=scopes)
        return creds

    # ---------- DB helpers ----------
    def _get_db_password(self) -> str:
        if self.DB_PASSWORD:
            return self.DB_PASSWORD
        if self.secret_client and self.DB_SECRET_ID:
            name = self.secret_client.secret_version_path(self.PROJECT_ID, self.DB_SECRET_ID, "latest")
            resp = self.secret_client.access_secret_version(request={"name": name})
            self.DB_PASSWORD = resp.payload.data.decode("utf-8")
            return self.DB_PASSWORD
        raise RuntimeError("No DB_PASSWORD and no Secret Manager configured")

    def db_connect(self):
        pw = self._get_db_password()
        conn = pg8000.dbapi.connect(
            host=self.DB_HOST, port=self.DB_PORT,
            user=self.DB_USER, password=pw,
            database=self.DB_NAME,
            timeout=10,
        )
        conn.autocommit = True
        return conn


    def test_db_connectivity(self) -> None:
        try:
            conn = self.db_connect()
            cur = conn.cursor()
            try:
                cur.execute("SELECT 1")
                _ = cur.fetchone()
            finally:
                try:
                    cur.close()
                finally:
                    conn.close()
            log.info("DB connectivity OK to %s:%d db=%s as %s", self.DB_HOST, self.DB_PORT, self.DB_NAME, self.DB_USER)
        except Exception:
            log.exception("DB connectivity test FAILED to %s:%d db=%s as %s", self.DB_HOST, self.DB_PORT, self.DB_NAME, self.DB_USER)
            sys.exit(3)

    def update_job_status(self, conn, job_id: str, status: str, error_message: str | None = None, result_url: str | None = None) -> int:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                UPDATE jobs
                   SET status = %s,
                       error_message = %s,
                       result_url = %s,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE job_id = %s
                """,
                (status, error_message, result_url, job_id),
            )
            conn.commit()
            return cur.rowcount or 0
        finally:
            cur.close()

    def set_worker_busy(self, conn, busy: bool) -> None:
        cur = conn.cursor()
        try:
            cur.execute(
                "UPDATE worker_state SET is_busy = %s, last_updated = CURRENT_TIMESTAMP WHERE id = 'singleton'",
                (busy,)
            )
            conn.commit()
        finally:
            cur.close()

    def fetch_job_exists(self, conn, job_id: str) -> bool:
        cur = conn.cursor()
        try:
            cur.execute("SELECT 1 FROM jobs WHERE job_id = %s LIMIT 1", (job_id,))
            row = cur.fetchone()
            return row is not None
        finally:
            cur.close()

    # ---------- Storage helpers ----------

    def _upload_to_gcs(self, bucket_name: str, blob_path: str, data: bytes, content_type: str = "application/zip"):
        bucket = self.storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{bucket_name}/{blob_path}", f"https://storage.googleapis.com/{bucket_name}/{blob_path}"

    def _generate_signed_url(self, bucket_name: str, blob_path: str, expires_in_seconds: int = 3600) -> str | None:
        try:
            bucket = self.storage_client.bucket(bucket_name)
            blob = bucket.blob(blob_path)
            return blob.generate_signed_url(
                version="v4",
                expiration=timedelta(seconds=expires_in_seconds),
                method="GET",
                credentials=self.bucket_creds,
            )
        except Exception as e:
            log.warning("Could not generate signed URL (non-fatal): %s", e)
            return None

    # ---------- Job processing ----------
    def handle_message(self, job_id: str, client_payload: dict):
        self.busy = True
        conn = None
        try:
            conn = self.db_connect()
            if not self.fetch_job_exists(conn, job_id):
                log.warning("Job %s not found in DB; marking error", job_id)
                return "ERROR", "Job missing in DB", None

            self.set_worker_busy(conn, True)
            self.update_job_status(conn, job_id, status="RUNNING")
            log.info("Job %s marked RUNNING", job_id)

            def _db_status_sink(current_value, completion_value, status_message):
                try:
                    cv = int(current_value) if current_value is not None else 0
                    mv = int(completion_value) if completion_value not in (None, 0) else 0
                    pct = 0 if mv == 0 else int(100 * min(cv, mv) / mv)
                except Exception:
                    pct = 0
                status_text = f"{pct}% {status_message}" if status_message else f"{pct}%"
                try:
                    log.info("Job %s - %s", job_id, status_text)
                    self.update_job_status(conn, job_id, status=status_text)
                except Exception:
                    log.exception("Failed to persist intermediate status for job %s", job_id)

            problem_statement = (client_payload or {}).get("text", "")
            model_name = (client_payload or {}).get("model", "")

            result = pipeline_main (
                problem_statement = problem_statement,
                model = model_name,
                run_id = job_id,
                status_event_sink= _db_status_sink,
                max_retries = 3,
            )
            if result["status"] == "success":
                object_path = f"results/{job_id}/{job_id}.zip"
                gs_url, https_url = self._upload_to_gcs(self.BUCKET_NAME, object_path, result["zip_bytes"])
                signed_url = self._generate_signed_url(self.BUCKET_NAME, object_path, 3600)
                final_url = signed_url or gs_url
                log.info("Uploaded artifact: gs=%s, https=%s, signed=%s", gs_url, https_url, bool(signed_url))
                self.update_job_status(conn, job_id, status="COMPLETED", result_url=final_url)
                log.info("Job %s COMPLETED with result_url=%s", job_id, final_url)
                return "COMPLETED", None, final_url

            else:
                log.exception("Job %s failed", job_id)
                try:
                    if conn:
                        self.update_job_status(conn, job_id, status="ERROR")
                except Exception:
                    log.exception("Failed to persist ERROR state for job %s", job_id)
                return "ERROR", None, None
        except Exception as e:
            log.exception("Job %s failed", job_id)
            try:
                if conn:
                    self.update_job_status(conn, job_id, status="ERROR", error_message=str(e))
            except Exception:
                log.exception("Failed to persist ERROR state for job %s", job_id)
            return "ERROR", str(e), None
        finally:
            try:
                if conn:
                    conn.close()
            finally:
                self.busy = False
    # ---------- Worker locking & queue ----------
    def acquire_worker_lock(self, conn) -> bool:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO worker_state (id, is_busy, last_updated)
                VALUES ('singleton', TRUE, CURRENT_TIMESTAMP)
                ON CONFLICT (id) DO UPDATE
                SET is_busy = EXCLUDED.is_busy,
                    last_updated = EXCLUDED.last_updated
                WHERE worker_state.is_busy = TRUE
                RETURNING id
                """
            )
            return cur.fetchone() is not None
        finally:
            cur.close()


    def fetch_next_pending_job(self, conn):
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT job_id, client_request_data FROM jobs WHERE status = 'PENDING' ORDER BY created_at ASC LIMIT 1"
            )
            row = cur.fetchone()
            if not row:
                return None
            job_id, client_payload_raw = row
            if isinstance(client_payload_raw, dict):
                client_payload = client_payload_raw
            else:
                client_payload = json.loads(client_payload_raw or "{}")
            return job_id, client_payload
        finally:
            cur.close()

    def release_worker_lock(self, conn) -> None:
        cur = conn.cursor()
        try:
            cur.execute(
                "UPDATE worker_state SET is_busy = FALSE, last_updated = CURRENT_TIMESTAMP WHERE id = 'singleton'"
            )
            conn.commit()
        finally:
            cur.close()

    # ---------- Main loop ----------
    def run(self) -> None:
        log.info("Worker starting. Project=%s Bucket=%s", self.PROJECT_ID, self.BUCKET_NAME)
        log.info("DB target %s:%d db=%s user=%s", self.DB_HOST, self.DB_PORT, self.DB_NAME, self.DB_USER)

        while not self.shutdown:
            try:
                if self.busy:
                    time.sleep(self.POLL_SLEEP_SECONDS)
                    continue

                conn = self.db_connect()
                try:
                    job = self.fetch_next_pending_job(conn)
                    if not job:
                        log.info("No pending jobs found.")
                        self.release_worker_lock(conn)
                        conn.close()
                        time.sleep(self.POLL_SLEEP_SECONDS)
                        continue

                    self.acquire_worker_lock(conn)
                    conn.close()

                    job_id, client_payload = job
                    log.info("Picked up job %s; processing...", job_id)
                    status, err, url = self.handle_message(job_id, client_payload)

                    self.release_worker_lock(conn)
                    conn.close()

                except Exception:
                    log.exception("Job handling failed")
                    try:
                        self.release_worker_lock(conn)
                        conn.close()
                    except Exception:
                        log.exception("Error releasing DB lock")
                    time.sleep(2)
            except KeyboardInterrupt:
                break
            except Exception:
                log.exception("Loop error; sleeping briefly")
                time.sleep(2)

        log.info("Worker stopped.")

    # ---------- Signals ----------
    def _graceful_shutdown(self, signum, frame) -> None:
        log.info("Received signal %s, shutting down loop...", signum)
        self.shutdown = True

def main():
    worker = JobWorker()
    worker.test_db_connectivity()
    worker.run()

if __name__ == "__main__":
    main()
