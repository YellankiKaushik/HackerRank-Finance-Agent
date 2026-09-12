import sys
import unittest
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from buywait.snapshot import (
    MessageTemporalRelation,
    build_request_snapshot,
    _message_temporal_relation,
    validate_snapshots_for_all_requests,
)
from buywait.loaders import load_dataset


class SnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(ROOT / "dataset")

    def test_snapshot_builds_for_all_evaluation_requests(self):
        snapshots = validate_snapshots_for_all_requests(self.dataset)
        self.assertEqual(len(snapshots), 250)
        self.assertEqual({snapshot.request.request_id for snapshot in snapshots}, set(self.dataset.request_by_id))

    def test_snapshot_selects_profile_events_and_options(self):
        request = self.dataset.requests[0]
        snapshot = build_request_snapshot(self.dataset, request.request_id)
        self.assertEqual(snapshot.request, request)
        self.assertEqual(snapshot.profile, self.dataset.profile_by_user[request.user_id])
        self.assertEqual(snapshot.user_events, self.dataset.events_by_user[request.user_id])
        self.assertEqual(snapshot.payment_options, self.dataset.payment_options_by_request[request.request_id])

    def test_inclusive_messages_are_retained(self):
        request = next(
            item for item in self.dataset.requests
            if any(message.request_id == item.request_id for message in self.dataset.messages)
        )
        snapshot = build_request_snapshot(self.dataset, request.request_id)
        self.assertTrue(any(item.message.request_id == request.request_id for item in snapshot.messages))
        self.assertTrue(any(item.message.user_id == request.user_id for item in snapshot.messages))

    def test_user_level_blank_link_messages_are_retained_when_present(self):
        request = next(
            item for item in self.dataset.requests
            if any(
                message.user_id == item.user_id and message.request_id is None and message.related_event_id is None
                for message in self.dataset.messages
            )
        )
        snapshot = build_request_snapshot(self.dataset, request.request_id)
        self.assertTrue(
            any("user_level_blank_links_retained" in item.reason for item in snapshot.messages)
        )

    def test_same_day_message_temporal_cutoff_remains_explicit(self):
        message = next(iter(self.dataset.messages))
        same_day = type(message)(
            message_id=message.message_id,
            user_id=message.user_id,
            request_id=message.request_id,
            related_event_id=message.related_event_id,
            sent_at=datetime(2026, 1, 15, 12, 30),
            source_type=message.source_type,
            message_text=message.message_text,
            source_row=message.source_row,
        )
        self.assertEqual(
            _message_temporal_relation(same_day, date(2026, 1, 15)),
            MessageTemporalRelation.ON_REQUEST_DATE_TIME_UNRESOLVED,
        )


if __name__ == "__main__":
    unittest.main()
