"""
Tests for trigger lifecycle behavior.
"""

from airfogsim.core.trigger import TimeTrigger


class TestTrigger:
    """Regression coverage for trigger activation limits."""

    def test_time_trigger_respects_max_triggers(self, env):
        """Periodic triggers should stop firing once max_triggers is reached."""
        fired_at = []

        trigger = TimeTrigger(env, interval=1, name="periodic_once")
        trigger.set_max_triggers(1)
        trigger.add_callback(lambda context: fired_at.append(context["time"]))
        trigger.activate()

        env.run(until=5)

        assert fired_at == [1]
        assert trigger.trigger_count == 1
        assert trigger.has_reached_max()
        assert not trigger.is_active
