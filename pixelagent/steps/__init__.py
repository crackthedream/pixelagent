"""The ordered pipeline. Each step module exposes run(ctx) (and optionally after_gate(ctx, decision))."""
from ..pipeline import Step
from . import (anim, artist_step, build, concept, critique, detail, export, intake, kit, later, materials, matmap,
               pixelize, scene)

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
    Step(10, "artist", artist_step.run),
    Step(11, "critique", critique.run),
    Step(12, "review", critique.review, gate="C", gate_title="approve the sprite (reject with a note to redraw)",
         redo_from="artist"),
    Step(13, "animate", later.animate),
    Step(14, "export", export.run),
]

# An animation asset: linked to an approved character, driven by the user's animation prompt.
ANIM_STEPS = [
    Step(1, "anim_plan", anim.plan),
    Step(2, "anim_poses", anim.poses, gate="P", gate_title="approve the pose sheet (or reject with a note)"),
    Step(3, "anim_traces", anim.traces),
    Step(4, "anim_draw", anim.draw),
    Step(5, "anim_review", anim.review, gate="R", gate_title="approve the animation (reject with a note to redraw)",
         redo_from="anim_draw"),
    Step(6, "anim_export", anim.export),
]

# A scene backdrop: parallax layers made from the user's prompt (docs/BACKGROUNDS_DESIGN.md).
SCENE_STEPS = [
    Step(1, "scene_plan", scene.plan),
    Step(2, "scene_layers", scene.layers),
    Step(3, "scene_seams", scene.seams),
    Step(4, "scene_pixel", scene.pixel),
    Step(5, "scene_review", scene.review, gate="S", gate_title="approve the backdrop (reject with a note to redraw)",
         redo_from="scene_layers"),
    Step(6, "scene_export", scene.export),
]

# A walkable kit: tiles and decorations painted as one terrain chunk and sliced (docs/BACKGROUNDS_DESIGN.md).
KIT_STEPS = [
    Step(1, "kit_plan", kit.plan),
    Step(2, "kit_sheet", kit.sheet),
    Step(3, "kit_read", kit.read),
    Step(4, "kit_review", kit.review, gate="K", gate_title="approve the kit's test level (reject with a note to repaint)",
         redo_from="kit_sheet"),
    Step(5, "kit_export", kit.export),
]
