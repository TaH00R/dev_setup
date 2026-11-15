"""The bus stays synchronous; its bridge always delivers on the Qt owner thread."""

import os
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QCoreApplication, QEvent, QObject, Slot
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as error:
    if error.name is None or not error.name.startswith("PySide6"):
        raise
    QT_AVAILABLE = False
else:
    from app.ui.events import EventBridge

    QT_AVAILABLE = True

from services.events import EventBus
from tests.unit.test_application_events import event


@unittest.skipUnless(QT_AVAILABLE, "Install requirements-gui.txt for Qt tests.")
class EventBridgeTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.bus = EventBus()
        self.bridge = EventBridge(self.bus)
        self.addCleanup(self.bridge.close)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(5)
        self.assertTrue(predicate())

    def test_worker_publication_is_queued_and_ordered_on_receiver_thread(self):
        received = []

        class Receiver(QObject):
            @Slot(object)
            def receive(self, delivery):
                received.append((delivery.sequence, threading.get_ident()))

        receiver = Receiver()
        self.bridge.delivered.connect(receiver.receive)
        publisher_threads = []

        def publish():
            publisher_threads.append(threading.get_ident())
            for _ in range(10):
                self.bus.publish(event())

        thread = threading.Thread(target=publish)
        thread.start()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(received, [])
        self.wait_for(lambda: len(received) == 10)
        self.assertEqual([sequence for sequence, _ in received], list(range(1, 11)))
        self.assertEqual({ident for _, ident in received}, {threading.get_ident()})
        self.assertNotEqual(publisher_threads[0], threading.get_ident())

    def test_close_unsubscribes_and_discards_already_queued_delivery(self):
        received = []
        self.bridge.delivered.connect(received.append)
        self.bus.publish(event())
        self.bridge.close()
        self.bridge.close()
        self.bus.publish(event())
        self.app.processEvents()
        self.assertEqual(self.bus.subscriber_count, 0)
        self.assertEqual(received, [])

    def test_destroyed_qobject_releases_core_subscription(self):
        bridge = EventBridge(self.bus)
        self.assertEqual(self.bus.subscriber_count, 2)
        bridge.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(self.bus.subscriber_count, 1)
        self.bus.publish(event())
        self.app.processEvents()

    def test_backlog_is_bounded_reports_omissions_and_retains_recent_order(self):
        received, omitted = [], []
        self.bridge.delivered.connect(received.append)
        self.bridge.overflow.connect(omitted.append)
        for _ in range(600):
            self.bus.publish(event())
        self.assertEqual(len(self.bridge._pending), 512)
        self.wait_for(lambda: len(received) == 512)
        self.assertEqual(omitted, [88])
        self.assertEqual([value.sequence for value in received], list(range(89, 601)))

    def test_final_report_barrier_drains_exactly_once(self):
        received = []
        self.bridge.delivered.connect(received.append)
        self.bus.publish(event())
        self.bridge.flush()
        self.assertEqual(len(received), 1)
        self.app.processEvents()
        self.assertEqual(len(received), 1)

    def test_reentrant_gui_flush_cannot_overtake_current_batch(self):
        received = []

        def receive(delivery):
            received.append(delivery.sequence)
            if delivery.sequence == 1:
                self.bus.publish(event())
                self.bridge.flush()
                self.app.processEvents()

        self.bridge.delivered.connect(receive)
        self.bus.publish(event())
        self.bus.publish(event())
        self.wait_for(lambda: len(received) == 3)
        self.assertEqual(received, [1, 2, 3])
