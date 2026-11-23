import logging

import pytest

django = pytest.importorskip("django")
from django.conf import settings  # noqa: E402

if not settings.configured:
    settings.configure(
        DEBUG=False, SECRET_KEY="x" * 40, ALLOWED_HOSTS=["*"], ROOT_URLCONF=__name__, USE_TZ=True,
        MIDDLEWARE=["regisse_kit.django_observability.ObservabilityMiddleware"],
        OBSERVABILITY_SERVICE="djdemo", METRICS_TOKEN="s3cret-token",
    )
    django.setup()

from django.http import HttpResponse, JsonResponse  # noqa: E402
from django.test import Client  # noqa: E402
from django.urls import path  # noqa: E402

from regisse_kit.django_observability import metrics_view  # noqa: E402


def item(request, item_id):
    return JsonResponse({"id": item_id})


def boom(request):
    return HttpResponse("x", status=500)


urlpatterns = [path("items/<int:item_id>/", item), path("boom/", boom), path("metrics", metrics_view)]


def test_request_id_kept_when_safe_and_replaced_when_not():
    c = Client()
    assert c.get("/items/1/", HTTP_X_REQUEST_ID="abc-123")["X-Request-ID"] == "abc-123"
    r = c.get("/items/1/", HTTP_X_REQUEST_ID="bad id with spaces")
    assert r["X-Request-ID"] != "bad id with spaces" and len(r["X-Request-ID"]) >= 16


def test_metrics_need_the_token_and_use_route_templates():
    c = Client()
    c.get("/items/41/"); c.get("/items/42/"); c.get("/boom/"); c.get("/nope/")
    assert c.get("/metrics").status_code == 401
    assert c.get("/metrics", HTTP_AUTHORIZATION="Bearer wrong").status_code == 401
    r = c.get("/metrics", HTTP_AUTHORIZATION="Bearer s3cret-token")
    assert r.status_code == 200
    body = r.content.decode()
    assert 'route="items/<int:item_id>/"' in body and "/items/41/" not in body
    assert 'status="500"' in body and 'service="djdemo"' in body and 'route="unmatched"' in body


def test_metrics_404_when_no_token_is_configured(settings):
    settings.METRICS_TOKEN = ""
    assert Client().get("/metrics").status_code == 404


def test_access_line_has_route_and_request_id_but_no_query_string(caplog):
    caplog.set_level(logging.INFO)
    Client().get("/items/7/?phone=0701020304", HTTP_X_REQUEST_ID="req-xyz-1")
    recs = [r for r in caplog.records if r.name == "djdemo.http"]
    assert recs and recs[-1].request_id == "req-xyz-1"
    assert "0701020304" not in " ".join(str(v) for r in recs for v in r.__dict__.values())
