"""Host progress reflects durable state, independently of worker lease renewal."""
from types import SimpleNamespace

from test_ledger import LedgerBase, SESSION
from agent.session_progress import SessionProgress


class SessionProgressTests(LedgerBase):
    def test_old_send_cannot_acknowledge_new_correction_from_another_connection(self):
        item = self.new_item()
        other = SessionProgress(self.open_ledger(), self.api)
        def crossing(*args, **kwargs):
            self.send(*args, **kwargs)
            other.queue_current(item['id'])
        self.api.create_activity = crossing
        self.now += 600
        self.assertTrue(self.progress.tick())
        self.api.create_activity = self.send
        self.assertTrue(self.progress.tick())
        self.assertEqual(len(self.sent), 2)
        self.assertNotEqual(self.sent[0][2], self.sent[1][2])

    def setUp(self):
        super().setUp()
        self.sent = []
        self.api = SimpleNamespace(create_activity=self.send)
        self.progress = SessionProgress(self.ledger, self.api)

    def send(self, session, content, activity_id=None):
        self.sent.append((session, content, activity_id))
        return {"success": True}

    def test_queued_progress_has_ten_minute_cadence_persisted_across_restart(self):
        self.new_item()
        self.assertFalse(self.progress.tick())
        self.now += 599
        self.assertFalse(self.progress.tick())
        self.now += 1
        self.assertTrue(self.progress.tick())
        self.assertEqual(self.sent[0][0], SESSION)
        self.assertEqual(self.sent[0][1]["type"], "thought")
        self.assertIn("排队", self.sent[0][1]["body"])
        restarted = SessionProgress(self.open_ledger(), self.api)
        self.assertFalse(restarted.tick())
        self.now += 600
        self.assertTrue(restarted.tick())
        self.assertNotEqual(self.sent[0][2], self.sent[1][2])

    def test_running_report_uses_checkpoint_and_never_renews_lease(self):
        item = self.new_item()
        self.now += 580
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.checkpoint(item["id"], token, {"stage": "investigating"})
        lease = self.ledger.item(item["id"])["lease_expires_at"]
        self.now += 20
        self.assertTrue(self.progress.tick())
        self.assertIn("investigating", self.sent[0][1]["body"])
        self.assertIn("检查点", self.sent[0][1]["body"])
        self.assertEqual(self.ledger.item(item["id"])["lease_expires_at"], lease)

    def test_waiting_for_resource_is_reported(self):
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.await_resource(item["id"], token, "unity_slot", "batch")
        self.now += 600
        self.assertTrue(self.progress.tick())
        self.assertIn("等待", self.sent[0][1]["body"])
        self.assertIn("验证资源", self.sent[0][1]["body"])

    def test_awaiting_input_terminal_and_local_sessions_are_quiet(self):
        item = self.new_item(skill="chat")
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.await_input(item["id"], token, "Which server?")
        self.now += 600
        self.assertFalse(self.progress.tick())
        self.ledger.cancel(item["id"], "Stop")
        self.assertFalse(self.progress.tick())
        self.ledger.ensure_session("local-test", item["issue_id"], True)
        self.ledger.create_work_item(issue_id=item["issue_id"], session_id="local-test", skill="chat")
        self.now += 600
        self.assertFalse(self.progress.tick())
        self.assertEqual(self.sent, [])

    def test_failed_send_retries_same_activity_without_flooding(self):
        self.new_item()
        def unavailable(*args, **kwargs):
            self.send(*args, **kwargs)
            raise OSError("temporary outage")
        self.api.create_activity = unavailable
        self.now += 600
        self.assertTrue(self.progress.tick())
        self.assertFalse(self.progress.tick())
        self.now += 59
        self.assertFalse(self.progress.tick())
        self.api.create_activity = self.send
        self.now += 1
        self.assertTrue(self.progress.tick())
        self.assertEqual(self.sent[0][2], self.sent[1][2])
        self.assertEqual(self.sent[0][1], self.sent[1][1])

    def test_stop_during_send_is_followed_by_session_state_correction(self):
        item = self.new_item()
        def stop_during_send(session, content, activity_id=None):
            self.send(session, content, activity_id)
            if content["type"] == "thought":
                self.ledger.cancel(item["id"], "Stop")
        self.api.create_activity = stop_during_send
        self.now += 600
        self.assertTrue(self.progress.tick())
        self.assertEqual([s[1]["type"] for s in self.sent], ["thought", "response"])
        self.assertIn("停止", self.sent[-1][1]["body"])

    def test_failed_state_correction_survives_restart(self):
        item = self.new_item()
        def stop_and_fail_correction(session, content, activity_id=None):
            self.send(session, content, activity_id)
            if content["type"] == "thought":
                self.ledger.cancel(item["id"], "Stop")
            else:
                raise OSError("lost correction")
        self.api.create_activity = stop_and_fail_correction
        self.now += 600
        self.progress.tick()
        self.api.create_activity = self.send
        self.now += 60
        restarted = SessionProgress(self.open_ledger(), self.api)
        self.assertTrue(restarted.tick())
        self.assertEqual(self.sent[-1][1]["type"], "response")
        self.assertEqual(self.sent[-1][2], self.sent[-2][2])

    def test_pending_question_correction_is_rechecked_after_stop(self):
        item = self.new_item(skill="chat")
        self.now += 580
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        def question_then_failure(session, content, activity_id=None):
            self.send(session, content, activity_id)
            if content["type"] == "thought":
                self.ledger.await_input(item["id"], token, "Which environment?")
            else:
                raise OSError("question response lost")
        self.api.create_activity = question_then_failure
        self.now += 20
        self.progress.tick()
        self.ledger.cancel(item["id"], "Stop")
        self.now += 60
        self.api.create_activity = self.send
        self.progress.tick()
        self.assertEqual(self.sent[-1][1]["type"], "response")
        self.assertIn("停止", self.sent[-1][1]["body"])

    def test_pending_terminal_correction_does_not_complete_new_work_in_same_session(self):
        item = self.new_item()
        def stop_then_failure(session, content, activity_id=None):
            if content["type"] == "thought":
                self.ledger.cancel(item["id"], "Stop")
            else:
                raise OSError("response lost")
        self.api.create_activity = stop_then_failure
        self.now += 600
        self.progress.tick()
        self.new_item()
        self.now += 60
        self.api.create_activity = self.send
        self.progress.tick()
        self.assertEqual(self.sent[-1][1]["type"], "thought")
        self.assertIn("排队", self.sent[-1][1]["body"])
