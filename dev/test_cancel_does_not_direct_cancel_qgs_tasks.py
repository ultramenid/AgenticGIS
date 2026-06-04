"""Regression check that Stop does not directly cancel QgsTask objects."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from AgenticGis.core.toolkit import QgisToolkit


class _FakeTask:
    def __init__(self):
        self.cancel_calls = 0

    def cancel(self):
        self.cancel_calls += 1


def main():
    toolkit = QgisToolkit(iface=None)
    event, owner = toolkit._cancel.register()
    assert owner is True

    task = _FakeTask()
    toolkit._bg_tasks.add(task)

    toolkit.request_cancel()

    assert event.is_set(), "Stop should flip the cooperative cancellation token"
    assert task.cancel_calls == 0, (
        "Stop should not directly cancel task-manager-owned QgsTask objects"
    )

    toolkit._cancel.release(event)


if __name__ == "__main__":
    main()
