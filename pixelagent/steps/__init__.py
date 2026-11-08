"""The ordered pipeline. Each step module exposes run(ctx) (and optionally after_gate(ctx, decision))."""
from ..pipeline import Step
from . import build, concept, critique, detail, export, intake, later, materials, matmap, pixelize

STEPS = [
    Step(1, "intake", intake.run, gate="A", gate_title="confirm or edit the spec"),
    Step(2, "concept", concept.run, gate="B", gate_title="pick a concept", after_gate=concept.after_gate),
    Step(3, "matmap", matmap.run),
    Step(4, "pixelize", pixelize.run),
    Step(5, "materials", materials.run),
    Step(6, "rebuild", build.rebuild),
    Step(7, "lines", build.lines),
    Step(8, "detail", detail.run),
    Step(9, "polish", build.polish),
    Step(10, "critique", critique.run),
    Step(11, "review", critique.review, gate="C", gate_title="approve the sprite"),
    Step(12, "animate", later.animate),
    Step(13, "export", export.run),
]
