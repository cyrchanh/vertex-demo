"""Technique library: guide claims about 1.9+ sword PvP, each checked against this arena.

Built once (2026-11-06) from the Minecraft Wiki's Java PvP tutorial plus the arena code
(BotPlayer.tick order, the crit/sprint checks) and measurements over 3 tournament rounds and the
owner's duels (~10,000 hits). Status:

  confirmed   follows from the arena code and/or was measured in recorded fights
  not shown   measured, and the claimed effect didn't appear at bot timing
  wrong here  true for human clients but not for bots in this arena
  untested    a guide technique nobody has measured yet: an idea to test with a prediction

Shown to every strategist as a menu of ideas. Confirmed mechanics are also stated in the rules.
Excluded: shields, axes, pearls, blocks, potions (not in the kit) and 1.8 spam-clicking.
"""

SOURCE = "https://minecraft.wiki/w/Tutorial:PvP_(Java_Edition)"

LIBRARY = [
    {"name": "Crit timing", "status": "confirmed",
     "what": "A crit needs full charge, falling, and NOT sprinting at the click. Each tick the arena handles your click "
             "BEFORE it reads this decision's movement keys, so stop sprinting at least one decision (2 ticks) before "
             "the crit swing: release sprint and keep it released (releasing forward also ends it, but forward + sprint "
             "held restarts a sprint at once, even in the air).",
     "evidence": "Code order: knockback, look, click, movement keys, physics. Bots' crit attempts so far: 45 of 51 air "
                 "swings were still sprinting."},
    {"name": "Sprint reset (W-tap / S-tap)", "status": "confirmed",
     "what": "A sprint hit ends your sprint. Re-sprint (release forward or tap back for one decision, then forward + "
             "sprint) before the next swing for another sprint hit. S-tap also slows you, keeping spacing.",
     "evidence": "Code; bots already land ~90% of their hits as sprint hits."},
    {"name": "Knockback by hit type", "status": "confirmed",
     "what": "A sprint hit pushes the victim about 1.9 blocks; a crit or normal hit about 1.1. A crit keeps the opponent "
             "in reach to hit back; a sprint hit buys space for the next hit (combos).",
     "evidence": "Median pushback after 8 ticks, ~10,000 hits on grounded victims."},
    {"name": "Hit-select (counter-hit)", "status": "confirmed",
     "what": "Land your own sprint hit in the same moment you are hit: your knockback is cut (the attacker's velocity is "
             "scaled by 0.6 on a sprint hit, and knockback is applied before the click in the same tick).",
     "evidence": "Pushed back 1.47 blocks when hitting back in the same step vs 2.03 without (n=2,536 / 7,337)."},
    {"name": "P-crit", "status": "confirmed",
     "what": "Being hit on the ground launches you upward. Falling from that, with full charge and not sprinting, your "
             "counter-hit is a crit without pressing jump. Typical after you take a crit (their hit gave little knockback).",
     "evidence": "52 of the bots' 199 crits and 9 of the owner's 412 were landed this way."},
    {"name": "Jump reset", "status": "not shown",
     "what": "Guides: jump at the moment you are hit to take less horizontal knockback.",
     "evidence": "Bots that held jump when hit while sprinting in were pushed 1.75 blocks vs 1.64 without (n=181 / 6,072); "
                 "jumping while not running in made it worse (3.03 vs 2.41). Likely needs jump held in the exact tick "
                 "the hit lands. Test only as a timed experiment."},
    {"name": "Sprint-crit state", "status": "wrong here",
     "what": "Guides: keep holding sprint after a sprint hit and you can crit while sprinting.",
     "evidence": "A client/server desync for human players. Bots run on the server and re-sprint for real, so it does "
                 "not exist for bots. The owner landed 21 of 412 crits while their client still reported a sprint."},
    {"name": "Bait (draw them in)", "status": "untested",
     "what": "Not waiting: an active retreat that makes the opponent push. Hold back (S) and drift away at about 3.5-5 "
             "blocks with full charge, so they chase. You choose when the gap closes: when they commit forward and come "
             "near reach, stop retreating and turn it into your attack as they arrive, ideally a jump timed so your fall "
             "lands on them (crit bait), or a full-charge hit at the edge as they step in. Holding still at a distance "
             "is not a bait; the retreat is what draws them. When THEY back off with S and you are well outside reach, "
             "don't chase: stop, or alternate forward and back (W/S) to bait them in turn. Judge it by net damage over "
             "the fight (net_damage_per_fight), not by who wins one moment.",
     "evidence": "The owner's note (after round 14): the bots wait but never bait. Owner's own version: hold S, back "
                 "off, crit when the opponent gets near."},
    {"name": "Momentum and the 200 ms gap", "status": "confirmed",
     "what": "On the ground your own reversal is quick: switching from sprinting forward to back stops you within ~2 "
             "ticks. What you misjudge is THEIR momentum: you see them 4 ticks late, and a sprinting opponent closes "
             "~0.26 blocks/tick, so they are about a block nearer than op shows. Together with your 2-tick reversal, a "
             "back-off must start when they look ~4+ blocks away, not 3. A jump rises ~6 ticks; at a combined ~0.5 "
             "blocks/tick you cover ~3 blocks while rising, so a crit jump must take off around 3.5-4.5 blocks (by "
             "extrapolated distance), or they sprint-hit you while you rise. In the air you can't change course "
             "(little air control, and knockback carries further: air drag 0.91/tick vs ground ~0.55), so a jump commits you.",
     "evidence": "Duels: W-to-S reversal ~2 ticks for owner and bots. Bots' crit jumps: a quarter take off inside ~2.5 "
                 "blocks; they got hit while rising on 37-57% of jumps and crit on 5-10% (owner: 15-20% hit, 41-50% crit). "
                 "Bots' back-offs after their own swing start at a median 2.7-3.1 blocks, 49-62% inside 3. See "
                 "jump_punished_share."},
    {"name": "Read and answer (the owner's decision table)", "status": "untested",
     "what": "Choose the answer from what the opponent is doing right now: (1) they come near -> jump for a crit "
             "(timed so you fall as they arrive); (2) they come near, then back out -> hold forward and sprint-hit them "
             "as they retreat; (3) they push in hard -> let them come: back off with S and step to the side, then "
             "sprint-hit them as they arrive (they overrun); (4) they back off and you are well out of reach -> don't "
             "chase, stop or alternate W/S. Needs their movement read from op velocity (200 ms old) plus distance.",
     "evidence": "The owner's notes (after round 14). Each branch can be checked with net_damage_per_fight, "
                 "rush_share and first_hit_share."},
    {"name": "Crit chain (crit spam)", "status": "untested",
     "what": "Chain jump crits at close range (inside ~2.5 blocks): jump again as you land, sprint off, swing on each "
             "fall at full charge. One jump arc (~12 ticks) is about one recharge, so a crit fits every jump. Crits push "
             "only ~1.1 blocks, so they stay in reach for the next one. Risk: each jump is telegraphed; a hit while "
             "you are rising breaks the chain.",
     "evidence": "Owner: 140 runs of 2+ crits in a row, 40 of 3+, longest 11, one every 12 ticks. All bot versions: "
                 "one run of 2, ever. See crit_chain_share."},
    {"name": "Crit deflection", "status": "untested",
     "what": "Against a jumping opponent, don't swing early. Sprint-hit them the moment they land (their crit chance is "
             "gone and your sprint hit has full knockback), then pull back slightly to start a combo.",
     "evidence": "Guide. A direct counter to the owner's jump-crit openers."},
    {"name": "Crit spacing", "status": "untested",
     "what": "Jump too close and they sprint in and hit you early in your jump; jump too far and they back out. "
             "The owner's jump-ins start at about 2.7-2.9 blocks.",
     "evidence": "Guide, plus the owner's measured jump distance."},
    {"name": "S-hold crit", "status": "untested",
     "what": "Hold back late in your own crit jump so you drift out of their counter (P-crit or crit deflection).",
     "evidence": "Guide."},
    {"name": "No-knockback crit follow-up", "status": "untested",
     "what": "Right after landing a crit, hold forward into them before you touch the ground, so the small crit "
             "knockback doesn't give them a free reset.",
     "evidence": "Guide."},
    {"name": "Double crit", "status": "untested",
     "what": "Jump crit, take their counter-hit, then P-crit off its knockback.",
     "evidence": "Guide. Combines two confirmed mechanics."},
    {"name": "Uppercut (hop hit)", "status": "confirmed",
     "what": "When your sprint hit can knock them out of reach, hit and hop forward at the same moment (a small jump is "
             "enough, as the owner does): a sprint hit thrown while rising. It pushes them out of reach and you "
             "follow, so they rarely get a hit back. Use it to start or extend a combo; it is not a crit (rising), so "
             "keep sprint ON for it, unlike a crit.",
     "evidence": "Duels: after a hop hit the victim hit back within 12 ticks 7% (owner's) / 10% (bots') of the time, "
                 "vs 36% / 31% after a grounded hit; 4.7 vs 3.3-4.0 blocks apart 8 ticks later. Bots land many hop "
                 "hits, mostly from failed crit jumps rather than by choice."},
    {"name": "Outspacing", "status": "untested",
     "what": "Hit at the very edge of the 3-block reach so their counter can't reach you.",
     "evidence": "Guide. Several bots already gate clicks near 2.85-2.95 blocks."},
    {"name": "Circle strafe while comboing", "status": "untested",
     "what": "Hold left or right while comboing so they struggle to aim back.",
     "evidence": "Guide. The owner strafes about 23% of the time in reach."},
]


def briefing_lines():
    lines = ["## Technique library (from PvP guides, each checked against this arena)",
             "Statuses: confirmed (code or measured here), not shown (measured, no effect at bot timing), wrong here "
             "(not true for bots in this arena), untested (an idea: test it with a prediction like any other).", ""]
    for t in LIBRARY:
        lines.append(f"- **{t['name']}** [{t['status']}]: {t['what']} Evidence: {t['evidence']}")
    return lines
