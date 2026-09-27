"""Configuration consumed by the shared TAMS domain rules."""

from dataclasses import dataclass, field


@dataclass
class Settings:
    api_version: str = "8.2"
    tamoss_version: str = "0.1.0"
    service_version: str = "0.1.0"
    service_name: str = "Cloudflare TAMS"
    service_description: str = "Time-addressable media in R2"
    min_object_timeout: str = "86400:0"
    min_presigned_url_timeout: str = "3600:0"
    storage_allocation_max_objects: int = 1000
    storage_object_id_max_length: int = 1024
    worker_queue_retention_seconds: int = 86400
    webhook_allow_private_targets: bool = False
    webhook_allowed_hosts: list[str] = field(default_factory=list)
    webhook_timeout_seconds: int = 10
    webhook_max_attempts: int = 5

    def min_object_timeout_seconds(self) -> int:
        return 86400
