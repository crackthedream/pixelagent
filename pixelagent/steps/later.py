"""Placeholders for steps that arrive in later milestones. They skip cleanly so the pipeline runs end to end."""


def detail(ctx):
    ctx.skipped = True
    ctx.info["reason"] = "detail pass (face/hands best-of-N agent) arrives in milestone M2"


def animate(ctx):
    ctx.skipped = True
    anims = ctx.spec().animations
    ctx.info["reason"] = ("animation arrives in milestone M3" + (f"; requested: {anims}" if anims else "; none requested"))
