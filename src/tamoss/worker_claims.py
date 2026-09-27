"""Record types used by the shared repository ports."""

from tamoss.domain.model import (
    DeletionRequestRecord,
    ObjectCleanupRecord,
    ObjectCopyRecord,
    WebhookDeliveryRecord,
)

type WorkerRecord = (
    DeletionRequestRecord
    | ObjectCleanupRecord
    | ObjectCopyRecord
    | WebhookDeliveryRecord
)
