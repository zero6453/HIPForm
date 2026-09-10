"""HTTP access to ANSYS jobs; results retain the service's fidelity labels."""

from pathlib import Path
from urllib.parse import quote

import httpx

from .inputs import validate_step


class AnsysServiceError(RuntimeError):
    """The service request failed or did not return its documented response."""


def _check_response(response, expected_status=200):
    if response.status_code != expected_status:
        detail = response.read()[:400].decode("utf-8", errors="replace").strip()
        raise AnsysServiceError(f"ANSYS HTTP {response.status_code}: {detail}")


def _json(response, expected_type=dict):
    try:
        payload = response.json()
    except ValueError as exc:
        raise AnsysServiceError("ANSYS returned invalid JSON") from exc
    if not isinstance(payload, expected_type):
        label = "object" if expected_type is dict else "array"
        raise AnsysServiceError(f"ANSYS response must be a JSON {label}")
    return payload


def _component(value, label):
    if (not isinstance(value, str) or not value or value == "." or ".." in value
            or any(char in value for char in "/\\%") or not value.isprintable()):
        raise ValueError(f"Invalid {label}")
    return quote(value, safe="")


class AnsysClient:
    """Use an injected HTTP client without taking ownership of its lifetime."""

    def __init__(self, base_url: str, *, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(timeout=60)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self._owns_client:
            self._client.close()

    def upload_step(self, path: Path) -> str:
        path = validate_step(path)
        with path.open("rb") as stream:
            response = self._request("POST", "/uploads",
                files={"file": (path.name, stream, "application/octet-stream")})
        uploaded = _json(response)
        if (not isinstance(uploaded.get("path"), str) or not uploaded["path"]
                or type(uploaded.get("size_bytes")) is not int or uploaded["size_bytes"] < 0):
            raise AnsysServiceError("ANSYS returned an invalid upload response")
        return uploaded["path"]

    def submit(self, method: str, params: dict) -> dict:
        response = self._request("POST", f"/sim/{_component(method, 'method')}",
                                 expected_status=202, json={"params": params})
        accepted = _json(response)
        for field in ("id", "method", "status_url"):
            if not isinstance(accepted.get(field), str) or not accepted[field]:
                raise AnsysServiceError(f"ANSYS accepted response is missing a valid {field}")
        return accepted

    def get_job(self, job_id: str) -> dict:
        state = _json(self._request("GET", f"/jobs/{_component(job_id, 'job ID')}"))
        if (state.get("id") != job_id
                or state.get("status") not in ("pending", "running", "succeeded", "failed", "cancelled")):
            raise AnsysServiceError("ANSYS returned an invalid or mismatched job state")
        return state

    def get_result(self, job_id: str) -> dict:
        return _json(self._request("GET", f"/jobs/{_component(job_id, 'job ID')}/result"))

    def get_log(self, job_id: str) -> str:
        return self._request("GET", f"/jobs/{_component(job_id, 'job ID')}/log").text

    def list_artifacts(self, job_id: str) -> list[str]:
        names = _json(self._request("GET", f"/jobs/{_component(job_id, 'job ID')}/artifacts"), list)
        try:
            for name in names:
                _component(name, "artifact name")
        except ValueError as exc:
            raise AnsysServiceError("ANSYS returned an invalid artifact name") from exc
        return names

    def download_artifact(self, job_id: str, name: str, destination: Path) -> Path:
        destination = Path(destination)
        path = f"/jobs/{_component(job_id, 'job ID')}/artifacts/{_component(name, 'artifact name')}"
        try:
            with self._client.stream("GET", self.base_url + path, follow_redirects=False) as response:
                _check_response(response)
                output = destination.open("xb")
                try:
                    with output:
                        for chunk in response.iter_bytes():
                            output.write(chunk)
                except BaseException:
                    destination.unlink(missing_ok=True)
                    raise
        except httpx.RequestError as exc:
            raise AnsysServiceError(f"ANSYS download failed: {exc}") from exc
        return destination

    def _request(self, method, path, *, expected_status=200, **kwargs):
        try:
            response = self._client.request(method, self.base_url + path, follow_redirects=False, **kwargs)
        except httpx.RequestError as exc:
            # A timed-out submission may already be queued; never repeat it here.
            raise AnsysServiceError(f"ANSYS {method} request failed: {exc}") from exc
        _check_response(response, expected_status)
        return response
