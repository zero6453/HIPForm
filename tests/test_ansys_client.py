"""Documented ANSYS HTTP behavior, exercised through its public client."""

import json

import httpx
import pytest

from hipform.ansys_client import AnsysClient, AnsysServiceError


def test_uploaded_step_can_be_submitted_without_losing_smoke_fidelity(tmp_path):
    capsule = tmp_path / "capsule.step"
    capsule.write_bytes(b"ISO-10303-21;\nEND-ISO-10303-21;")
    accepted = {"id": "job-1", "method": "full3d-hip", "status": "pending",
                "fidelity": "smoke", "status_url": "/jobs/job-1"}

    def service(request):
        if request.url.path == "/uploads":
            assert request.method == "POST"
            assert b'name="file"; filename="capsule.step"' in request.content
            assert capsule.read_bytes() in request.content
            return httpx.Response(200, json={"path": "C:\\uploads\\capsule.step", "size_bytes": 36})
        assert request.method == "POST"
        assert request.url.path == "/sim/full3d-hip"
        assert json.loads(request.content) == {
            "params": {"geometry": {"capsule_step": "C:\\uploads\\capsule.step"}}}
        return httpx.Response(202, json=accepted)

    with httpx.Client(transport=httpx.MockTransport(service)) as http:
        with AnsysClient("http://ansys.test/", client=http) as client:
            remote_path = client.upload_step(capsule)
            assert client.submit("full3d-hip", {"geometry": {"capsule_step": remote_path}}) == accepted
        assert not http.is_closed


def test_job_results_logs_and_artifacts_retain_service_values(tmp_path):
    def service(request):
        assert request.method == "GET"
        responses = {
            "/jobs/job-1": {"id": "job-1", "status": "succeeded", "fidelity": "smoke"},
            "/jobs/job-1/result": {"fidelity": "smoke", "deformed_stl": None},
            "/jobs/job-1/artifacts": ["model.cdb"],
        }
        if request.url.path.endswith("/log"):
            return httpx.Response(200, text="MAPDL completed\n")
        if request.url.path.endswith("/model.cdb"):
            return httpx.Response(200, content=b"/PREP7\n")
        return httpx.Response(200, json=responses[request.url.path])

    with httpx.Client(transport=httpx.MockTransport(service)) as http:
        with AnsysClient("http://ansys.test", client=http) as client:
            assert client.get_job("job-1")["status"] == "succeeded"
            assert client.get_result("job-1") == {"fidelity": "smoke", "deformed_stl": None}
            assert client.get_log("job-1") == "MAPDL completed\n"
            assert client.list_artifacts("job-1") == ["model.cdb"]
            output = client.download_artifact("job-1", "model.cdb", tmp_path / "model.cdb")
            assert output.read_bytes() == b"/PREP7\n"


@pytest.mark.parametrize("response,message", [
    (httpx.Response(503, text="Sakura Frp Service Unavailable"), "HTTP 503.*Sakura Frp"),
    (httpx.Response(400, json={"code": "INVALID_PARAMS", "message": "unknown field"}), "INVALID_PARAMS"),
    (httpx.Response(202, text="not JSON"), "invalid JSON"),
    (httpx.Response(202, json=[]), "JSON object"),
    (httpx.Response(202, json={"status": "pending"}), "missing.*id"),
    (httpx.Response(307, headers={"Location": "http://other.test/sim/full3d-hip"}), "HTTP 307"),
])
def test_unusable_submission_responses_fail_clearly_without_resubmission(response, message):
    submitted = []

    def service(request):
        submitted.append(request)
        return response

    with httpx.Client(transport=httpx.MockTransport(service), follow_redirects=True) as http:
        with AnsysClient("http://ansys.test", client=http) as client:
            with pytest.raises(AnsysServiceError, match=message):
                client.submit("full3d-hip", {})
    assert len(submitted) == 1


@pytest.mark.parametrize("name", ["../model.cdb", "a/b", "a\\b", "..", "%2e%2e", "model\x00.cdb"])
def test_unsafe_artifact_names_never_reach_the_service(tmp_path, name):
    def service(request):
        pytest.fail("Unsafe artifact must be rejected before HTTP")

    with httpx.Client(transport=httpx.MockTransport(service)) as http:
        with AnsysClient("http://ansys.test", client=http) as client:
            with pytest.raises(ValueError, match="Invalid"):
                client.download_artifact("job-1", name, tmp_path / "download")


def test_artifact_download_cannot_overwrite_an_existing_symlink(tmp_path):
    original = tmp_path / "original"
    original.write_bytes(b"keep")
    destination = tmp_path / "model.cdb"
    destination.symlink_to(original)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"replace"))
    with httpx.Client(transport=transport) as http:
        with AnsysClient("http://ansys.test", client=http) as client:
            with pytest.raises(FileExistsError):
                client.download_artifact("job-1", "model.cdb", destination)
    assert original.read_bytes() == b"keep"
    assert destination.is_symlink()


def test_bad_artifact_list_is_rejected_as_a_service_error():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=["../escape"]))
    with httpx.Client(transport=transport) as http:
        with AnsysClient("http://ansys.test", client=http) as client:
            with pytest.raises(AnsysServiceError, match="artifact"):
                client.list_artifacts("job-1")


def test_connection_loss_does_not_resubmit_an_ambiguous_job():
    submitted = []

    def service(request):
        submitted.append(request)
        raise httpx.ReadTimeout("connection lost after submission", request=request)

    with httpx.Client(transport=httpx.MockTransport(service)) as http:
        with AnsysClient("http://ansys.test", client=http) as client:
            with pytest.raises(AnsysServiceError, match="connection lost"):
                client.submit("full3d-hip", {})
    assert len(submitted) == 1


def test_interrupted_artifact_download_removes_its_partial_file(tmp_path):
    class InterruptedStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"partial"
            raise httpx.ReadError("download interrupted")

    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=InterruptedStream()))
    destination = tmp_path / "model.cdb"
    with httpx.Client(transport=transport) as http:
        with AnsysClient("http://ansys.test", client=http) as client:
            with pytest.raises(AnsysServiceError, match="download interrupted"):
                client.download_artifact("job-1", "model.cdb", destination)
    assert not destination.exists()


@pytest.mark.parametrize("response,message", [
    (httpx.Response(307, headers={"Location": "http://other.test/uploads"}), "HTTP 307"),
    (httpx.Response(200, json={"path": None, "size_bytes": 35}), "upload"),
])
def test_bad_upload_response_does_not_forward_the_file(tmp_path, response, message):
    capsule = tmp_path / "capsule.step"
    capsule.write_bytes(b"ISO-10303-21;\nEND-ISO-10303-21;")
    uploads = []

    def service(request):
        uploads.append(request)
        return response

    with httpx.Client(transport=httpx.MockTransport(service), follow_redirects=True) as http:
        with AnsysClient("http://ansys.test", client=http) as client:
            with pytest.raises(AnsysServiceError, match=message):
                client.upload_step(capsule)
    assert len(uploads) == 1


@pytest.mark.parametrize("state", [
    {"id": "job-1"},
    {"id": "other-job", "status": "succeeded", "fidelity": "smoke"},
    {"id": "job-1", "status": "unknown"},
])
def test_polling_rejects_missing_or_mismatched_job_state(state):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=state))
    with httpx.Client(transport=transport) as http:
        with AnsysClient("http://ansys.test", client=http) as client:
            with pytest.raises(AnsysServiceError, match="job state"):
                client.get_job("job-1")
