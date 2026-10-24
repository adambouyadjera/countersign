"""Eval HTTP routes delegate to jobs without initializing live dependencies."""

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.contract_models import ErrorResponse, EvalJob
from app.main import create_app


class FakeEvalManager:
    def __init__(self):
        self.requests = []
        self.jobs = {}
        self.failure = None

    def start(self, body):
        if self.failure is not None:
            raise self.failure
        self.requests.append(body.model_copy(deep=True))
        job = EvalJob(job_id="eval-recorded", suite=body.suite, status="queued", execution="dry_run",
                      progress={"completed": 0, "total": 40}, items=[])
        self.jobs[job.job_id] = job
        return job

    def get(self, job_id):
        return self.jobs.get(job_id)


@pytest.fixture
def application(tmp_path, monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    settings = Settings(_env_file=None, nessie_mode="offline", extractor_mode="offline",
                        planner_mode="offline", agent_mode="offline", mutator_mode="offline", elastic_mode="offline")
    app = create_app(settings, db_path=tmp_path / "must-not-exist.sqlite")
    app.state.eval_manager = FakeEvalManager()
    return app


@pytest.fixture
def client(application):
    # Deliberately omit lifespan: the routes depend only on the job manager.
    client = TestClient(application)
    yield client
    client.close()


def test_post_returns_a_contract_dry_run_job_and_leaves_default_lane_selection_to_manager(client, application):
    response = client.post("/api/eval", json={"suite": "planted_errors_v1"})
    assert response.status_code == 200, response.text
    job = EvalJob.model_validate(response.json())
    assert job.job_id == "eval-recorded" and job.execution == "dry_run"
    assert job.progress.total == 40 and job.items == []
    assert application.state.eval_manager.requests[0].lanes is None
    assert not hasattr(application.state, "adapters")
    assert not hasattr(application.state, "store")


def test_optional_unguarded_lane_is_passed_through_with_countersign(client, application):
    response = client.post("/api/eval", json={"suite": "planted_errors_v1", "lanes": ["countersign", "unguarded"]})
    assert response.status_code == 200
    assert application.state.eval_manager.requests[0].lanes == ["countersign", "unguarded"]


@pytest.mark.parametrize("body", [
    {}, {"suite": "unrecognized"}, {"suite": "planted_errors_v1", "lanes": []},
    {"suite": "planted_errors_v1", "lanes": ["countersign", "countersign"]},
    {"suite": "planted_errors_v1", "lanes": ["unguarded"]},
    {"suite": "planted_errors_v1", "lanes": ["system"]},
    {"suite": "planted_errors_v1", "surprise": True},
])
def test_invalid_suite_and_lane_requests_are_rejected_before_job_start(client, application, body):
    response = client.post("/api/eval", json=body)
    assert response.status_code == 422
    assert ErrorResponse.model_validate(response.json()).error.code == "validation_error"
    assert application.state.eval_manager.requests == []


def test_get_reads_an_existing_job_and_unknown_id_returns_contract_404(client):
    created = client.post("/api/eval", json={"suite": "planted_errors_v1"}).json()
    response = client.get("/api/eval/" + created["job_id"])
    assert response.status_code == 200 and response.json() == created
    missing = client.get("/api/eval/missing")
    assert missing.status_code == 404
    assert ErrorResponse.model_validate(missing.json()).error.code == "not_found"


def test_start_errors_keep_contract_status_and_are_not_relabelled_success(client, application):
    application.state.eval_manager.failure = HTTPException(503, "An evaluation is already running")
    response = client.post("/api/eval", json={"suite": "planted_errors_v1"})
    assert response.status_code == 503
    assert ErrorResponse.model_validate(response.json()).error.code == "service_unavailable"
    assert application.state.eval_manager.requests == []


def test_openapi_uses_shared_eval_shapes_and_operation_ids(application):
    schema = application.openapi()
    post = schema["paths"]["/api/eval"]["post"]
    get = schema["paths"]["/api/eval/{job_id}"]["get"]
    assert post["operationId"] == "create_eval_job"
    assert get["operationId"] == "get_eval_job"
    assert post["requestBody"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/EvalRequest"}
    for operation in (post, get):
        assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/EvalJob"}
