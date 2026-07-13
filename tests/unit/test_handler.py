"""
Unit tests for src/handler.py — Lambda event boundary layer.

🧪 TEST STRATEGY & MATRIX
--------------------------
Framework : pytest + unittest.mock (no real AWS calls).
Strategy  :
  - handler._s3_client is replaced per-test via the ``mock_s3`` conftest
    fixture (monkeypatch), so every AWS interaction is fully controlled.
  - handler.process_image is patched in handler tests to return a fixed
    bytes payload, keeping these tests independent of PIL behaviour.
  - SQS event payloads are built via the mocks/sqs_events.py helpers.

Coverage targets
  lambda_handler
    - Happy path: 1 record, N records, empty batch
    - Payload routing: correct bucket/key, output prefix, ContentType
    - Bucket mismatch guard (confused-deputy attack prevention)
    - Malformed JSON body, missing nested keys
    - S3 GetObject failure (ClientError)
    - Oversized file rejection (> 20 MB, exactly 20 MB accepted)
    - S3 PutObject failure (ClientError)
    - Partial batch failure → RuntimeError for SQS retry / DLQ routing
    - URL-encoded keys decoded via unquote_plus

  presigned_url_handler
    - Allowlisted extensions (.jpg, .jpeg, .png, .webp)
    - Rejected extensions (.gif, .bmp, .pdf, .exe, no-extension)
    - Case-insensitive extension check
    - Path traversal sanitisation via os.path.basename
    - Null / missing request body default behaviour
    - ExpiresIn=300 verified in presigned URL generation call
    - S3 ClientError → HTTP 500

⚠️ EDGE CASES & VULNERABILITY VECTORS
---------------------------------------
  - Bucket mismatch injects arbitrary bucket names via SQS message body
    (confused-deputy attack on s3:GetObject)
  - Path traversal in presigned URL filename (../../etc/passwd)
  - Files just below / at / above the 20 MB size ceiling
  - Partial batch failure must raise RuntimeError so SQS retries and routes
    unprocessable records to the DLQ
  - process_image must NOT be called for oversized or rejected inputs
"""

import json
import os
from unittest.mock import MagicMock, call, patch

import pytest
from botocore.exceptions import ClientError

# conftest.py sets env vars and patches boto3 before this import.
import handler

# Minimal fake JPEG payload returned by the process_image mock.
_FAKE_PROCESSED = b"\xFF\xD8\xFF\xE0" + b"\x00" * 64


# ── Helpers ───────────────────────────────────────────────────────────────────

def _client_error(code: str = "NoSuchKey", op: str = "GetObject") -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": code, "Message": "simulated error"}},
        operation_name=op,
    )


def _sqs_event(bucket: str, key: str, num_records: int = 1) -> dict:
    body = json.dumps({
        "Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]
    })
    return {"Records": [{"body": body} for _ in range(num_records)]}


def _good_s3_response(data: bytes = b"x" * 512) -> dict:
    return {"Body": MagicMock(read=MagicMock(return_value=data))}


_SOURCE = os.environ["SOURCE_BUCKET"]
_PROCESSED = os.environ["PROCESSED_BUCKET"]


# ────────────────────────────────────────────────────────────────────────────
# lambda_handler — happy paths
# ────────────────────────────────────────────────────────────────────────────

