import pytest

from airfogsim.core.cooperative_task import CooperativeTask, CooperativeTaskState
from airfogsim.core.transfer_item import MockTransferItem


class DummyEventRegistry:
	def subscribe(self, *args, **kwargs):
		return None

	def get_event(self, *args, **kwargs):
		return DummyEvent()

	def trigger_event(self, *args, **kwargs):
		return None

	def unsubscribe(self, *args, **kwargs):
		return None


class DummyEvent:
	id = "dummy_event"


class DummyEnv:
	def __init__(self):
		self.now = 0.0
		self.visual_interval = 1.0
		self.event_registry = DummyEventRegistry()
		self.id = "env"
		self.agent_registry = None

	def advance(self, delta: float) -> None:
		self.now += delta


class DummyComponent:
	MONITORED_STATES = []


class DummyAgent:
	def __init__(self):
		self.id = "dummy_agent"
		self._state_templates = {"dummy_state": 0.0, "position": (0.0, 0.0, 0.0)}
		self._components = {"dummy_component": DummyComponent()}
		self._states = {"position": (0.0, 0.0, 0.0), "dummy_state": 0.0}

	def get_state_templates(self):
		return self._state_templates

	def get_component(self, name):
		return self._components.get(name)

	def get_state(self, key):
		return self._states.get(key)

	def update_states(self, updates):
		self._states.update(updates)


class DummyCooperativeTask(CooperativeTask):
	NECESSARY_METRICS = ["dummy_metric"]
	PRODUCED_STATES = ["dummy_state"]

	def __init__(
		self,
		env,
		agent,
		*,
		invitation_results=None,
		confirmation_sequence=None,
		invitation_attempts=3,
		invitation_timeout=1.0,
		confirmation_timeout=1.0,
		retry_delay=0.5,
		transfer_ticks_required=1,
	):
		props = {
			"participants": {},
			"rendezvous_area": None,
			"transfer_manager": None,
			"item": MockTransferItem("mock_item", "package"),
			"invitation_attempts": invitation_attempts,
			"invitation_timeout": invitation_timeout,
			"confirmation_timeout": confirmation_timeout,
			"invitation_retry_delay": retry_delay,
		}
		super().__init__(env, agent, "dummy_component", "dummy_task", properties=props)
		self._invitation_results = list(invitation_results or [True])
		self._confirmation_sequence = list(confirmation_sequence or [True])
		self._transfer_ticks_required = transfer_ticks_required
		self._transfer_ticks = 0
		self.invitation_send_count = 0
		self.events = []
		self.timeout_count = 0

	def _check_preconditions(self) -> bool:
		return True

	def send_invitation(self) -> bool:
		self.invitation_send_count += 1
		if self._invitation_results:
			return self._invitation_results.pop(0)
		return True

	def on_invitation_sent(self) -> None:
		self.events.append(("sent", None))

	def check_confirmation(self):
		if not self._confirmation_sequence:
			return None
		return self._confirmation_sequence.pop(0)

	def on_invitation_accepted(self) -> None:
		self.events.append(("accepted", None))

	def on_invitation_rejected(self, reason: str) -> None:
		self.events.append(("rejected", reason))

	def on_invitation_timeout(self) -> None:
		self.events.append(("timeout", None))
		self.timeout_count += 1

	def on_invitation_retry(self, reason: str) -> None:
		self.events.append(("retry", reason))

	def on_prepare(self) -> bool:
		return True

	def execute_transfer(self) -> float:
		self._transfer_ticks += 1
		return min(1.0, self._transfer_ticks / self._transfer_ticks_required)

	def _is_transfer_done(self) -> bool:
		return self._transfer_ticks >= self._transfer_ticks_required


def run_until_terminal(task: CooperativeTask, env: DummyEnv, max_steps: int = 20) -> None:
	for _ in range(max_steps):
		task.update()
		if task.state in (CooperativeTaskState.COMPLETED, CooperativeTaskState.FAILED):
			break
		env.advance(0.0)


def test_cooperative_task_succeeds_after_confirmation_acceptance():
	env = DummyEnv()
	agent = DummyAgent()
	task = DummyCooperativeTask(env, agent)

	run_until_terminal(task, env)

	assert task.state == CooperativeTaskState.COMPLETED
	assert task.error is None
	assert task.invitation_send_count == 1
	assert ("accepted", None) in task.events


def test_cooperative_task_retries_after_rejection():
	env = DummyEnv()
	agent = DummyAgent()
	task = DummyCooperativeTask(
		env,
		agent,
		invitation_results=[True, True],
		confirmation_sequence=[False, True],
		invitation_attempts=2,
		retry_delay=0.5,
	)

	task.update()  # first attempt -> rejection schedules retry
	assert task.state == CooperativeTaskState.INVITATION
	assert ("rejected", "rejected") in task.events
	assert ("retry", "invitation_rejected") in task.events

	env.advance(0.5)
	run_until_terminal(task, env)

	assert task.state == CooperativeTaskState.COMPLETED
	assert task.invitation_send_count == 2
	assert ("accepted", None) in task.events


def test_cooperative_task_fails_after_confirmation_timeout():
	env = DummyEnv()
	agent = DummyAgent()
	task = DummyCooperativeTask(
		env,
		agent,
		invitation_results=[True, True],
		confirmation_sequence=[None, None, None, None, None],
		invitation_attempts=2,
		confirmation_timeout=1.0,
		retry_delay=0.5,
	)

	# First invitation
	task.update()  # send + initial wait (None)
	env.advance(0.5)
	task.update()  # still waiting before timeout
	env.advance(0.6)
	task.update()  # timeout -> retry scheduled

	assert task.state == CooperativeTaskState.INVITATION
	assert task.timeout_count == 1
	assert ("timeout", None) in task.events
	assert ("retry", "confirmation_timeout") in task.events

	env.advance(0.5)
	task.update()  # second invitation sent
	env.advance(1.1)
	task.update()  # second attempt times out and exhausts retries

	assert task.state == CooperativeTaskState.FAILED
	assert task.error == "confirmation_timeout"
	assert task.invitation_send_count == 2
	assert task.timeout_count == 2
