import pytest
from pydantic import ValidationError

from app.common.errors import DomainException
from app.core.config import settings
from app.domains.internal.security import verify_internal_service_key
from app.domains.pipeline.schema import CreateEmailDeliveryRequest


def test_final_artifact_email_requires_api_credentials():
    with pytest.raises(ValidationError):
        CreateEmailDeliveryRequest(
            recipient="customer@example.com",
            delivery_type="FINAL_ARTIFACT",
        )


def test_email_api_credentials_must_be_supplied_as_a_pair():
    with pytest.raises(ValidationError):
        CreateEmailDeliveryRequest(
            recipient="customer@example.com",
            delivery_type="FINAL_ARTIFACT",
            api_endpoint_url="http://localhost:8082/api/external/v1/deliveries/CTR-1",
        )


def test_internal_service_key_rejects_missing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "internal_service_key", "")

    with pytest.raises(DomainException) as error:
        verify_internal_service_key("local-development-only-key")

    assert error.value.code == "INTERNAL_SERVICE_KEY_NOT_CONFIGURED"


def test_internal_service_key_uses_constant_time_comparison(monkeypatch):
    monkeypatch.setattr(settings, "internal_service_key", "test-internal-key")

    verify_internal_service_key("test-internal-key")

    with pytest.raises(DomainException) as error:
        verify_internal_service_key("wrong-key")
    assert error.value.code == "INVALID_INTERNAL_SERVICE_KEY"