class TestLambdaHandlerSuccess:

    @pytest.fixture(autouse=True)
    def _patch_process(self):
        """Stub out process_image so these tests are independent of PIL."""
        with patch.object(handler, "process_image", return_value=_FAKE_PROCESSED) as m:
            self.process_mock = m
            yield

    def test_single_record_returns_status_200(self, mock_s3):
        mock_s3.get_object.return_value = _good_s3_response()
        mock_s3.put_object.return_value = {}

        result = handler.lambda_handler(_sqs_event(_SOURCE, "photo.jpg"), None)

        assert result["statusCode"] == 200

    def test_single_record_body_reports_one_processed(self, mock_s3):
        mock_s3.get_object.return_value = _good_s3_response()
        mock_s3.put_object.return_value = {}

        result = handler.lambda_handler(_sqs_event(_SOURCE, "photo.jpg"), None)

        assert "1" in result["body"]

    def test_three_records_all_processed(self, mock_s3):
        mock_s3.get_object.return_value = _good_s3_response()
        mock_s3.put_object.return_value = {}

        handler.lambda_handler(_sqs_event(_SOURCE, "photo.jpg", num_records=3), None)

        assert mock_s3.get_object.call_count == 3
        assert mock_s3.put_object.call_count == 3

    def test_empty_records_batch_returns_200(self, mock_s3):
        result = handler.lambda_handler({"Records": []}, None)

        assert result["statusCode"] == 200
        mock_s3.get_object.assert_not_called()

    def test_missing_top_level_records_key_returns_200(self, mock_s3):
        """event.get('Records', []) default — no records means 0 processed."""
        result = handler.lambda_handler({}, None)

        assert result["statusCode"] == 200

    def test_output_key_is_prefixed_with_processed(self, mock_s3):
        mock_s3.get_object.return_value = _good_s3_response()
        mock_s3.put_object.return_value = {}

        handler.lambda_handler(_sqs_event(_SOURCE, "images/sunset.jpg"), None)

        put_kwargs = mock_s3.put_object.call_args.kwargs
        assert put_kwargs["Key"] == "processed/images/sunset.jpg"

    def test_put_object_content_type_is_image_jpeg(self, mock_s3):
        mock_s3.get_object.return_value = _good_s3_response()
        mock_s3.put_object.return_value = {}

        handler.lambda_handler(_sqs_event(_SOURCE, "photo.jpg"), None)

        assert mock_s3.put_object.call_args.kwargs["ContentType"] == "image/jpeg"

    def test_put_object_targets_processed_bucket(self, mock_s3):
        mock_s3.get_object.return_value = _good_s3_response()
        mock_s3.put_object.return_value = {}

        handler.lambda_handler(_sqs_event(_SOURCE, "photo.jpg"), None)

        assert mock_s3.put_object.call_args.kwargs["Bucket"] == _PROCESSED

    def test_source_bucket_never_written_to(self, mock_s3):
        """
        Architectural guardrail: PutObject must NEVER target the source bucket.
        A write back to source would trigger an infinite SQS→Lambda loop.
        """
        mock_s3.get_object.return_value = _good_s3_response()
        mock_s3.put_object.return_value = {}

        handler.lambda_handler(_sqs_event(_SOURCE, "photo.jpg"), None)

        for c in mock_s3.put_object.call_args_list:
            assert c.kwargs["Bucket"] != _SOURCE

    def test_url_plus_encoded_key_decoded(self, mock_s3):
        """
        S3 event notifications encode spaces as +.
        unquote_plus must decode them back to spaces before the GetObject call.
        """
        mock_s3.get_object.return_value = _good_s3_response()
        mock_s3.put_object.return_value = {}

        handler.lambda_handler(_sqs_event(_SOURCE, "my+photo.jpg"), None)

        get_key = mock_s3.get_object.call_args.kwargs["Key"]
        assert get_key == "my photo.jpg"

    def test_url_percent_encoded_key_decoded(self, mock_s3):
        """
        Keys with percent-encoding (%20) must also be decoded correctly.
        """
        mock_s3.get_object.return_value = _good_s3_response()
        mock_s3.put_object.return_value = {}

        handler.lambda_handler(_sqs_event(_SOURCE, "my%20photo.jpg"), None)

        get_key = mock_s3.get_object.call_args.kwargs["Key"]
        assert get_key == "my photo.jpg"

    def test_process_image_receives_raw_bytes(self, mock_s3):
        """process_image must be called with the exact bytes from S3."""
        raw = b"fake-image-data-bytes"
        mock_s3.get_object.return_value = _good_s3_response(raw)
        mock_s3.put_object.return_value = {}

        handler.lambda_handler(_sqs_event(_SOURCE, "photo.jpg"), None)

        self.process_mock.assert_called_once_with(raw)

    def test_put_object_body_is_process_image_output(self, mock_s3):
        """Bytes stored in S3 must be the exact output of process_image."""
        mock_s3.get_object.return_value = _good_s3_response()
        mock_s3.put_object.return_value = {}

        handler.lambda_handler(_sqs_event(_SOURCE, "photo.jpg"), None)

        assert mock_s3.put_object.call_args.kwargs["Body"] == _FAKE_PROCESSED

    def test_file_exactly_at_20mb_limit_is_accepted(self, mock_s3):
        """
        The size guard is `> MAX_RAW_BYTES`, so a file of exactly 20 MB
        must NOT be rejected.
        """
        at_limit = b"x" * (20 * 1024 * 1024)
        mock_s3.get_object.return_value = _good_s3_response(at_limit)
        mock_s3.put_object.return_value = {}

        result = handler.lambda_handler(_sqs_event(_SOURCE, "big-ok.jpg"), None)

        assert result["statusCode"] == 200
        self.process_mock.assert_called_once()


# ────────────────────────────────────────────────────────────────────────────
# lambda_handler — failure / rejection paths
# ────────────────────────────────────────────────────────────────────────────

