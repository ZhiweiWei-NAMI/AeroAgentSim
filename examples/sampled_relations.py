"""Authored record/collection example with a relation and sampled entered event.

Run: .venv/bin/python -m examples.sampled_relations
This is deterministic example content, not an observation from an external source.
"""

from aerokernel import (
    ActivateObligation,
    BindingManifest,
    BindingRule,
    Cardinality,
    EntityRef,
    FieldDescriptor,
    Instant,
    Interval,
    Kernel,
    LifecycleRule,
    MemoryRegistry,
    MessageDescriptor,
    ObligationRule,
    Partition,
    RelationDependency,
    RelationDescriptor,
    RelationRule,
    SampleSpec,
    Stamp,
    TypeDescriptor,
)
from aerokernel.engine import Dependency
from aerokernel.profiles.sampled import EnteredEvaluator, Evaluation
from aerokernel.sdk import ContextEngine, EngineContext

DOCUMENT = EntityRef("sample", "0", "document", 0, "Document")
FOLDER = EntityRef("sample", "0", "folder", 0, "Folder")
SPEC = SampleSpec(
    "ready-context",
    "evaluate",
    ("records",),
    {"document": DOCUMENT},
    {"document": "records"},
    {"document": ("canonical", "canonical")},
)


class Records(ContextEngine):
    """Explicit authored work at time 3; owns lifecycle, field and source edges."""

    def bootstrap(self, ctx: EngineContext) -> None:
        ctx.create(FOLDER)
        ctx.create(DOCUMENT)
        stamp, valid = Stamp("canonical", 0, 1, "canonical"), Interval(ctx.now, None)
        ctx.set(DOCUMENT, "ready", False, acquired=stamp, valid=valid)
        ctx.relate(
            "membership", "contains", FOLDER, DOCUMENT, acquired=stamp, valid=valid
        )
        ctx.ops.append(
            ActivateObligation(
                "required-document", "contains", "targets_per_source", FOLDER, valid
            )
        )
        self.wakeup_ns = 3

    def step(self, ctx: EngineContext) -> None:
        ctx.set(
            DOCUMENT,
            "ready",
            True,
            acquired=Stamp("canonical", ctx.now.ns, 1, "canonical"),
            valid=Interval(ctx.now, None),
        )
        self.wakeup_ns = None


class Ready(EnteredEvaluator):
    """The optional profile computes only from the settled declared input."""

    def evaluate(self, ctx: EngineContext) -> Evaluation:
        value = ctx.get(DOCUMENT, "ready")
        if type(value) is not bool:
            return Evaluation("unresolved", None, ("ready is not observed",))
        return Evaluation("known", value)


def make_run() -> Kernel:
    """Bind the explicit authored model and independent evaluator."""
    registry = MemoryRegistry(
        (TypeDescriptor("Folder"), TypeDescriptor("Document")),
        (FieldDescriptor("ready", "Document", {"type": "boolean"}),),
        (
            MessageDescriptor(
                "ready.entered",
                schema={
                    "type": "record",
                    "members": {
                        "context_id": {"type": "string"},
                        "start_ns": {"type": "integer"},
                        "end_ns": {"type": "integer"},
                    },
                    "required": ["context_id", "start_ns", "end_ns"],
                    "extra": False,
                },
            ),
        ),
        relations=(
            RelationDescriptor(
                "contains",
                "Folder",
                "Document",
                Cardinality(1, 1),
                Cardinality(0, None),
            ),
        ),
    )
    records = Records(
        Partition(
            "records",
            "records",
            produces=("ready",),
            lifecycle=True,
            relation_produces=("contains",),
            obligation_produces=("contains",),
        )
    )
    evaluator = Ready(
        Partition(
            "evaluate",
            "evaluate",
            consumes=(Dependency("ready"),),
            emits=("ready.entered",),
            message_targets=("events",),
            relation_consumes=(RelationDependency("contains"),),
        ),
        SPEC,
        event_schema="ready.entered",
        topic="events",
    )
    manifest = BindingManifest(
        "sample",
        "0",
        (DOCUMENT, FOLDER),
        rules=(BindingRule("records", "Document", ("ready",)),),
        lifecycle=(
            LifecycleRule("records", "Document"),
            LifecycleRule("records", "Folder"),
        ),
        relation_rules=(RelationRule("records", "contains", "Folder"),),
        obligation_rules=(
            ObligationRule("records", "contains", "targets_per_source", "Folder"),
        ),
        samples=(SPEC,),
    )
    kernel = Kernel()
    kernel.bind(registry, manifest, (records, evaluator))
    return kernel


if __name__ == "__main__":
    run = make_run()
    run.start()
    run.run_until(3)
    assert len(run.view().relations("contains", Instant(3))) == 1
    event = next(iter(run._store.messages.values()))
    print(dict(event.payload))
    run.close()
