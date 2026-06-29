# 004 - Optional Navigation Nodes in reishift

## Problem

The `reishift` system navigates to quests by walking each prefix of a quest tuple.
For a quest like `(1, 0, 0, 0)`, it calls:

1. `place[(1,)]()` — scroll to find Part 1 header (`1.png`)
2. `place[(1,0)]()` — scroll to find 冬木 entrance (`1-0.png`)
3. `place[(1,0,0)]()` — navigate on Map to quest node at coordinates

However, **step 1 can fail** depending on account progress:

- **Chapters incomplete**: Each chapter (冬木, 奥尔良, etc.) is listed individually
  in the story quest screen. The Part 1 group header (`1.png`) does **not** appear.
- **All chapters completed**: Chapters 1-0 through 1-7 collapse under a single
  "Part 1" banner (`1.png`). Tapping it expands the chapter list.

Currently `List.__call__()` scrolls indefinitely until it finds its template image.
If the image doesn't exist on screen (because the grouping state doesn't match what
the code expects), the script hangs in an infinite loop.

This same pattern likely applies to other parts (Part 1.5, Part 2, etc.) as players
progress through the story.

## Current Architecture

```
quest tuple: (part, chapter, quest_node, sub_quest)
              ↓        ↓         ↓
         place[(1,)]  place[(1,0)]  place[(1,0,0)]
           List       List          Map
```

`reishift(quest)` iterates `range(1, len(quest))` and calls each prefix:

```python
def reishift(quest):
    for i in range(1, len(quest)):
        place.get(quest[:i], lambda: None)()
```

The `List` class scrolls through the quest list UI looking for a template match:

```python
class List:
    def __call__(self):
        while not isMainInterface(): pass
        while not isQuestListBegin(): swipe(down_to_up)  # scroll to top
        while not findChapter(self.name): swipe(up_to_down)  # scroll to find
```

## Design Goals

1. **Never hang** — if a navigation node's target isn't visible, detect and skip it
2. **Account-state aware** — handle both "chapters grouped" and "chapters ungrouped"
3. **Minimal invasiveness** — avoid restructuring the entire navigation system
4. **Forward compatible** — new chapters/parts should work without code changes

## Proposed Solutions

### Option A: Scroll-with-timeout + Skip (Recommended)

Add a `max_scrolls` limit to `List.__call__()`. If the template isn't found within
N scroll attempts, treat the node as absent and proceed to the next step.

```python
class List:
    def __init__(self, name, optional=False, max_scrolls=20):
        self.name = name
        self.optional = optional
        self.max_scrolls = max_scrolls

    def __call__(self):
        while not Detect(0, 1).isMainInterface(): pass
        while not Detect(.4).isQuestListBegin():
            fgoDevice.device.swipe((1000, 200), (1000, 600))

        scrolls = 0
        while not (p := Detect(.4).findChapter(self.name)):
            fgoDevice.device.swipe((1000, 600), (1000, 200))
            scrolls += 1
            if scrolls >= self.max_scrolls:
                if self.optional:
                    logger.info(f'Optional node {self.name} not found, skipping')
                    return
                else:
                    raise RuntimeError(f'Navigation node {self.name} not found after {scrolls} scrolls')
        fgoDevice.device.touch(p)
```

Mark part-level nodes as optional:

```python
place = {i.name: i for i in (
    List((0,), optional=True),
    List((1,), optional=True),  # Only visible when all 1-x chapters completed
    List((2,), optional=True),
    ...
)}
```

**Pros:**
- Simple, minimal change
- Naturally handles both grouped/ungrouped states
- Timeout prevents infinite loops even for unexpected UI states

**Cons:**
- Wastes time scrolling through entire list before giving up (20 scrolls ≈ 20s)
- Doesn't actively detect which state the account is in

### Option B: Detect-then-decide (Smart Skip)

Before scrolling, take a screenshot and detect whether the screen shows grouped
or ungrouped chapters. Skip the part-level `List` node if chapters are ungrouped.