class TestLambdaHandlerFailures:

    @pytest.fixture(autouse=True)
    def _patch_process(self):
        with patch.object(handler, "process_image", return_value=_FAKE_PROCESSED) as m:
            self.process_mock = m
            yield

    def test_bucket_mismatch_raises_runtime_error(self, mock_s3):
        """
        I-03 guardrail: records whose bucket name differs from SOURCE_BUCKET
        must be rejected and must not trigger a GetObject call.
        """
        event = _sqs_event("attacker-controlled-bucket", "evil.jpg")
        with pytest.raises(RuntimeError, match="Failed to process"):
            handler.lambda_handler(event, None)
        mock_s3.get_object.assert_not_called()

    def test_malformed_json_body_raises_runtime_error(self, mock_s3):
        event = {"Records": [{"body": "{not valid json{{"}]}
        with pytest.raises(RuntimeError):
            handler.lambda_handler(event, None)

    def test_body_missing_records_key_raises_runtime_error(self, mock_s3):
        event = {"Records": [{"body": json.dumps({"no_records_here": []})}]}
        with pytest.raises(RuntimeError):
            handler.lambda_handler(event, None)

    def test_body_empty_records_list_raises_runtime_error(self, mock_s3):
        """Inner body Records=[] → IndexError on [0] → caught → RuntimeError."""
        event = {"Records": [{"body": json.dumps({"Records": []})}]}
        with pytest.raises(RuntimeError):
            handler.lambda_handler(event, None)

    def test_s3_get_object_no_such_key_raises_runtime_error(self, mock_s3):
        mock_s3.get_object.side_effect = _client_error("NoSuchKey", "GetObject")
        with pytest.raises(RuntimeError, match="Failed to process"):
            handler.lambda_handler(_sqs_event(_SOURCE, "missing.jpg"), None)

    def test_s3_get_object_access_denied_raises_runtime_error(self, mock_s3):
        mock_s3.get_object.side_effect = _client_error("AccessDenied", "GetObject")
        with pytest.raises(RuntimeError, match="Failed to process"):
            handler.lambda_handler(_sqs_event(_SOURCE, "forbidden.jpg"), None)

    def test_oversized_file_raises_runtime_error(self, mock_s3):
        """I-04: Files > 20 MB must be rejected before reaching process_image."""
        oversized = b"x" * (21 * 1024 * 1024)
        mock_s3.get_object.return_value = _good_s3_response(oversized)

        with pytest.raises(RuntimeError, match="Failed to process"):
            handler.lambda_handler(_sqs_event(_SOURCE, "huge.jpg"), None)

    def test_oversized_file_does_not_invoke_process_image(self, mock_s3):
        """
        process_image must NOT be called on oversized files — passing 20 MB+
        to PIL could exhaust Lambda memory before the guard triggers.
        """
        oversized = b"x" * (21 * 1024 * 1024)
        mock_s3.get_object.return_value = _good_s3_response(oversized)

        with pytest.raises(RuntimeError):
            handler.lambda_handler(_sqs_event(_SOURCE, "huge.jpg"), None)

        self.process_mock.assert_not_called()

    def test_s3_put_object_access_denied_raises_runtime_error(self, mock_s3):
        mock_s3.get_object.return_value = _good_s3_response()
        mock_s3.put_object.side_effect = _client_error("AccessDenied", "PutObject")

        with pytest.raises(RuntimeError, match="Failed to process"):
            handler.lambda_handler(_sqs_event(_SOURCE, "photo.jpg"), None)

    def test_partial_batch_failure_raises_runtime_error(self, mock_s3):
        """
        SQS retry contract: if any record fails, RuntimeError must be raised
        so SQS can re-deliver the batch and eventually route to the DLQ.
        """
        good_resp = _good_s3_response()
        mock_s3.get_object.side_effect = [
            good_resp,
            _client_error("NoSuchKey", "GetObject"),  # record 2 fails
            good_resp,
        ]
        mock_s3.put_object.return_value = {}

        with pytest.raises(RuntimeError, match="Failed to process"):
            handler.lambda_handler(_sqs_event(_SOURCE, "photo.jpg", num_records=3), None)

    def test_partial_batch_still_processes_non_failing_records(self, mock_s3):
        """
        Failing records must not short-circuit the batch — healthy records
        must still be processed so losses are minimised.
        """
        good_resp = _good_s3_response()
        mock_s3.get_object.side_effect = [
            good_resp,
            _client_error("NoSuchKey", "GetObject"),
            good_resp,
        ]
        mock_s3.put_object.return_value = {}

        with pytest.raises(RuntimeError):
            handler.lambda_handler(_sqs_event(_SOURCE, "photo.jpg", num_records=3), None)

        # GetObject attempted for all 3 records; PutObject succeeded for 2.
        assert mock_s3.get_object.call_count == 3
        assert mock_s3.put_object.call_count == 2

    def test_process_image_pil_exception_caught_gracefully(self, mock_s3):
        """
        INF-02 fix: if process_image raises any PIL exception (e.g.
        DecompressionBombError for an image that passes the 20 MB size check
        but detonates inside PIL), the exception must be caught, the key
        appended to failed_keys, and a RuntimeError raised for SQS retry —
        not leaked as an unhandled Lambda invocation failure.
        """
        from PIL.Image import DecompressionBombError

        mock_s3.get_object.return_value = _good_s3_response()
        self.process_mock.side_effect = DecompressionBombError("simulated bomb")

        with pytest.raises(RuntimeError, match="Failed to process"):
            handler.lambda_handler(_sqs_event(_SOURCE, "bomb.jpg"), None)

        # put_object must NOT have been called — no partial write on PIL failure.
        mock_s3.put_object.assert_not_called()

    def test_process_image_generic_exception_caught_gracefully(self, mock_s3):
        """
        INF-02 fix: a generic Exception from process_image (e.g. MemoryError
        on a pathological image) must also be caught and routed to failed_keys.
        """
        mock_s3.get_object.return_value = _good_s3_response()
        self.process_mock.side_effect = MemoryError("simulated OOM")

        with pytest.raises(RuntimeError, match="Failed to process"):
            handler.lambda_handler(_sqs_event(_SOURCE, "oom.jpg"), None)

        mock_s3.put_object.assert_not_called()


