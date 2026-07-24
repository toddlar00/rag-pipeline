import queue

import pytest

import operational_metrics


class _PendingWorker:
    @staticmethod
    def done():
        return False

    @staticmethod
    def result():
        raise AssertionError("a pending worker has no result")


class _FailedWorker:
    @staticmethod
    def done():
        return True

    @staticmethod
    def result():
        raise RuntimeError("worker failed")


class _SaturatingQueue:
    def __init__(self, failures):
        self.failures = failures
        self.items = []

    def put(self, item, *, timeout):
        assert timeout == 0.25
        if self.failures:
            self.failures -= 1
            raise queue.Full
        self.items.append(item)


def test_bounded_queue_metrics_measure_saturation_and_total_admission_wait():
    work_queue = _SaturatingQueue(failures=2)
    metrics = operational_metrics.QueueBackpressureMetrics()
    clock = iter((10.0, 10.125))

    operational_metrics.put_unless_worker_failed(
        work_queue,
        "batch",
        _PendingWorker(),
        metrics=metrics,
        poll_interval=0.25,
        monotonic_clock=lambda: next(clock),
    )

    assert work_queue.items == ["batch"]
    assert metrics.snapshot() == {
        "queue_put_count": 1,
        "queue_saturation_events": 2,
        "queue_wait_ms": 125.0,
    }


def test_failed_worker_is_surfaced_without_recording_false_admission():
    metrics = operational_metrics.QueueBackpressureMetrics()

    with pytest.raises(RuntimeError, match="worker failed"):
        operational_metrics.put_unless_worker_failed(
            _SaturatingQueue(failures=0),
            "batch",
            _FailedWorker(),
            metrics=metrics,
        )

    assert metrics.snapshot() == {
        "queue_put_count": 0,
        "queue_saturation_events": 0,
        "queue_wait_ms": 0.0,
    }


@pytest.mark.parametrize("poll_interval", [True, 0, -1, float("inf")])
def test_queue_poll_interval_must_be_positive_and_finite(poll_interval):
    with pytest.raises(ValueError, match="poll interval"):
        operational_metrics.put_unless_worker_failed(
            _SaturatingQueue(failures=0),
            "batch",
            _PendingWorker(),
            poll_interval=poll_interval,
        )


def test_queue_metric_updates_reject_invalid_values_and_clamp_clock_rollback():
    metrics = operational_metrics.QueueBackpressureMetrics()
    metrics.record_admission(saturation_events=0, wait_ms=0)
    operational_metrics.put_unless_worker_failed(
        _SaturatingQueue(failures=0),
        "batch",
        _PendingWorker(),
        metrics=metrics,
        poll_interval=0.25,
        monotonic_clock=iter((5.0, 4.0)).__next__,
    )

    assert metrics.snapshot()["queue_wait_ms"] == 0.0
    with pytest.raises(ValueError, match="saturation"):
        metrics.record_admission(saturation_events=True, wait_ms=0)
    with pytest.raises(ValueError, match="queue wait"):
        metrics.record_admission(saturation_events=0, wait_ms=float("nan"))


def test_bad_post_admission_clock_cannot_turn_queued_work_into_failure():
    work_queue = _SaturatingQueue(failures=1)
    metrics = operational_metrics.QueueBackpressureMetrics()
    clock = iter((10.0, float("nan")))

    operational_metrics.put_unless_worker_failed(
        work_queue, "batch", _PendingWorker(), metrics=metrics,
        poll_interval=0.25, monotonic_clock=lambda: next(clock))

    assert work_queue.items == ["batch"]
    assert metrics.snapshot() == {
        "queue_put_count": 1,
        "queue_saturation_events": 1,
        "queue_wait_ms": 0.0,
    }


def test_bad_initial_clock_or_metrics_fail_before_queue_admission():
    work_queue = _SaturatingQueue(failures=0)
    with pytest.raises(ValueError, match="clock"):
        operational_metrics.put_unless_worker_failed(
            work_queue, "batch", _PendingWorker(),
            monotonic_clock=lambda: float("inf"))
    with pytest.raises(TypeError, match="QueueBackpressureMetrics"):
        operational_metrics.put_unless_worker_failed(
            work_queue, "batch", _PendingWorker(), metrics=object())

    assert work_queue.items == []


def test_cumulative_wait_overflow_is_rejected_transactionally():
    metrics = operational_metrics.QueueBackpressureMetrics()
    metrics.record_admission(saturation_events=2, wait_ms=1e308)

    with pytest.raises(ValueError, match="cumulative"):
        metrics.record_admission(saturation_events=3, wait_ms=1e308)

    assert metrics.snapshot() == {
        "queue_put_count": 1,
        "queue_saturation_events": 2,
        "queue_wait_ms": 1e308,
    }


def test_queue_counters_cannot_be_seeded_with_unvalidated_state():
    with pytest.raises(TypeError):
        operational_metrics.QueueBackpressureMetrics(wait_ms=float("inf"))