```python
class List:
    def __init__(self, name, skip_if=None):
        self.name = name
        self.skip_if = skip_if  # callable returning True to skip

    def __call__(self):
        while not Detect(0, 1).isMainInterface(): pass
        if self.skip_if and self.skip_if():
            logger.info(f'Skipping {self.name} (condition met)')
            return
        # ... normal scroll logic with timeout ...
```

Detection could check for direct chapter visibility:

```python
def chapters_ungrouped():
    """True if individual chapters visible (not grouped under part header)."""
    return Detect(.4).findChapter((1, 0)) is not None
```

**Pros:**
- Instant skip, no wasted scroll time
- More deterministic behavior

**Cons:**
- Requires additional detection logic per-part
- Fragile if UI layout changes
- More complex to maintain

### Option C: Two-phase navigation with fallback

Try the full navigation chain. If a step fails (timeout), backtrack and retry
without that step.

```python
def reishift(quest):
    for i in range(1, len(quest)):
        node = place.get(quest[:i])
        if node is None:
            continue
        try:
            node()
        except NavigationTimeout:
            if node.optional:
                logger.info(f'Skipping optional node {quest[:i]}')
                continue
            raise
```

**Pros:**
- Clean separation of concerns
- Works with any navigation class (List, Map, etc.)

**Cons:**
- Requires exception-based flow
- Backtracking after timeout loses time

## Recommendation

**Option A** (scroll-with-timeout + skip) for immediate fix, with the following
implementation plan:

### Implementation Steps

1. **Add `optional` and `max_scrolls` parameters to `List.__init__()`**
   - Default `optional=False`, `max_scrolls=20`
   - Part-level entries `(0,)`, `(1,)`, `(2,)`, `(4,)`, `(5,)` → `optional=True`
   - Chapter-level entries remain `optional=False` (they should always be visible
     once the part-level navigation succeeds or is skipped)

2. **Add scroll counter and timeout logic to `List.__call__()`**
   - Count scrolls in the "find chapter" loop
   - If `max_scrolls` exceeded and `optional=True` → return silently
   - If `max_scrolls` exceeded and `optional=False` → raise error (prevents
     infinite hang, surfaces real bugs)

3. **Add logging** for skip events so user can see what happened

4. **Handle the "already on correct screen" case**
   - If part-level is skipped but chapter is directly visible (ungrouped mode),
     `List((1,0))` should still work — it scrolls from top and finds `1-0.png`
   - Verify this works: after skipping `place[(1,)]`, does the UI state still allow
     `place[(1,0)]` to find 冬木?

5. **Test matrix:**

   | Account State | Quest | Expected Behavior |
   |---|---|---|
   | Part 1 incomplete | `(1,0,0,0)` | Skip `(1,)`, find `(1,0)` directly, then Map |
   | Part 1 complete | `(1,0,0,0)` | Find `(1,)` group header, expand, find `(1,0)`, then Map |
   | Part 2 incomplete | `(3,0,0,0)` | Skip `(3,)` if ungrouped, find `(3,0)` directly |
   | Daily quests | `(0,0,0)` | Skip `(0,)` if ungrouped, find `(0,0)` |

## Edge Cases

- **Scroll direction**: `List.__call__()` always scrolls to top first, then down.
  This ensures consistent starting position regardless of previous navigation state.
- **Multiple parts visible**: If both grouped and ungrouped parts coexist on screen,
  the scroll-from-top approach still works since it searches sequentially.
- **Network lag**: Template matching may fail on loading screens. The existing
  `Detect` timeout (0.4s) provides some protection, but the scroll counter adds
  a hard upper bound.

## Future Considerations

- Option B (smart detection) could be layered on top of Option A later for
  performance — skip scrolling entirely if we can detect the state upfront.
- Consider caching account progress state so subsequent navigations in the same
  session don't re-scroll unnecessarily.
- The `Map` and `Mictlan` classes might benefit from similar timeout protection,
  though their failure modes are different (coordinate-based, not scroll-based).
