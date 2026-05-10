"""Shared test fixtures for frontend tests."""

import os

# Must be set before any app module import
_TEST_ENV = {
    "TENANT_ID": "test-tenant-id",
    "WEB_CLIENT_ID": "test-web-client-id",
    "BLUEPRINT_APP_CLIENT_ID": "test-blueprint-id",
    "BACKEND_URL": "http://test-backend:8000",
    "SESSION_SECRET": "test-session-secret-32-chars-long",
    "BASE_URL": "http://localhost:3000",
    "AZURE_FEDERATED_TOKEN_FILE": "/tmp/test-federated-token",
}
os.environ.update(_TEST_ENV)