# ────────────────────────────────────────────────────────────────────────────
# presigned_url_handler
# ────────────────────────────────────────────────────────────────────────────

_FAKE_PRESIGNED = (
    "https://s3.amazonaws.com/daisy-source-test/photo.jpg"
    "?AWSAccessKeyId=test&Expires=9999&Signature=abc123"
)


class TestPresignedUrlHandler:

    @pytest.fixture(autouse=True)
    def _setup(self, mock_s3):
        mock_s3.generate_presigned_url.return_value = _FAKE_PRESIGNED
        self.s3 = mock_s3

    def _call(self, filename) -> dict:
        body = json.dumps({"filename": filename}) if filename is not None else None
        return handler.presigned_url_handler({"body": body}, None)

    # -- Allowlisted extensions --

    @pytest.mark.parametrize("fname,expected_ct", [
        ("portrait.jpg",  "image/jpeg"),
        ("photo.jpeg",    "image/jpeg"),
        ("diagram.png",   "image/png"),
        ("hero.webp",     "image/webp"),
    ])
    def test_allowed_extension_returns_200(self, fname, expected_ct):
        result = self._call(fname)
        assert result["statusCode"] == 200

    @pytest.mark.parametrize("fname,expected_ct", [
        ("portrait.jpg",  "image/jpeg"),
        ("photo.jpeg",    "image/jpeg"),
        ("diagram.png",   "image/png"),
        ("hero.webp",     "image/webp"),
    ])
    def test_allowed_extension_content_type_mapped(self, fname, expected_ct):
        self._call(fname)
        params = self.s3.generate_presigned_url.call_args.kwargs["Params"]
        assert params["ContentType"] == expected_ct

    @pytest.mark.parametrize("fname", [
        "PHOTO.JPG",
        "IMAGE.JPEG",
        "diagram.PNG",
        "banner.WebP",
    ])
    def test_uppercase_extension_accepted(self, fname):
        """H-05: Extension check uses .lower(); uppercase must pass."""
        result = self._call(fname)
        assert result["statusCode"] == 200

    # -- Rejected extensions --

    @pytest.mark.parametrize("fname", [
        "animation.gif",
        "image.bmp",
        "document.pdf",
        "malware.exe",
        "script.sh",
        "archive.zip",
        "data.json",
        "nodotfile",
    ])
    def test_disallowed_extension_returns_400(self, fname):
        result = self._call(fname)
        assert result["statusCode"] == 400

    def test_disallowed_extension_error_body(self):
        result = self._call("exploit.exe")
        body = json.loads(result["body"])
        assert "error" in body

    # -- Response shape --

    def test_response_contains_upload_url(self):
        result = self._call("photo.jpg")
        body = json.loads(result["body"])
        assert "upload_url" in body

    def test_response_upload_url_matches_presigned_url(self):
        result = self._call("photo.jpg")
        body = json.loads(result["body"])
        assert body["upload_url"] == _FAKE_PRESIGNED

    def test_response_contains_key(self):
        result = self._call("photo.jpg")
        body = json.loads(result["body"])
        assert body["key"] == "photo.jpg"

    # -- Path traversal sanitisation --

    def test_path_traversal_no_extension_rejected(self):
        """../../etc/passwd → basename → 'passwd', no valid ext → 400."""
        result = self._call("../../etc/passwd")
        assert result["statusCode"] == 400

    def test_path_traversal_with_valid_extension_sanitised(self):
        """../../evil.jpg → basename → 'evil.jpg' → 200, key is basename only."""
        result = self._call("../../evil.jpg")
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["key"] == "evil.jpg"
        assert ".." not in body["key"]

    def test_absolute_path_traversal_sanitised(self):
        """/var/task/handler.py → basename → 'handler.py' → no valid ext → 400."""
        result = self._call("/var/task/handler.py")
        assert result["statusCode"] == 400

    # -- Empty / missing body --

    def test_null_body_defaults_to_upload_jpg(self):
        result = handler.presigned_url_handler({"body": None}, None)
        assert result["statusCode"] == 200

    def test_missing_body_key_defaults_to_upload_jpg(self):
        result = handler.presigned_url_handler({}, None)
        assert result["statusCode"] == 200

    def test_empty_filename_after_basename_returns_400(self):
        """'/' → os.path.basename('/') == '' → falsy → 400."""
        result = self._call("/")
        assert result["statusCode"] == 400

    # -- Presigned URL parameters --

    def test_expires_in_is_300_seconds(self):
        self._call("photo.jpg")
        kwargs = self.s3.generate_presigned_url.call_args.kwargs
        assert kwargs["ExpiresIn"] == 300

    def test_presigned_url_operation_is_put_object(self):
        self._call("photo.jpg")
        args = self.s3.generate_presigned_url.call_args.args
        assert args[0] == "put_object"

    def test_presigned_url_bucket_is_source_bucket(self):
        self._call("photo.jpg")
        params = self.s3.generate_presigned_url.call_args.kwargs["Params"]
        assert params["Bucket"] == _SOURCE

    # -- ClientError handling --

    def test_s3_client_error_returns_500(self):
        self.s3.generate_presigned_url.side_effect = _client_error(
            "AccessDenied", "GeneratePresignedUrl"
        )
        result = self._call("photo.jpg")
        assert result["statusCode"] == 500

    def test_s3_client_error_body_contains_error_key(self):
        self.s3.generate_presigned_url.side_effect = _client_error(
            "AccessDenied", "GeneratePresignedUrl"
        )
        body = json.loads(self._call("photo.jpg")["body"])
        assert "error" in body

    def test_malformed_json_body_returns_500(self):
        """
        Regression test for presigned_url_handler JSON decode bug.

        json.JSONDecodeError is NOT a ClientError — it must be explicitly caught
        or it propagates as an unhandled Lambda exception, leaking a stack trace
        to the caller. The fix adds json.JSONDecodeError to the except clause.
        """
        result = handler.presigned_url_handler({"body": "not-valid-json{{"}, None)
        assert result["statusCode"] == 500

    def test_malformed_json_body_error_body_is_json_serialisable(self):
        """Error response must itself be valid JSON — not a raw exception string."""
        result = handler.presigned_url_handler({"body": "{broken"}, None)
        body = json.loads(result["body"])  # raises if body is not valid JSON
        assert "error" in body

    def test_integer_body_returns_500_not_unhandled_exception(self):
        """
        event['body'] = 1 → json.loads(1) raises TypeError.
        The handler must absorb all decode-time exceptions, not propagate them.
        """
        result = handler.presigned_url_handler({"body": 1}, None)
        # Acceptable outcomes: 400, 500. Unacceptable: unhandled exception.
        assert result["statusCode"] in (400, 500)


# ────────────────────────────────────────────────────────────────────────────
# Module-level initialisation assertions
# ────────────────────────────────────────────────────────────────────────────

class TestModuleLevelInit:

    def test_config_source_bucket_equals_env_var(self):
        assert handler._config.source_bucket == os.environ["SOURCE_BUCKET"]

    def test_config_processed_bucket_equals_env_var(self):
        assert handler._config.processed_bucket == os.environ["PROCESSED_BUCKET"]

    def test_s3_client_is_not_none(self):
        """_s3_client must be populated at module load (warm invocation cache)."""
        assert handler._s3_client is not None

    def test_config_is_immutable(self):
        with pytest.raises((AttributeError, TypeError)):
            handler._config.source_bucket = "tamper"  # type: ignore[misc]
