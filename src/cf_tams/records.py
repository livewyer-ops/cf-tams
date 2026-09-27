from collections.abc import MutableMapping
from uuid import UUID

from pydantic import TypeAdapter


class RecordMap(MutableMapping):
    """Persist each domain record independently as validated JSON."""

    def __init__(self, sql, kind, record_type, key_type=UUID):
        self.sql, self.kind, self.key_type = sql, kind, key_type
        self.adapter = TypeAdapter(record_type)

    def __getitem__(self, key):
        rows = self.sql.exec(
            "SELECT payload FROM records WHERE kind = ? AND id = ?", self.kind, str(key)
        ).toArray()
        if not rows:
            raise KeyError(key)
        return self.adapter.validate_json(rows[0]["payload"])

    def __setitem__(self, key, value):
        self.sql.exec(
            "INSERT INTO records VALUES (?, ?, ?) ON CONFLICT (kind, id) DO UPDATE SET payload = excluded.payload",
            self.kind,
            str(key),
            self.adapter.dump_json(value).decode(),
        )

    def __delitem__(self, key):
        if not self.sql.exec(
            "DELETE FROM records WHERE kind = ? AND id = ? RETURNING id",
            self.kind,
            str(key),
        ).toArray():
            raise KeyError(key)

    def __iter__(self):
        return iter(
            self.key_type(row["id"])
            for row in self.sql.exec(
                "SELECT id FROM records WHERE kind = ?", self.kind
            ).toArray()
        )

    def __len__(self):
        return self.sql.exec(
            "SELECT count(*) AS n FROM records WHERE kind = ?", self.kind
        ).toArray()[0]["n"]

    def items(self):
        return [
            (self.key_type(row["id"]), self.adapter.validate_json(row["payload"]))
            for row in self.sql.exec(
                "SELECT id, payload FROM records WHERE kind = ?", self.kind
            ).toArray()
        ]

    def values(self):
        return [value for _, value in self.items()]
