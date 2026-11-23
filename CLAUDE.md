# Hoopframe

Synthetic 5-on-5 and one-on-one basketball with referees and configurable cameras, rendered in the browser. It
produces ground truth for multi-view tracking (live over MQTT, or recorded as MP4 per camera plus per-frame labels).
Everything is in `index.html`; Three.js, mqtt.js and mp4-muxer load from jsDelivr at pinned versions. There is no
build step. The README documents every feature and is kept current with them.

gym-mv3dt uses this repo as a submodule (`sim/hoopframe`) for simulated games, detector training data and layout
studies, pinned at a fixed commit.

This file applies to every session, including cloud sessions that don't see the owner's personal settings, so the
working rules are stated here in full.

## Understand first, then propose
1. **Understand the problem.** Find out what actually happens, from the data, the code and the results, before
   suggesting any change. Measure where the losses are and what causes them.
2. **Verify the understanding.** Check that it holds: test the explanation against cases it should predict, look for
   evidence that contradicts it, and say what is still uncertain. If a measurement surprises you, find out why before
   building on it.
3. **Only then propose solutions.** Aim them at the verified cause, say how much they could gain at most, and
   recommend one. If the ceiling is low, say so and suggest stopping.

## Decisions and approvals
- When in doubt, don't assume: propose an approach and ask for approval before going ahead.
- Work in phases, with approval between them.
- Commit, push, open PRs and merge only when asked. Ask before deleting anything that can't be recreated.

## Git
- One branch per task, named for what it does in kebab-case (for example `commentary-runs`, `one-on-one-officials`);
  never an automatic name such as `claude/<random words>`. Open a PR into `main`, merge it, then delete the branch.
  Don't push to a branch after its PR is merged: start a new one.
- This repository is public: everything pushed is visible to anyone. No real footage or images of real people, no
  venue names, and nothing from gym-mv3dt's private data.

## Reproducibility (gym-mv3dt depends on it)
- The same seed must give the same game, event for event. Game logic draws only from the seeded `rng`.
- Anything else that needs randomness (cosmetics, commentary, audio, noise) uses its own `mulberry32` generator,
  never `rng`, so adding it doesn't change the games.
- If a change does alter the game for a given seed, say so in the PR and the README, so gym-mv3dt can decide
  whether to move its pin.

## Checking a change
- There is no test suite. Check changes in Chrome or Edge, and through the test hooks (`window.hoopframe.advance`,
  `advanceTo`, `snapshot`, `events`, `commentary`, ...), which gym-mv3dt drives in headless Chrome.
- For game logic, compare `snapshot()` and `events()` for a fixed seed before and after the change.

## File header
`index.html` (and any new code file) starts with the SPDX lines; no author, date or version lines.

```html
<!doctype html>
<!-- SPDX-FileCopyrightText: 2026 Ethos Engineering LLC
     SPDX-License-Identifier: Apache-2.0 -->
```

## Results and writing
- Report results as they are, including failed attempts and what was skipped.
- Use plain names in messages, files and the README; no cryptic abbreviations.
