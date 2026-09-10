"""Share the Functions binding connection contract with the outbox sender."""

from contextlib import contextmanager
import os
import re

from azure.identity import ManagedIdentityCredential
from azure.storage.queue import QueueClient


@contextmanager
def outbox_queue():
    connection = os.environ.get("AzureQueueConnection", "")
    endpoint = os.environ.get("AzureQueueConnection__queueServiceUri", "")
    if bool(connection) == bool(endpoint):
        raise RuntimeError("QUEUE_CONNECTION_MISSING_OR_AMBIGUOUS")
    if connection:
        with QueueClient.from_connection_string(
            connection, "anke-sports-jobs", api_version="2025-11-05"
        ) as client:
            yield client
        return
    if not re.fullmatch(r"https://[a-z0-9]{3,24}\.queue\.core\.windows\.net/?", endpoint):
        raise RuntimeError("QUEUE_MANAGED_IDENTITY_ENDPOINT_INVALID")
    if os.environ.get("AzureQueueConnection__credential") != "managedidentity":
        raise RuntimeError("QUEUE_MANAGED_IDENTITY_REQUIRED")
    # No Azure CLI/default credential fallback into another local product's account.
    kwargs = {}
    if client_id := os.environ.get("AzureQueueConnection__clientId"):
        kwargs["client_id"] = client_id
    with ManagedIdentityCredential(**kwargs) as credential:
        with QueueClient(
            endpoint, "anke-sports-jobs", credential=credential, api_version="2025-11-05"
        ) as client:
            yield client
